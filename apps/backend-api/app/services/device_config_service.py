"""Cihaz yapilandirma dosyalarinin is mantigi — sablon, surum, duzenleme.

Katman: router -> BURASI -> models. Codec (`horstmann_config_codec`) yalnizca
bayt/bicim isini yapar; hangi surumun yaratilacagi, kimin yaptigi ve neyin
denetime yazilacagi burada karara baglanir.

SURUMLER APPEND-ONLY
--------------------
Hicbir surum guncellenmez ya da silinmez. "Geri al" eskiyi geri YAZMAZ, eski
baytlarla YENI bir surum yaratir. Boylece "o gun cihazda ne vardi" sorusunun
cevabi hep dogru kalir; denetim kaydinin degeri de zaten bu.

DOSYA ADI TELEMETRIDEN GELIR
----------------------------
`<seri>_Configuration.csv` icindeki seri, `master.serial_number` SINYALIDIR.
`devices` tablosunda seri kolonu YOKTUR. Ayrica `master.info_serial_number`
sifir dolguludur (`0000050984`) ve KULLANILMAMALIDIR — o adla yazilan dosyayi
cihaz hic gormez.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models.device import Device
from app.models.device_config import DeviceConfigTemplate, DeviceConfigVersion
from app.models.telemetry_latest import TelemetryLatest
from app.services import device_update_files as duf
from app.services.horstmann_config_codec import (
    CatalogEntry,
    ConfigParseError,
    parse,
    render,
)

#: Seri numarasini tasiyan sinyal. Sifir dolgulu `master.info_serial_number`
#: DEGIL — bkz. modul docstring.
SERIAL_SIGNAL = "master.serial_number"


class ConfigNotFound(LookupError):
    """Cihazin hic yapilandirma surumu yok."""


class NoTemplate(LookupError):
    """Cihaz modeli icin varsayilan sablon tanimlanmamis."""


# --- seri numarasi ---------------------------------------------------------
def device_serial(db: Session, device_id: int) -> str | None:
    """Cihazin telemetriden okunan seri numarasi (dolgusuz)."""
    satir = db.execute(
        select(TelemetryLatest.value, TelemetryLatest.value_string).where(
            TelemetryLatest.device_id == device_id,
            TelemetryLatest.signal_key == SERIAL_SIGNAL,
        )
    ).first()
    if satir is None:
        return None
    sayi, metin = satir
    if metin:
        return metin.strip() or None
    if sayi is None:
        return None
    # Telemetri sayisal geliyor (50984.0); dosya adinda ondalik olamaz.
    return str(int(sayi))


def config_filename(db: Session, device_id: int) -> str:
    """`<seri>_Configuration.csv`. Seri yoksa ACIK hata verir.

    Sessizce cihaz adina ya da id'ye dusmek, cihazin HIC GORMEYECEGI bir dosya
    uretirdi — ve bu, hicbir hata mesaji olmadan "komut gitti ama bir sey
    olmadi" seklinde ortaya cikardi.
    """
    seri = device_serial(db, device_id)
    plan = duf.build_plan(
        duf.UpdateKind.CONFIG, duf.UpdateScope.SINGLE, serial_number=seri
    )
    return plan.filename


# --- sablonlar -------------------------------------------------------------
def list_templates(db: Session, device_model: str | None = None) -> list[DeviceConfigTemplate]:
    q = select(DeviceConfigTemplate).order_by(
        DeviceConfigTemplate.device_model, DeviceConfigTemplate.name
    )
    if device_model:
        q = q.where(DeviceConfigTemplate.device_model == device_model)
    return list(db.execute(q).scalars())


def default_template(db: Session, device_model: str) -> DeviceConfigTemplate | None:
    return db.execute(
        select(DeviceConfigTemplate).where(
            DeviceConfigTemplate.device_model == device_model,
            DeviceConfigTemplate.is_default.is_(True),
        )
    ).scalars().first()


def create_template(
    db: Session,
    *,
    name: str,
    device_model: str,
    raw: bytes,
    source_filename: str | None = None,
    note: str | None = None,
    is_default: bool = False,
    actor: str | None = None,
) -> DeviceConfigTemplate:
    """Sablon kaydeder. Bozuk checksum'li dosya sablon OLAMAZ.

    Surum kaydinda bozuk dosyaya izin veriyoruz (cihazdan oyle gelmis
    olabilir, kullaniciya gostermek gerekir) ama SABLON farklidir: her yeni
    cihaz ondan turer, yani tek bir bozuk sablon filoya yayilir.
    """
    doc = parse(raw)
    if doc.checksum_valid is not True:
        raise ConfigParseError(
            "Sablon dosyasinin saglama toplami gecersiz — bu dosya her yeni "
            "cihaza kopyalanacagi icin kabul edilmiyor."
        )

    if is_default:
        _clear_default(db, device_model)

    sablon = DeviceConfigTemplate(
        name=name,
        device_model=device_model,
        raw=raw,
        source_filename=source_filename,
        note=note,
        is_default=is_default,
        created_by=actor,
        created_at=datetime.now(timezone.utc),
    )
    db.add(sablon)
    db.flush()
    return sablon


def set_default_template(db: Session, template_id: int) -> DeviceConfigTemplate:
    sablon = db.get(DeviceConfigTemplate, template_id)
    if sablon is None:
        raise LookupError(f"Sablon bulunamadi: {template_id}")
    _clear_default(db, sablon.device_model)
    sablon.is_default = True
    db.flush()
    return sablon


def _clear_default(db: Session, device_model: str) -> None:
    """Tip basina TEK varsayilan. Iki varsayilan, yeni cihazin hangisini
    alacagini sorgu sirasina birakirdi — yani belirsiz davranis."""
    for s in db.execute(
        select(DeviceConfigTemplate).where(
            DeviceConfigTemplate.device_model == device_model,
            DeviceConfigTemplate.is_default.is_(True),
        )
    ).scalars():
        s.is_default = False


# --- surumler --------------------------------------------------------------
def list_versions(db: Session, device_id: int) -> list[DeviceConfigVersion]:
    return list(
        db.execute(
            select(DeviceConfigVersion)
            .where(DeviceConfigVersion.device_id == device_id)
            .order_by(DeviceConfigVersion.version.desc())
        ).scalars()
    )


def current_version(db: Session, device_id: int) -> DeviceConfigVersion | None:
    return db.execute(
        select(DeviceConfigVersion)
        .where(DeviceConfigVersion.device_id == device_id)
        .order_by(DeviceConfigVersion.version.desc())
        .limit(1)
    ).scalars().first()


def create_version(
    db: Session,
    *,
    device_id: int,
    raw: bytes,
    source: str,
    actor: str | None = None,
    note: str | None = None,
    template_id: int | None = None,
) -> DeviceConfigVersion:
    """Yeni surum ekler. Surum numarasi DB'deki en buyukten turetilir."""
    if source not in ("sablon", "cihazdan_cekildi", "yuklendi", "duzenlendi"):
        raise ValueError(f"gecersiz kaynak: {source}")

    enbuyuk = db.execute(
        select(func.max(DeviceConfigVersion.version)).where(
            DeviceConfigVersion.device_id == device_id
        )
    ).scalar()

    surum = DeviceConfigVersion(
        device_id=device_id,
        version=(enbuyuk or 0) + 1,
        raw=raw,
        source=source,
        template_id=template_id,
        note=note,
        created_by=actor,
        created_at=datetime.now(timezone.utc),
    )
    db.add(surum)
    db.flush()
    return surum


def ensure_initial_version(
    db: Session, device: Device, *, actor: str | None = None
) -> DeviceConfigVersion | None:
    """Cihaz eklendiginde sablondan ilk surumu uretir.

    Zaten surum varsa DOKUNMAZ (idempotent) — cihaz kaydi guncellenirken
    tekrar cagrilirsa kullanicinin duzenlemelerini ezmemeli.

    Sablon yoksa `None` doner, PATLAMAZ: config sablonu tanimlanmamis olmasi
    cihaz eklemeyi engellememelidir. Eksiklik arayuzde gorunur.
    """
    if current_version(db, device.id) is not None:
        return None
    sablon = default_template(db, device.model)
    if sablon is None:
        return None
    return create_version(
        db,
        device_id=device.id,
        # Baytlar KOPYALANIR: sablon sonradan degisse/silinse de bu surum
        # oldugu gibi kalmali.
        raw=bytes(sablon.raw),
        source="sablon",
        actor=actor,
        template_id=sablon.id,
        note=f"'{sablon.name}' sablonundan olusturuldu",
    )


def apply_changes(
    db: Session,
    *,
    device_id: int,
    changes: dict[str, int],
    actor: str | None = None,
    note: str | None = None,
) -> DeviceConfigVersion:
    """Guncel surumdeki degerleri degistirip YENI surum yaratir.

    `changes`: CatIndex -> yeni sayisal deger (orn. {"2010C6": 720}).
    Uzunluk asimi ve olmayan girdi codec tarafindan reddedilir; buradan
    sessizce gecmez.
    """
    guncel = current_version(db, device_id)
    if guncel is None:
        raise ConfigNotFound(f"cihaz {device_id} icin yapilandirma surumu yok")

    doc = parse(bytes(guncel.raw))
    for cat_index, deger in changes.items():
        doc.set_int(cat_index, deger)

    return create_version(
        db,
        device_id=device_id,
        raw=render(doc),
        source="duzenlendi",
        actor=actor,
        note=note or f"{len(changes)} ayar degistirildi (v{guncel.version} uzerinden)",
        template_id=guncel.template_id,
    )


def revert_to(
    db: Session, *, device_id: int, version: int, actor: str | None = None
) -> DeviceConfigVersion:
    """Eski surume doner — eskiyi geri yazmadan, YENI surum yaratarak."""
    eski = db.execute(
        select(DeviceConfigVersion).where(
            DeviceConfigVersion.device_id == device_id,
            DeviceConfigVersion.version == version,
        )
    ).scalars().first()
    if eski is None:
        raise ConfigNotFound(f"cihaz {device_id} icin v{version} yok")

    return create_version(
        db,
        device_id=device_id,
        raw=bytes(eski.raw),
        source="duzenlendi",
        actor=actor,
        note=f"v{version} surumune geri donuldu",
        template_id=eski.template_id,
    )


# --- gosterim --------------------------------------------------------------
#: Yerlesik anlam katalogu (CatIndex -> ad/birim). Kaynak: Smart Navigator
#: Explorer'in urettigi `<seri>.xml` ObjectCatalog dosyasi; bir kez cikarilip
#: `app/data/horstmann_sn2_config_catalog.json` olarak gomuldu.
#:
#: NEDEN GOMULU: cihazdan gelen CSV yalnizca `GROUP,INDEX,...` icerir, ADLARI
#: TASIMAZ. Katalog olmadan arayuz "381101 = 0" gostermek zorunda kalir ve
#: kullanici hangi ayari degistirdigini bilemez — bu, yanlis ayar degistirmenin
#: en kolay yoludur.
_KATALOG_YOLU = Path(__file__).resolve().parents[1] / "data/horstmann_sn2_config_catalog.json"
_katalog_onbellek: dict[str, CatalogEntry] | None = None


def builtin_catalog() -> dict[str, CatalogEntry]:
    """Gomulu katalogu dondurur (bir kez okunur).

    Dosya okunamazsa BOS doner, PATLAMAZ: katalog bir GOSTERIM zenginligidir,
    onun yoklugu yapilandirmayi goruntulenemez yapmamali.
    """
    global _katalog_onbellek
    if _katalog_onbellek is None:
        try:
            ham = json.loads(_KATALOG_YOLU.read_text(encoding="utf-8"))
            _katalog_onbellek = {
                ci: CatalogEntry(
                    cat_index=ci, meaning=v.get("meaning", ""), value=None,
                    unit=v.get("unit"),
                )
                for ci, v in ham.items()
            }
        except Exception:  # noqa: BLE001
            _katalog_onbellek = {}
    return _katalog_onbellek


def describe(
    raw: bytes, catalog: dict[str, CatalogEntry] | None = None
) -> list[dict]:
    """Ham dosyayi arayuzun gosterebilecegi satirlara cevirir.

    Katalog verilmezse GOMULU katalog kullanilir. Katalogda olmayan girdi ham
    CatIndex ile donulur — eksik bir ad, satiri GIZLEMEK icin sebep degildir.
    """
    doc = parse(raw)
    katalog = catalog if catalog is not None else builtin_catalog()
    satirlar: list[dict] = []
    for e in doc.entries:
        bilgi = katalog.get(e.cat_index)
        # Uzun alanlar metin, kisalar sayi olarak anlamli. Ikisini de veriyoruz
        # ki arayuz hangisini gosterecegine karar verebilsin.
        satirlar.append(
            {
                "cat_index": e.cat_index,
                "group": e.group,
                "index": e.index,
                "length": e.length,
                "value_int": e.as_int() if 0 < e.length <= 8 else None,
                "value_text": e.as_text() if e.length > 8 else None,
                "raw_hex": e.raw.hex().upper(),
                "meaning": bilgi.meaning if bilgi else None,
                "unit": bilgi.unit if bilgi else None,
            }
        )
    return satirlar


def diff(onceki: bytes, sonraki: bytes) -> list[dict]:
    """Iki surum arasindaki DEGER farklari.

    Denetim ekraninin ana ciktisi: "bu surumde ne degisti".
    """
    a = {e.cat_index: e for e in parse(onceki).entries}
    b = {e.cat_index: e for e in parse(sonraki).entries}

    farklar: list[dict] = []
    for ci in sorted(set(a) | set(b)):
        eski, yeni = a.get(ci), b.get(ci)
        if eski is not None and yeni is not None and eski.raw == yeni.raw:
            continue
        farklar.append(
            {
                "cat_index": ci,
                "before": eski.raw.hex().upper() if eski else None,
                "after": yeni.raw.hex().upper() if yeni else None,
                "before_int": eski.as_int() if eski and 0 < eski.length <= 8 else None,
                "after_int": yeni.as_int() if yeni and 0 < yeni.length <= 8 else None,
            }
        )
    return farklar
