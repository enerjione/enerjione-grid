"""Cihaz basina calisma-zamani sagligi — `device_health_v1` alimi.

SOZLESME PR #33'TE ACIK; DEGISIRSE YALNIZCA BURASI DEGISIR.
Vendor kopyasi: `docs/gateway-contract/device-health-api-pr33.md`
(kaynak: enerjione-grid-dnp3-gateway PR #33, commit bd502c49,
`docs/GRID_DEVICE_HEALTH_API.md`).

Wire <-> model eslemesi TEK bir adaptor fonksiyonundadir (`_wire_to_model`).
PR birlesirken sozlesme degisirse degistirilecek yer ORASIDIR; router,
migration ve testler alan adlarina dogrudan bagimli DEGILDIR. Ayni sebeple
zarf ayristirmasi da burada: pydantic sema dosyasina bolmek, sozlesmeyi iki
dosyaya yayip "degisirse tek nokta" garantisini bozardi.

BU MODULUN KORUDUGU UC SESSIZ HATA
----------------------------------
1. BAYAT YAZMA. Siralama `(boot_id, sequence)` ikilisinin LEKSIKOGRAFIK
   karsilastirmasidir. `gateway_instance_id` siralamaya GIRMEZ: o kimlik
   gateway diskinde KALICIDIR ve restart'ta AYNI kalir; yalnizca ona bakan
   bir backend, yeni calismanin `sequence=1` partisini "eski" sanip ATARDI.
   `boot_id` her acilista arttigi icin eski calismanin `sequence=9999`u
   yeni calismanin `sequence=1`inden KUCUKTUR.
   DUVAR SAATI KULLANILMAZ — sahada RTC'si bos acilan gateway'ler ve NTP
   sicramalari gercektir; saate bagli siralama tam da o anlarda tersine doner.

2. YARIM SNAPSHOT'LA SILME. Partiler `snapshot_id` ile eslesir. `device_total`
   TEK BASINA YETMEZ: yarim kalan eski snapshot ile yenisi ayni toplami
   tasir, ayirt edilemez ve "eksikleri sil" mantigi VAR OLAN CIHAZLARI
   SILER. Silme yalnizca `snapshot_batch_count` kadar parti geldikten SONRA.

3. SAGLIKLI UYKUYU ARIZA SAYMA. `smart_idle` offline DEGIL, `report_late`
   bir DURUM DEGIL bayraktir, sonda (`ip_probe_status`/`tcp_probe_status`)
   sonuclari SALT TESHISTIR. Bu modul hicbirini yorumlamaz, oldugu gibi
   saklar; `devices.communication_status` ve `telemetry_latest` alanlarina
   DOKUNMAZ (onlarin sahibi telemetri hattidir).

Ileri uyumluluk: BILINMEYEN ALANLAR YOK SAYILIR ve bilinen enum'lar
gelen degere ZORLANMAZ — PR acik, yeni durum/alan eklenebilir.
"""

from __future__ import annotations

import logging
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from fastapi import HTTPException, status
from sqlalchemy import delete, distinct, func, select
from sqlalchemy.orm import Session

from app.models.device_runtime_health import DeviceRuntimeHealth

logger = logging.getLogger(__name__)

#: Kabul edilen tek sema. Farkli deger gelirse REDDEDILIR (sozlesme bolum 3).
SCHEMA_ADI = "device_health_v1"

#: Sozlesme bolum 7: parti boyu `1..500`. Ustu, yanlis yapilandirilmis ya da
#: kotu niyetli bir istemcidir; tek istekte sinirsiz satir yazdirmayiz.
MAX_PARTI_CIHAZ = 500

#: Yalnizca BELGELENMIS alanlar okunur; gerisi sessizce dusulur.
#: (Adlarin tamami sozlesme bolum 4'tendir.)
_CIHAZ_ALANLARI_METIN = (
    "connection_state",
    "configured_session_policy",
    "effective_session_policy",
    "operation_mode",
    "ip_probe_status",
    "tcp_probe_status",
    "ip_endpoint_type",
    # 1.15.1 — SALT TESHIS. `connection_state`i ETKILEMEZ.
    "device_clock_status",
)
_CIHAZ_ALANLARI_BOOL = ("connected", "reachable", "report_late")

#: UC DURUMLU bool (1.15.1). `None` = "BILMIYORUZ" ve `False` ile AYNI SEY
#: DEGILDIR: `need_time_iin=False` "cihaz saat istemiyor" demektir, `None`
#: ise "hic IIN gorulmedi" (or. 1.15.0 gateway). Ayrim onemli — saati yanlis
#: olup saat ISTEMEYEN cihaz kendiliginden DUZELMEZ.
_CIHAZ_ALANLARI_BOOL_NULLABLE = ("need_time_iin",)

#: EPOCH alanlari. `null` = "HIC OLMADI"; gateway 0 GONDERMEZ ve biz de 0'a
#: cevirmeyiz (panelde 1970 tarihleri cikmasin diye).
_CIHAZ_ALANLARI_FLOAT = (
    "next_expected_report_epoch",
    "report_overdue_sec",
    "last_valid_contact_epoch",
    "last_frame_epoch",
    "last_probe_epoch",
    # 1.15.1
    "last_device_time_epoch",
    "session_started_epoch",
)

#: EPOCH OLMAYAN float (1.15.1). Ayri liste, cunku yukaridaki "0 gelmez"
#: gerekcesi burada GECERLI DEGIL: `device_clock_offset_sec = 0.0` tam
#: senkron demektir ve tamamen mesrudur.
_CIHAZ_ALANLARI_FLOAT_OLCU = ("device_clock_offset_sec",)


@dataclass(frozen=True)
class Zarf:
    """Ayristirilmis ve dogrulanmis istek zarfi (sozlesme bolum 3)."""

    gateway_code: str
    gateway_instance_id: str | None
    boot_id: int
    sequence: int
    snapshot: bool
    snapshot_id: str | None
    snapshot_batch_index: int | None
    snapshot_batch_count: int | None
    devices: list[dict[str, Any]]


# ---------------------------------------------------------------------------
# Tip zorlamasi — TEK BIR BOZUK ALAN PARTIYI DUSURMEZ
# ---------------------------------------------------------------------------
#
# Gerekce `gateway_health_service` ile ayni: kor noktayi kapatmak icin
# eklenen bir kanalin, tek bir hatali alan yuzunden 200 cihazin durumunu
# atmasi kabul edilemez. Cozulemeyen alan None kalir, kayit YAZILIR.


def _as_bool(deger: Any) -> bool | None:
    return deger if isinstance(deger, bool) else None


def _as_int(deger: Any) -> int | None:
    # bool bir int'tir (Python); `True -> 1` yazmak sessiz veri bozulmasi.
    if isinstance(deger, bool):
        return None
    if isinstance(deger, int):
        return deger
    if isinstance(deger, float):
        return int(deger)
    return None


def _as_float(deger: Any) -> float | None:
    if isinstance(deger, bool):
        return None
    if isinstance(deger, (int, float)):
        return float(deger)
    return None


def _as_str(deger: Any, uzunluk: int) -> str | None:
    if not isinstance(deger, str):
        return None
    kirpik = deger.strip()
    return kirpik[:uzunluk] or None


# ---------------------------------------------------------------------------
# ADAPTOR — WIRE'DAN MODELE TEK GECIS NOKTASI
# ---------------------------------------------------------------------------


def _wire_to_model(kayit: dict[str, Any]) -> tuple[str, dict[str, Any]] | None:
    """Sozlesmedeki bir cihaz kaydini ORM kolon sozluguna cevir.

    SOZLESME DEGISIRSE DEGISECEK TEK FONKSIYON BUDUR.

    `device_code` yoksa/bos ise `None` doner: o KAYIT atlanir, parti degil.
    Bilinmeyen ALANLAR okunmaz (ileri uyumluluk: alan eklemek geriye uyumlu).

    `connection_state` ISE SOZLESME KUMESINE ZORLANIR
    -------------------------------------------------
    Iki sebep:

    1. Bu alan arayuzde bir renge/etikete cevriliyor. Tanimadigimiz bir deger
       saklanirsa hicbir kovaya girmez ve ekranda cizilemez.
    2. Sunum katmaninin kovalari kanonik duruma SIZMAMALIDIR. Ornegin `late`
       KPI'da mesru bir kovadir ama bir `connection_state` DEGILDIR: gecikme
       `report_late` bayragiyla tasinir ve durum `smart_idle` KALIR. "late"
       durum olarak yazilsaydi, bayrak kalkinca hangi duruma donulecegi
       bilgisi kaybolurdu.

    Tanimadigimiz deger SESSIZCE YUTULMAZ: `unknown` yazilir ve degeri
    ADIYLA loglanir. Boylece gateway gercekten yeni bir durum eklerse bu
    log'da gorunur ve matris bilincli olarak genisletilir — sessiz kayip da
    olmaz, cizilemeyen deger de saklanmaz.
    """
    if not isinstance(kayit, dict):
        return None
    kod = _as_str(kayit.get("device_code"), 50)
    if not kod:
        return None

    alanlar: dict[str, Any] = {}
    for ad in _CIHAZ_ALANLARI_METIN:
        alanlar[ad] = _as_str(kayit.get(ad), 24)
    for ad in _CIHAZ_ALANLARI_BOOL:
        # NOT NULL kolonlar: eksik/bozuk gelirse False. "Bilinmiyor" ile
        # "hayir" arasindaki fark burada ONEMSIZ — uc alan da bayrak.
        alanlar[ad] = bool(_as_bool(kayit.get(ad)))
    for ad in _CIHAZ_ALANLARI_BOOL_NULLABLE:
        # `bool()` ILE SARMALANMAZ: `None` korunur. Sarmalamak "bilmiyoruz"u
        # "hayir" yapar ve 1.15.0 gateway'lerin tum filosu "saat istemiyor"
        # gibi gorunurdu.
        alanlar[ad] = _as_bool(kayit.get(ad))
    for ad in _CIHAZ_ALANLARI_FLOAT:
        # `null` = "HIC OLMADI". Gateway 0 GONDERMEZ ve biz de 0'a
        # cevirmeyiz: panelde 1970 tarihleri cikmasin diye.
        alanlar[ad] = _as_float(kayit.get(ad))
    for ad in _CIHAZ_ALANLARI_FLOAT_OLCU:
        # Olcu alani: `0.0` mesru bir deger, `None`'a cevrilmez.
        alanlar[ad] = _as_float(kayit.get(ad))

    alanlar["dial_in_interval_min"] = _as_int(kayit.get("dial_in_interval_min"))
    # `connection_state` NOT NULL; bildirilmemisse iddiada bulunmayiz.
    alanlar["connection_state"] = _baglanti_durumu(alanlar["connection_state"], kod)
    return kod, alanlar


#: Sozlesmedeki `connection_state` kumesi (bolum 4). `late` BILEREK YOK —
#: gecikme bir bayraktir, durum degil.
BAGLANTI_DURUMLARI: frozenset[str] = frozenset(
    {"online", "smart_idle", "recovering", "lost", "listener_error", "unknown"}
)


def _baglanti_durumu(ham: str | None, device_code: str) -> str:
    """Sozlesme kumesine zorla; tanimadigini `unknown` yap ve LOGLA."""
    if not ham:
        return "unknown"
    if ham in BAGLANTI_DURUMLARI:
        return ham
    logger.warning(
        "device_health bilinmeyen connection_state=%r device=%s — 'unknown' "
        "yazildi. Gateway yeni bir durum eklediyse BAGLANTI_DURUMLARI "
        "genisletilmeli; sunum kovasi (or. 'late') ise gateway hatasidir.",
        ham,
        device_code,
    )
    return "unknown"


# ---------------------------------------------------------------------------
# Zarf dogrulama
# ---------------------------------------------------------------------------


def _reddet(mesaj: str) -> HTTPException:
    return HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=mesaj)


def zarfi_coz(payload: Any, *, gateway_code: str) -> Zarf:
    """Istek govdesini dogrula. Sozlesme disi govde 400 ile reddedilir."""
    if not isinstance(payload, dict):
        raise _reddet("Govde bir JSON nesnesi olmali")

    sema = payload.get("schema")
    if sema != SCHEMA_ADI:
        # Sozlesme bolum 3: farkli sema REDDEDILIR. Sessizce kabul etmek,
        # tanimadigimiz bir semayi bu semaymis gibi ayristirmak olurdu.
        raise _reddet(f"Desteklenmeyen sema: {sema!r} (beklenen {SCHEMA_ADI!r})")

    govde_kodu = _as_str(payload.get("gateway_code"), 50)
    if govde_kodu is not None and govde_kodu != gateway_code:
        # Defans derinligi: yol/baslik/govde ucu de ayni gateway'i gostermeli.
        raise _reddet("gateway_code govde ile yol arasinda uyusmuyor")

    boot_id = _as_int(payload.get("boot_id"))
    sequence = _as_int(payload.get("sequence"))
    if boot_id is None or boot_id < 1 or sequence is None or sequence < 1:
        # Ikisi de siralamanin TEMELI; eksik/gecersizse bayat yazma
        # korumasi calismaz ve eski bir parti yenisini ezebilir.
        raise _reddet("boot_id ve sequence >= 1 tamsayi olmali")

    cihazlar = payload.get("devices")
    if not isinstance(cihazlar, list):
        raise _reddet("devices bir dizi olmali")
    if len(cihazlar) > MAX_PARTI_CIHAZ:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail=f"Tek partide en fazla {MAX_PARTI_CIHAZ} cihaz kabul edilir",
        )

    snapshot = bool(_as_bool(payload.get("snapshot")))
    return Zarf(
        gateway_code=gateway_code,
        gateway_instance_id=_as_str(payload.get("gateway_instance_id"), 80),
        boot_id=boot_id,
        sequence=sequence,
        snapshot=snapshot,
        # Snapshot korelasyon alanlari delta'da `null` gelir; snapshot'ta da
        # eksik olabilir (o zaman UZLASTIRMA YAPILMAZ, bkz. `_uzlastir`).
        snapshot_id=_as_str(payload.get("snapshot_id"), 64) if snapshot else None,
        snapshot_batch_index=_as_int(payload.get("snapshot_batch_index")) if snapshot else None,
        snapshot_batch_count=_as_int(payload.get("snapshot_batch_count")) if snapshot else None,
        devices=cihazlar,
    )


# ---------------------------------------------------------------------------
# OKUMA TARAFI — cihaz yanitina baglama
# ---------------------------------------------------------------------------


def saglik_haritasi(
    db: Session, kodlar: Iterable[str]
) -> dict[str, DeviceRuntimeHealth]:
    """Verilen cihaz kodlari icin saglik satirlari — `code -> satir`.

    TEK SORGU, CIHAZ BASINA DEGIL. Okuma yolu (`GET /devices`) 600+ cihaz
    donuyor; satir basina `db.get(...)` 600 gidis-donus demekti. Ayni gerekce
    alim tarafindaki toplu okumada da yazili.

    JOIN DEGIL, AYRI SELECT — bilincli ve GUVENLIK gerekcesi var:

    * Cihaz sorgusuna bir JOIN eklemek, sonuc kumesini ETKILEYEBILIR
      (`device_runtime_health`te birden fazla eslesme olsa satir cogalir,
      yanlis join turu olsa satir duserdi). O kume kapsam filtresinin
      (`scope_service`) ciktisi; genisletmesi de daraltmasi da yetki hatasi
      olurdu. Ayri select cihaz kumesine DOKUNAMAZ: sayfalama, toplam sayi,
      filtre, arama ve siralama neyse o kalir.
    * Kapsam disi bir cihazin sagligi hic OKUNMAZ, cunku sorgu yalnizca
      cagiranin ELINDEKI kodlari sorar. Sagliga AYRI bir yetki yolu
      acilmamistir; otorite cihaz kapsamidir.

    Cihaz basina EN FAZLA BIR satir olabilir: `device_code` bu tablonun
    BIRINCIL ANAHTARIDIR (bkz. `models/device_runtime_health.py`). Sozluk
    kurmak bu yuzden veri kaybetmez.
    """
    kod_kumesi = {k for k in kodlar if k}
    if not kod_kumesi:
        return {}
    return {
        satir.device_code: satir
        for satir in db.scalars(
            select(DeviceRuntimeHealth).where(
                DeviceRuntimeHealth.device_code.in_(kod_kumesi)
            )
        ).all()
    }


# ---------------------------------------------------------------------------
# Bayat yazma korumasi
# ---------------------------------------------------------------------------


def saklanan_kursor(db: Session, gateway_code: str) -> tuple[int, int] | None:
    """Bu gateway icin en son UYGULANAN `(boot_id, sequence)`.

    Kursor AYRI BIR TABLODA TUTULMUYOR: uygulanan her parti zaten cihaz
    satirlarina kendi `(boot_id, sequence)` degerini yaziyor, dolayisiyla
    gateway'in en yuksek ikilisi satirlardan okunur (index:
    `ix_device_runtime_health_kursor`, tek satir okur).

    BILINEN SINIR: gateway'in TUM cihazlari config'ten cikarsa satir kalmaz
    ve kursor sifirlanir; o an yolda olan eski bir parti yeniden
    uygulanabilir. Zararsiz kabul edildi — ortada ezilecek durum yok ve
    gateway yalnizca EN SON durumu tasiyan sinirli bir yeniden deneme
    penceresi tutar (sozlesme bolum 7). Kursor icin ayri bir tablo acmak,
    tek satirlik bir yazmayi her saglik partisine eklerdi.
    """
    satir = db.execute(
        select(DeviceRuntimeHealth.boot_id, DeviceRuntimeHealth.sequence)
        .where(DeviceRuntimeHealth.gateway_code == gateway_code)
        .order_by(DeviceRuntimeHealth.boot_id.desc(), DeviceRuntimeHealth.sequence.desc())
        .limit(1)
    ).first()
    if satir is None:
        return None
    return int(satir[0] or 0), int(satir[1] or 0)


def bayat_mi(gelen: tuple[int, int], saklanan: tuple[int, int] | None) -> bool:
    """`gelen <= saklanan` ise BAYAT. Karsilastirma leksikografik."""
    if saklanan is None:
        return False
    return gelen <= saklanan


# ---------------------------------------------------------------------------
# Uygulama
# ---------------------------------------------------------------------------


def _uzlastir(db: Session, zarf: Zarf) -> int:
    """Snapshot TAMAMLANDIYSA artik bildirilmeyen cihaz satirlarini sil.

    Gateway "cihaz silindi" mesaji GONDERMEZ; config'ten cikan cihaz sonraki
    snapshot'ta BULUNMAZ. Silme karari bu yoklugun uzerine kurulur — ve tam
    da bu yuzden YALNIZCA butun partiler geldikten sonra alinir.

    Tamamlanma olcusu, ayni `snapshot_id` icin GORULEN FARKLI PARTI SAYISIdir
    ve veritabanindan okunur. Sureç-ici bir sozlukte tutmak, birden fazla
    uvicorn worker'i oldugunda partiler farkli sureclere dagildigi icin
    snapshot'i HIC tamamlanmamis gosterirdi (silinen cihaz sonsuza kadar
    kalirdi). Yeni bir `snapshot_id` baslayinca eski yarim snapshot kendini
    tamamlayamaz; damgasi eskidigi icin bir sonraki tamamlanan snapshot onu
    da temizler.
    """
    if not zarf.snapshot or not zarf.snapshot_id:
        # Korelasyon kimligi yoksa hangi partilerin ayni snapshot'a ait
        # oldugu BILINEMEZ. Silmemek tek guvenli davranis.
        return 0
    sayi = zarf.snapshot_batch_count
    if sayi is None or sayi < 1:
        return 0

    if sayi == 1:
        # Tek partilik snapshot: az once isledik, kume TAM. Bu dal ayrica
        # BOS filoyu kapsar (`devices: []`) — cihaz satiri yazilmadigi icin
        # asagidaki parti sayimi 0 kalir ve uzlastirma hic calismazdi.
        tamam = True
    else:
        gorulen = db.scalar(
            select(func.count(distinct(DeviceRuntimeHealth.snapshot_batch_index))).where(
                DeviceRuntimeHealth.gateway_code == zarf.gateway_code,
                DeviceRuntimeHealth.snapshot_id == zarf.snapshot_id,
            )
        )
        tamam = int(gorulen or 0) >= sayi
    if not tamam:
        return 0

    # `snapshot_id IS NULL` ACIKCA yazilmali: SQL'de `NULL != 'x'` sonucu
    # NULL'dur ve satiri ELEMEZ. Yalnizca esitsizlige bakan bir kosul,
    # hicbir snapshot gormemis satirlari sonsuza kadar birakirdi.
    sonuc = db.execute(
        delete(DeviceRuntimeHealth)
        .where(
            DeviceRuntimeHealth.gateway_code == zarf.gateway_code,
            (DeviceRuntimeHealth.snapshot_id.is_(None))
            | (DeviceRuntimeHealth.snapshot_id != zarf.snapshot_id),
        )
        # `synchronize_session=False`: kosul OR/IS NULL icerdigi icin ORM'in
        # varsayilan "evaluate" stratejisi Python tarafinda cozmeye calisip
        # patlayabilir. Hemen ardindan commit geldigi ve nesneler expire
        # oldugu icin oturumu senkronlamanin degeri de yok.
        .execution_options(synchronize_session=False)
    )
    return int(sonuc.rowcount or 0)


def sagligi_uygula(
    db: Session,
    *,
    gateway_code: str,
    payload: Any,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Bir saglik partisini uygula. Ozet sozluk doner (loglama/test icin).

    Bayat parti REDDEDILMEZ, YOK SAYILIR: sozlesme geregi 4xx gecici sayilip
    yeniden denenir, yani bayat bir yeniden gonderimi hata saymak gateway'i
    sonsuz donguye sokardi. 2xx doner, hicbir sey yazilmaz.
    """
    zarf = zarfi_coz(payload, gateway_code=gateway_code)
    an = now or datetime.now(timezone.utc)

    gelen = (zarf.boot_id, zarf.sequence)
    saklanan = saklanan_kursor(db, gateway_code)
    if bayat_mi(gelen, saklanan):
        logger.info(
            "event=device_health_bayat gateway_code=%s gelen=%s saklanan=%s",
            gateway_code, gelen, saklanan,
        )
        return {"stale": True, "applied": 0, "skipped": 0, "reconciled": 0}

    cozulen: list[tuple[str, dict[str, Any]]] = []
    atlanan = 0
    for ham in zarf.devices:
        esleme = _wire_to_model(ham)
        if esleme is None:
            atlanan += 1
            continue
        cozulen.append(esleme)

    kodlar = [kod for kod, _ in cozulen]
    mevcut: dict[str, DeviceRuntimeHealth] = {}
    if kodlar:
        # Toplu okuma: cihaz basina `db.get` 500'luk bir partide 500 sorgu
        # demekti.
        mevcut = {
            satir.device_code: satir
            for satir in db.scalars(
                select(DeviceRuntimeHealth).where(
                    DeviceRuntimeHealth.device_code.in_(kodlar)
                )
            ).all()
        }

    # DURUM DEGISIMLERI — olay kaydina YALNIZCA gercek gecisler yazilir.
    #
    # Parti basina yazmak 2 yillik FIFO olay kaydini gurultuyle doldururdu
    # (300sn'de bir snapshot x 600 cihaz). Gecis ise cihaz basina gunde
    # birkac kez olur: `smart_idle`a girmek ve uyanmak operatorun gecmise
    # donup bakmak istedigi gercek olaylardir.
    gecisler: list[tuple[str, str, str]] = []  # (kod, onceki, yeni)

    for kod, alanlar in cozulen:
        satir = mevcut.get(kod)
        # ILK GOZLEM OLAY URETMEZ. Uretseydi ilk tam snapshot butun filo
        # icin ayni anda 600 satir yazardi — hicbiri bir DEGISIMI
        # anlatmadigi halde.
        if satir is not None:
            onceki_durum = satir.connection_state
            yeni_durum = alanlar.get("connection_state")
            if onceki_durum and yeni_durum and onceki_durum != yeni_durum:
                gecisler.append((kod, onceki_durum, yeni_durum))
        if satir is None:
            satir = DeviceRuntimeHealth(device_code=kod)
            db.add(satir)
            # YENI SATIR DA HARITAYA GIRER. Ayni parti icinde ayni
            # `device_code` iki kez gelirse (bozuk/mukerrer parti) ikinci
            # gecis AYNI nesneyi gunceller; girmeseydi ayni birincil
            # anahtarla IKINCI bir nesne eklenir ve flush IntegrityError ile
            # patlardi — gateway 5xx'i gecici sayip sonsuza kadar yeniden
            # denerdi.
            mevcut[kod] = satir
        satir.gateway_code = gateway_code
        for ad, deger in alanlar.items():
            setattr(satir, ad, deger)
        satir.gateway_instance_id = zarf.gateway_instance_id
        satir.boot_id = zarf.boot_id
        satir.sequence = zarf.sequence
        if zarf.snapshot:
            # SNAPSHOT DAMGASI YALNIZCA SNAPSHOT PARTILERINDE YAZILIR.
            # Delta'da yazilsaydi (ya da temizlenseydi), devam eden bir
            # snapshot sirasinda gelen delta cihazin damgasini bozar ve
            # snapshot tamamlandigi anda o cihaz "snapshot'ta yok" sanilip
            # SILINIRDI. Delta yalnizca degeri gunceller.
            satir.snapshot_id = zarf.snapshot_id
            satir.snapshot_batch_index = zarf.snapshot_batch_index
        satir.updated_at = an

    _gecisleri_yaz(db, gecisler, an)

    # Uzlastirma silmesi, bu partinin yazdiklarini gormeli.
    db.flush()
    silinen = _uzlastir(db, zarf)

    # --- UYANAN CIHAZ -> BEKLEYEN YAPILANDIRMA -----------------------------
    #
    # Uyuyan bir Horstmann'a gonderilen yapilandirma komutu 120 saniyede
    # oluyordu; cihaz ise 24 saate kadar uyuyabilir. Cozum komut omrunu
    # uzatmak DEGIL (o sure kesici komutlarini da kapsayan bir guvenlik
    # invaryantidir), cihaz DOGAL OLARAK uyandiginda O AN taze bir komut
    # uretmektir.
    #
    # NEDEN BURADA: bu, cihazin uyandigini ogrendigimiz TEK yer. Ayri bir
    # zamanlayici ile dakikada bir yoklamak hem gecikme eklerdi hem de
    # gereksiz sorgu uretirdi.
    #
    # NEDEN COMMIT'TEN ONCE: saglik satiri, niyetin durum gecisi ve uretilen
    # komut AYNI transaction'da yazilir. Backend tam ortada yeniden
    # baslarsa ya hepsi vardir ya hicbiri — "komut uretildi ama niyet hala
    # bekliyor" gibi bir ara durum olusamaz.
    #
    # HER PARTIDE KOMUT URETMEZ: once tek indeksli sorguyla bu partide
    # bekleyen niyet var mi diye bakar; 600 cihazlik filoda bu genelde
    # sifirdir.
    # SAVEPOINT: yapilandirma tarafi KENDI ICINDE atomiktir ama sagligi
    # REHIN ALMAZ.
    #
    # Iki gereksinim ayni anda saglanmali:
    #   * Durum gecisi (`BEKLIYOR -> KUYRUKTA`) ve uretilen komut ya IKISI
    #     BIRDEN yazilir ya hicbiri; yoksa "komut var ama niyet hala
    #     bekliyor" gibi bir ara durum ikinci bir komut daha urettirirdi.
    #   * Yapilandirma tarafindaki bir hata SAGLIK ALIMINI DUSURMEMELI. Bu
    #     kanal 600 cihazin durum gozlemini tasiyor; 5xx donmek gateway'i
    #     sonsuz yeniden denemeye sokar ve butun filonun durumu bayatlar.
    #
    # Ic islem (nested) ikisini birden verir: hata halinde yalnizca
    # savepoint geri alinir, saglik satirlari yazilmaya devam eder.
    try:
        from app.services import device_config_apply_service as apply_svc

        with db.begin_nested():
            apply_svc.uyanma_degerlendir(db, saglik_satirlari=mevcut, simdi=an)
    except Exception:  # noqa: BLE001 - saglik alimi korunur
        logger.exception(
            "device_health uyanma degerlendirmesi basarisiz gateway=%s", gateway_code
        )

    db.commit()

    return {
        "stale": False,
        "applied": len(cozulen),
        "skipped": atlanan,
        "reconciled": silinen,
        "transitions": len(gecisler),
    }


#: Olay kaydinda "dikkat gerektiren" sayilan durumlar. `smart_idle` BILEREK
#: YOK: uyku SAGLIKLIDIR ve uyari seviyesinde yazilirsa olay listesinde her
#: gece filo boyu sahte alarm gibi gorunur.
_UYARI_DURUMLARI = frozenset({"lost", "listener_error"})


def _gecisleri_yaz(
    db: Session, gecisler: list[tuple[str, str, str]], an: datetime
) -> None:
    """Baglanti durumu gecislerini olay kaydina yaz.

    `i18n_key` KULLANILIR, hazir metin DEGIL: olay listesi kullanicinin
    dilinde gosteriliyor. Backend'de Turkce cumle uretmek, ayni metni iki
    yerde tutmak ve Ingilizce arayuzde Turkce satir birakmak olurdu.
    `message` yalnizca geriye uyumluluk icin yazilir.

    ANAHTAR HEDEF DURUMA GORE (`device_runtime_smart_idle` gibi), tek bir
    genel anahtar + `{{to}}` parametresi DEGIL. Genel anahtar olsaydi
    ekranda ham enum gorunurdu ("... smart_idle oldu"); cevirinin icine
    baska bir ceviri gomme numaralarina gerek kalmadan her gecis kendi
    dogal cumlesini alir ("SN2_0 Smart Beklemeye gecti").
    """
    if not gecisler:
        return
    # Ice aktarim DONGUSEL BAGIMLILIGI onlemek icin burada: `event_service`
    # cagri zincirinde modul seviyesinde bu servise donebiliyor.
    from app.services.event_service import record_event

    for kod, onceki, yeni in gecisler:
        record_event(
            db,
            category="device",
            event_type="device_runtime_state_changed",
            message=f"{kod}: {onceki} -> {yeni}",
            severity="warning" if yeni in _UYARI_DURUMLARI else "info",
            device_code=kod,
            i18n_key=f"device_runtime_{yeni}",
            i18n_params={"device": kod, "from": onceki, "to": yeni},
            metadata={"from": onceki, "to": yeni},
            occurred_at=an,
        )
