"""Gateway yazilim guncellemesi — istek / durum / sonuc / denetim katmani.

NE YAPAR, NE YAPMAZ
-------------------
Bu modul IKINCI BIR OTA CERCEVESI DEGILDIR. Guncellemeyi fiilen yapan
mekanizma zaten var ve degismiyor:

    backend  ->  request.json  ->  e1-gwd (host, root)  ->  docker compose

Burasi yalnizca dort seyi ekler: hedefi SEC (ve sabitle), istegi baslat,
sonucu izle, denetime yaz. Ajanin aksiyon kumesine yeni bir aksiyon
EKLENMEDI — geri alma bile `update` aksiyonudur; tek fark gonderilen imaj
referansidir.

HEDEF DIGEST'E SABITLENIR — VE BU CHECKSUM'IN TA KENDISIDIR
-----------------------------------------------------------
Hazirlik adiminda etiket (`:1.13.0`) kayit defterinde cozulur ve manifest
digest'i alinir; ajana giden referans `repo:tag@sha256:...` olur.

Bunun iki sonucu var:

1. Etiketin apply anina kadar baska bir imaja kaymis olmasi IMKANSIZ hale
   gelir (operatorun onayladigi sey ile kurulan sey ayni).
2. Ayri bir SHA256 dogrulamasi YAZMIYORUZ: digest'e sabitlenmis bir
   referansta indirilen manifest tutmuyorsa `docker pull` ZATEN reddeder.
   Kendi dogrulayicimizi yazmak, container runtime'in yaptigi isi ikinci
   kez ve daha kotu yapmak olurdu (GU-05 bu sekilde kapaniyor).

GERI ALMA SESSIZCE `latest`E DUSMEZ
-----------------------------------
Hedef yalnizca `from_image`dir — yani BU SISTEMIN gercekten calistirdigi
imaj. Bilinmiyorsa 409 doner. "Bilmiyorum, en gunceli kurayim" demek, geri
alma isteyen operatore tam TERSINI yapmak olurdu.
"""

from __future__ import annotations

import logging
import re
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.gateway_update import GatewayUpdate
from app.schemas.gateway_agent import LocalGateway
from app.services import gateway_agent_service, gateway_release_service
from app.services.gateway_agent_service import GatewayAgentError

logger = logging.getLogger(__name__)

#: Ajana istek yazilmis ama sonuclanmamis durumlar. Yeni bir istek kabul
#: edilmez (GU-07): ust uste guncelleme, hangi hedefin gecerli oldugunu
#: belirsizlestirir.
BUSY_STATUSES = frozenset({"requested", "running"})

#: Gelistirme etiketleri — release-image.yml bunlari main push'unda uretir
#: (`:main`, `:sha-<short>`). URETIM HEDEFI DEGILDIR: `:latest` yalnizca bir
#: surum tag'iyle (v*.*.*) olusur, yani "kararli" olan odur.
#: Bu ayrim GU-17'nin ta kendisi: henuz yayinlanmamis bir dal imajini
#: "guncelleme mevcut" diye sunmak, operatoru test edilmemis bir imaja
#: yonlendirirdi.
def is_development_tag(tag: str | None) -> bool:
    t = (tag or "").strip().lower()
    if not t:
        return False
    return t == "main" or t.startswith("sha-")


#: Kabul edilen imaj referansi bicimi — e1-gwd'deki `IMAGE_RE` ile AYNI
#: kurallar (registry[:port]/yol/parca[:tag][@sha256:...]).
#:
#: NEDEN BURADA DA DOGRULANIYOR: ajan zaten reddediyor, ama o red istek
#: DISKE YAZILDIKTAN ve asenkron islendikten SONRA olur — operator "kabul
#: edildi" yanitini alir, hata baska bir ekranda belirir. Erken dogrulama
#: hatayi istegi yapana, o anda gosterir. Kural KOPYALANMADI, ayni bicim
#: yeniden ifade edildi; ajan yine de son sozu soyler.
_IMAGE_REF_RE = re.compile(
    r"^[a-z0-9]([a-z0-9._\-]*[a-z0-9])?"
    r"(:[0-9]+)?"
    r"(/[a-z0-9]([a-z0-9._\-]*[a-z0-9])?)*"
    r"(:[A-Za-z0-9._\-]{1,128})?"
    r"(@sha256:[a-f0-9]{64})?$"
)


class GatewayUpdateError(Exception):
    """Istek kabul edilemez. `code` HTTP katmaninda duruma cevrilir."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


def _now() -> datetime:
    return datetime.now(timezone.utc)


def get_row(db: Session, gateway_code: str) -> GatewayUpdate | None:
    return db.get(GatewayUpdate, gateway_code)


def _row_or_new(db: Session, gateway_code: str) -> GatewayUpdate:
    row = db.get(GatewayUpdate, gateway_code)
    if row is None:
        row = GatewayUpdate(gateway_code=gateway_code, status="idle")
        db.add(row)
        db.flush()
    return row


def _local(gateway_code: str) -> LocalGateway | None:
    """Ajanin bildirdigi kurulu gateway kaydi (yoksa None)."""
    durum = gateway_agent_service.read_status()
    for gw in durum.gateways:
        if gw.code == gateway_code:
            return gw
    return None


def _current_image(local: LocalGateway | None) -> str | None:
    """Su an CALISAN imajin referansi — mumkunse digest'e sabitlenmis.

    `tracked_image` operatorun IZLEDIGI etikettir; `image_digest` ise o an
    calisan imajin gercek digest'i. Geri alma hedefi olarak etiket YETMEZ:
    etiket yarin baska bir imaja kayabilir ve "geri aldim" denilen sey
    bambaska bir surum olur. Ikisi birlestirilir.
    """
    if local is None:
        return None
    ref = (local.tracked_image or local.image or "").strip()
    if not ref:
        return None
    taban = ref.split("@", 1)[0]
    digest = (local.image_digest or "").strip()
    return f"{taban}@{digest}" if digest.startswith("sha256:") else taban


# ---------------------------------------------------------------------------
# HAZIRLIK
# ---------------------------------------------------------------------------
def prepare(
    db: Session,
    gateway_code: str,
    actor_username: str,
    *,
    target_image: str | None = None,
) -> GatewayUpdate:
    """Hedefi coz, digest'e sabitle, kaydet. Ajana HENUZ dokunmaz.

    Ag erisimi BU ADIMDA olur — guncelleme aninda degil. Boylece operator
    "ne kurulacak" sorusunun cevabini onaylamadan once gorur.
    """
    local = _local(gateway_code)
    if local is None:
        raise GatewayUpdateError(
            "not_installed_locally",
            "Gateway bu cihazda kurulu degil; buradan guncellenemez.",
        )

    row = _row_or_new(db, gateway_code)
    if row.status in BUSY_STATUSES:
        raise GatewayUpdateError(
            "update_in_progress",
            "Bu gateway icin devam eden bir guncelleme var; bitmesini bekleyin.",
        )

    hedef_ref = (target_image or "").strip() or (local.tracked_image or "").strip()
    if not hedef_ref:
        raise GatewayUpdateError(
            "target_unknown",
            "Hedef imaj belirlenemedi: gateway hangi imaji izledigini bildirmedi.",
        )

    if not _IMAGE_REF_RE.match(hedef_ref):
        raise GatewayUpdateError(
            "target_invalid",
            f"Gecersiz imaj referansi: {hedef_ref}. Beklenen bicim "
            "`[registry[:port]/]yol[:etiket][@sha256:...]`.",
        )
    parcalar = gateway_release_service.parse_image_ref(hedef_ref)
    if parcalar is None:
        raise GatewayUpdateError("target_invalid", f"Gecersiz imaj referansi: {hedef_ref}")
    _, _, tag = parcalar

    # Kayit defterine SOR — bloklayan cagri. Hazirlik operatorun bilincli bir
    # eylemi; burada beklemek dogru. (Liste ekranindaki pasif gosterim
    # bloklamayan `lookup`u kullanir.)
    uzak = gateway_release_service.fetch(hedef_ref.split("@", 1)[0])
    if uzak.error or not uzak.digest:
        # FAIL-CLOSED: digest cozulemeden hedef sabitlenemez. "Sorun degil,
        # etiketle gonderelim" demek, onaylanan ile kurulanin ayrismasina
        # kapi acmakti (GU-13).
        raise GatewayUpdateError(
            "registry_unreachable",
            "Hedef surum kayit defterinden dogrulanamadi; guncelleme "
            f"baslatilmadi. Sebep: {uzak.error or 'digest okunamadi'}",
        )

    if is_development_tag(tag):
        raise GatewayUpdateError(
            "target_not_released",
            f"'{tag}' bir gelistirme etiketi; uretim guncelleme hedefi "
            "olamaz. Yayinlanmis bir surum etiketi secin.",
        )

    mevcut_digest = (local.image_digest or "").strip()
    if mevcut_digest and mevcut_digest == uzak.digest:
        raise GatewayUpdateError(
            "already_current",
            f"Gateway zaten bu surumu calistiriyor ({local.local_version or uzak.version or tag}).",
        )

    taban = hedef_ref.split("@", 1)[0]
    row.from_version = local.local_version or None
    row.from_image = _current_image(local)
    row.to_version = uzak.version or tag
    row.to_image = f"{taban}@{uzak.digest}"
    row.expected_digest = uzak.digest
    row.status = "preparing"
    row.started_by = actor_username
    row.started_at = None
    row.finished_at = None
    row.error = None
    row.request_id = None
    row.is_rollback = False
    db.flush()
    return row


# ---------------------------------------------------------------------------
# UYGULA
# ---------------------------------------------------------------------------
def apply(db: Session, gateway_code: str, actor_username: str) -> GatewayUpdate:
    """Hazirlanmis hedefi ajana yolla."""
    row = db.get(GatewayUpdate, gateway_code)
    if row is None or row.status != "preparing" or not row.to_image:
        raise GatewayUpdateError(
            "not_prepared",
            "Once hazirlik adimini calistirin (hedef surum secilmedi).",
        )
    return _dispatch(db, row, actor_username, image=row.to_image, rollback=False)


# ---------------------------------------------------------------------------
# GERI AL
# ---------------------------------------------------------------------------
def rollback(db: Session, gateway_code: str, actor_username: str) -> GatewayUpdate:
    """Onceki imaja don. Hedef YALNIZCA `from_image`dir."""
    row = db.get(GatewayUpdate, gateway_code)
    if row is None or not row.from_image:
        raise GatewayUpdateError(
            "no_rollback_target",
            "Geri alinacak onceki surum bilinmiyor. Bu sistem uzerinden "
            "yapilmis bir guncelleme kaydi yok; sessizce en guncele donmek "
            "geri alma OLMAZDI.",
        )
    if row.status in BUSY_STATUSES:
        raise GatewayUpdateError(
            "update_in_progress",
            "Devam eden bir islem var; bitmesini bekleyin.",
        )

    hedef = row.from_image
    # Yon degisiyor: simdiki calisan surum, geri almanin "from"u olur ve
    # geri aldigimiz surum "to". Aksi halde ikinci bir geri alma ayni yere
    # donmeye calisir ve operator dongude kalirdi.
    local = _local(gateway_code)
    row.from_image, row.to_image = _current_image(local) or row.to_image, hedef
    row.from_version, row.to_version = (
        (local.local_version if local else row.to_version),
        row.from_version,
    )
    row.expected_digest = hedef.split("@", 1)[1] if "@" in hedef else None
    db.flush()
    return _dispatch(db, row, actor_username, image=hedef, rollback=True)


def _dispatch(
    db: Session, row: GatewayUpdate, actor_username: str, *, image: str, rollback: bool
) -> GatewayUpdate:
    try:
        request_id = gateway_agent_service.request_update(
            row.gateway_code, actor_username, image=image
        )
    except GatewayAgentError as exc:
        # `request_pending` dahil: ajan kuyrugunda islenmemis bir istek varsa
        # ustune yazmiyoruz (GU-07 ikinci kapi).
        raise GatewayUpdateError("agent_" + str(exc).split(":")[0], str(exc)) from exc

    row.status = "requested"
    row.request_id = request_id
    row.started_by = actor_username
    row.started_at = _now()
    row.finished_at = None
    row.error = None
    row.is_rollback = rollback
    db.flush()
    return row


# ---------------------------------------------------------------------------
# SONUCU IZLE
# ---------------------------------------------------------------------------
def reconcile(db: Session, gateway_code: str) -> GatewayUpdate | None:
    """Ajanin bildirdigi son sonucu duruma yansit.

    NEDEN AYRI BIR ISCI YOK: guncelleme saniyeler suren, operatorun ekrana
    BAKARAK yaptigi bir islem ve arayuz zaten ajan durumunu saniyede bir
    yokluyor. Okuma aninda uzlastirmak, arka planda surekli donen bir
    dongu acmaktan hem ucuz hem de dogrudur — kimse bakmiyorsa uzlastirmaya
    gerek de yoktur.
    """
    row = db.get(GatewayUpdate, gateway_code)
    if row is None or row.status not in BUSY_STATUSES:
        return row

    durum = gateway_agent_service.read_status()
    son = durum.last_apply
    if son is None or getattr(son, "id", None) != row.request_id:
        # Ajan bizim istegimizi henuz gormedi ya da baska bir istegi
        # isliyor. Bilmedigimiz bir seyi "basarili" yazmayiz.
        return row

    if getattr(son, "running", False):
        row.status = "running"
        db.flush()
        return row

    ok = getattr(son, "ok", None)
    if ok is None:
        return row

    row.finished_at = _now()
    if ok:
        row.status = "rolled_back" if row.is_rollback else "succeeded"
        row.error = None
    else:
        row.status = "failed"
        parcalar = [
            str(getattr(son, "stage", "") or ""),
            str(getattr(son, "message", "") or ""),
            str(getattr(son, "detail", "") or ""),
        ]
        row.error = " | ".join(p for p in parcalar if p)[:2000] or "bilinmeyen hata"
    db.flush()
    return row


# ---------------------------------------------------------------------------
# GORUNUM — liste ve detay AYNI kaynaktan
# ---------------------------------------------------------------------------
def smart_device_counts(db: Session) -> dict[str, int]:
    """Gateway basina `session_policy=smart` cihaz sayisi.

    Tek sorgu: JSON alani icinde filtrelemek yerine satirlari okuyup Python'da
    sayiyoruz. Sebep, tasinabilirlik degil DOGRULUK: diskteki sozlukte alan
    hic olmayabilir ya da `null` yazilmis olabilir ve "yok" ile "continuous"
    ayni anlama gelir — bu normallestirmeyi zaten `merge_dnp3_extended`
    yapiyor ve tek kaynak olarak kalmali (bkz. B5).
    """
    from app.models.device import Device
    from app.schemas.dnp3_extended import merge_dnp3_extended

    sayim: dict[str, int] = {}
    satirlar = db.execute(
        select(Device.gateway_code, Device.dnp3_extended).where(
            Device.gateway_code.is_not(None)
        )
    ).all()
    for kod, ext in satirlar:
        if merge_dnp3_extended(ext if isinstance(ext, dict) else None).session_policy == "smart":
            sayim[kod] = sayim.get(kod, 0) + 1
    return sayim


def _health_version(db: Session, gateway_code: str) -> str | None:
    """Gateway'in KENDI bildirdigi surum (heartbeat).

    Uzak (bu cihaza kurulu OLMAYAN) gateway'ler icin surumun TEK kaynagi
    budur; ajan onlari hic gormez.
    """
    from app.models.gateway_health import GatewayHealth

    row = db.get(GatewayHealth, gateway_code)
    return (getattr(row, "gateway_version", None) or None) if row else None


def build_state(
    db: Session,
    gateway_code: str,
    *,
    smart_counts: dict[str, int] | None = None,
    reconcile_first: bool = True,
):
    """Tek gateway icin birlesik guncelleme gorunumu."""
    from app.schemas.gateway_update import CompatibilityWarningOut, GatewayUpdateState
    from app.services import gateway_compatibility

    if reconcile_first:
        reconcile(db, gateway_code)
    row = db.get(GatewayUpdate, gateway_code)
    local = _local(gateway_code)

    # SURUM KAYNAGI ACIKCA BILDIRILIR. Ajan bu cihazdaki gerceklige en
    # yakin olandir; yoksa gateway'in kendi heartbeat'ine duseriz.
    surum = (local.local_version if local else None) or None
    kaynak = "agent" if surum else None
    if not surum:
        surum = _health_version(db, gateway_code)
        kaynak = "health" if surum else None

    takip = (local.tracked_image if local else None) or None
    kanal = None
    if takip:
        parcalar = gateway_release_service.parse_image_ref(takip)
        if parcalar:
            kanal = "development" if is_development_tag(parcalar[2]) else "stable"

    mevcut_uzak = local.remote_version if local else None
    guncelleme_var = local.update_available if local else None
    if kanal == "development":
        # GELISTIRME ETIKETI URETIM HEDEFI DEGIL (GU-17). Digest degismis
        # olabilir ama bu "yeni surum yayinlandi" demek DEGILDIR; oyle
        # sunmak operatoru test edilmemis bir imaja yonlendirirdi.
        guncelleme_var = None

    if smart_counts is None:
        smart_counts = smart_device_counts(db)
    uyarilar = []
    uyari = gateway_compatibility.smart_session_warning(
        surum, smart_counts.get(gateway_code, 0)
    )
    if uyari is not None:
        uyarilar.append(CompatibilityWarningOut(**uyari.__dict__))

    return GatewayUpdateState(
        gateway_code=gateway_code,
        current_version=surum,
        current_version_source=kaynak,
        available_version=mevcut_uzak or None,
        update_available=guncelleme_var,
        target_version=row.to_version if row else None,
        target_image=row.to_image if row else None,
        expected_digest=row.expected_digest if row else None,
        tracked_image=takip,
        channel=kanal,
        status=row.status if row else "idle",
        from_version=row.from_version if row else None,
        from_image=row.from_image if row else None,
        started_by=row.started_by if row else None,
        started_at=row.started_at if row else None,
        finished_at=row.finished_at if row else None,
        error=row.error if row else None,
        is_rollback=bool(row.is_rollback) if row else False,
        can_rollback=bool(row and row.from_image),
        installed_locally=local is not None,
        compatibility=uyarilar,
    )


def note_legacy_update(
    db: Session, gateway_code: str, actor_username: str, *, request_id: str
) -> GatewayUpdate:
    """Eski `local-update` ucundan gelen guncellemeyi duruma isle.

    O uc hedefi SECMEZ ("en guncel yayina gec" der), dolayisiyla
    `to_version`/`expected_digest` bos kalir. Uydurmuyoruz: arayuz bos hedefi
    "en guncel" olarak gosterir. Onemli olan, guncellemenin hangi yoldan
    yapildigindan bagimsiz olarak "son guncelleme" bilgisinin DOGRU olmasi.
    """
    row = _row_or_new(db, gateway_code)
    local = _local(gateway_code)
    row.from_version = (local.local_version if local else None) or None
    row.from_image = _current_image(local)
    row.to_version = None
    row.to_image = None
    row.expected_digest = None
    row.status = "requested"
    row.request_id = request_id
    row.started_by = actor_username
    row.started_at = _now()
    row.finished_at = None
    row.error = None
    row.is_rollback = False
    db.flush()
    return row
