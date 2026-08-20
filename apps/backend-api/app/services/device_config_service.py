"""Cihaz yapilandirma dosyalarinin is mantigi — sablon, surum, duzenleme.

Katman: router -> BURASI -> models. Codec (`horstmann_config_codec`) yalnizca
bayt/bicim isini yapar; hangi surumun yaratilacagi, kimin yaptigi ve neyin
denetime yazilacagi burada karara baglanir.

SURUMLER APPEND-ONLY
--------------------
Hicbir surum guncellenmez ya da silinmez. "Geri al" eskiyi geri YAZMAZ, eski
baytlarla YENI bir surum yaratir. Boylece "o gun cihazda ne vardi" sorusunun
cevabi hep dogru kalir; denetim kaydinin degeri de zaten bu.

DOSYA ADINDAKI SERI
-------------------
`<seri>_Configuration.csv` icindeki serinin BIRINCIL kaynagi artik
`devices.serial_number` kolonudur: kurulumda girilir, cihaz baglaninca
`master.serial_number` telemetrisinden otomatik guncellenir (bkz.
telemetry_consumer). Telemetri tek basina guvenilmezdi — cihaz bir an 0
gonderdi ve sistem `0_Configuration.csv` uretti. `master.info_serial_number`
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
def _gecerli_seri(seri: str | None) -> str | None:
    """SIFIR gecerli seri DEGILDIR. Sahada yasandi (2026-08-05): gercek SN20
    bir an `master.serial_number = 0` gonderdi ve sistem `0_Configuration.csv`
    uretmeye kalkti — o adi hicbir cihaz okumaz."""
    if not seri:
        return None
    s = seri.strip()
    if not s or s.strip("0") == "":
        return None
    return s


def device_serial(db: Session, device_id: int) -> str | None:
    """Cihazin seri numarasi (dolgusuz).

    ONCELIK SIRASI:
      1. `devices.serial_number` — kurulumda girilir, cihaz baglaninca
         telemetriden OTOMATIK guncellenir (bkz. telemetry_consumer). Kalici
         kaynak budur; telemetrinin anlik sifirlanmasi akisi KILITLEYEMEZ
         (sahada yasandi: cihaz bir an seri=0 gonderdi).
      2. Telemetri (`master.serial_number`) — kayitta seri yoksa ve gecerli
         (sifir olmayan) bir deger geldiyse.
      3. Cihaz KODU, salt rakamsa — operatorler kodu seri ile acar ("50984").
         Rakam olmayan koda DUSULMEZ: yanlis ada yazilan dosyayi cihaz gormez.
    """
    cihaz = db.get(Device, device_id)
    if cihaz is not None:
        seri = _gecerli_seri(getattr(cihaz, "serial_number", None))
        if seri:
            return seri

    satir = db.execute(
        select(TelemetryLatest.value, TelemetryLatest.value_string).where(
            TelemetryLatest.device_id == device_id,
            TelemetryLatest.signal_key == SERIAL_SIGNAL,
        )
    ).first()
    if satir is not None:
        sayi, metin = satir
        if metin:
            seri = _gecerli_seri(metin)
            if seri:
                return seri
        elif sayi is not None:
            # Telemetri sayisal geliyor (50984.0); dosya adinda ondalik olamaz.
            seri = _gecerli_seri(str(int(sayi)))
            if seri:
                return seri

    import re as _re

    if cihaz is not None and _re.fullmatch(r"[0-9]{1,20}", cihaz.code or ""):
        return _gecerli_seri(cihaz.code)
    return None


def find_device_id_by_serial(db: Session, seri: str) -> int | None:
    """Seri numarasindan cihaz bulur.

    Hem FTP olay yakalayicisi (dahili mod) hem yoklama worker'i (harici mod)
    bunu kullanir: cihazin yazdigi `<seri>_Configuration.csv` dosyasindaki
    seri, cihazi TANIMLAR. Eslesme yoksa None — bilinmeyen bir seriyi rastgele
    bir cihaza baglamak, yanlis cihazin gecmisini kirletirdi.

    Oncelik `device_serial` ile AYNI: once cihaz kaydi, sonra telemetri,
    son care salt-rakam cihaz kodu. Iki kaynak ayni cihazi gosterirse zaten
    ayni sonuc; farkli cihazlari gosteriyorsa kalici kayit kazanir.
    """
    if _gecerli_seri(seri) is None:
        return None

    kayit = db.execute(
        select(Device.id).where(Device.serial_number == seri)
    ).scalars().first()
    if kayit is not None:
        return kayit

    aday = db.execute(
        select(
            TelemetryLatest.device_id, TelemetryLatest.value, TelemetryLatest.value_string
        ).where(TelemetryLatest.signal_key == SERIAL_SIGNAL)
    ).all()
    for did, deger, metin in aday:
        okunan = (metin or "").strip() or (str(int(deger)) if deger is not None else "")
        if okunan == seri:
            return did

    return db.execute(select(Device.id).where(Device.code == seri)).scalars().first()


def ingest_pulled_config(
    db: Session, *, device_id: int, ham: bytes, filename: str
) -> DeviceConfigVersion | None:
    """Cihazin FTP'ye yazdigi config baytlarini yeni SURUM olarak kaydeder.

    AYNI icerik yeni surum URETMEZ (None doner): cihaz her cagrida ayni
    dosyayi yazarsa gecmis anlamsiz kayitlarla dolar ve gercek degisiklikler
    gorunmez hale gelir.

    Bozuk dosya burada PATLAR (ConfigParseError) — cagiran taraf olayi
    'islenemedi' olarak kaydeder; sessizce yutmak teshisi imkansiz kilardi.
    """
    belge = parse(ham)
    mevcut = current_version(db, device_id)
    if mevcut is not None and bytes(mevcut.raw) == ham:
        return None
    return create_version(
        db,
        device_id=device_id,
        raw=ham,
        source="cihazdan_cekildi",
        actor=None,  # cihazin kendisi yazdi, bir kullanici degil
        note=(
            f"Cihaz FTP'ye yazdi ({filename})"
            + ("" if belge.checksum_valid else " — saglama toplami GECERSIZ")
        ),
    )


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


#: Depoyla birlikte gelen fabrika config'i (gercek 50984 cihazindan alindi,
#: checksum'i dogrulanmis 1095 baytlik dosya). CSV govdesi cihaza ozel kimlik
#: TASIMAZ (bkz. DeviceConfigTemplate docstring) — her SN2 cihazina uygulanabilir.
_FABRIKA_DOSYASI = Path(__file__).resolve().parents[1] / "data/horstmann_sn2_factory_config.csv"
FABRIKA_SABLON_ADI = "Fabrika ayarlari (SN2)"


def seed_factory_template(db: Session) -> DeviceConfigTemplate | None:
    """SN2 modeli icin HIC sablon yoksa fabrika sablonunu yukler.

    Kurulumdan cikan sistemde "sablon tanimlanmamis, yeni cihaz config'siz"
    durumu kalmasin diye acilista cagrilir. Kullanici sablon tanimladiysa
    (bir tane bile) DOKUNMAZ — id yerine varligina bakmak bilincli: kullanici
    fabrika sablonunu silmisse tekrar dayatmak onun kararini ezerdi.
    """
    if list_templates(db, "horstmann_sn_2_0"):
        return None
    try:
        ham = _FABRIKA_DOSYASI.read_bytes()
    except OSError:
        return None  # paketle gelmemis olabilir; eksiklik arayuzde gorunur
    try:
        return create_template(
            db,
            name=FABRIKA_SABLON_ADI,
            device_model="horstmann_sn_2_0",
            raw=ham,
            source_filename="50984_Configuration.csv",
            note="Depoyla gelen fabrika yapilandirmasi (gercek cihazdan alindi)",
            is_default=True,
            actor="sistem",
        )
    except ConfigParseError:
        return None  # bozuk paket dosyasi sablon olamaz; sessizce dayatilmaz


def ensure_initial_version(
    db: Session, device: Device, *, actor: str | None = None
) -> DeviceConfigVersion | None:
    """Cihaz eklendiginde sablondan ilk surumu uretir.

    Zaten surum varsa DOKUNMAZ (idempotent) — cihaz kaydi guncellenirken
    tekrar cagrilirsa kullanicinin duzenlemelerini ezmemeli.

    Sablon yoksa `None` doner, PATLAMAZ: config sablonu tanimlanmamis olmasi
    cihaz eklemeyi engellememelidir. Eksiklik arayuzde gorunur.

    SANAL SET KAYITLARINDA HIC CALISMAZ: yapilandirma dosyasi FIZIKSEL
    cihaza aittir (`<seri>_Configuration.csv` ve seri numarasi kitindir).
    Her sete ayri bir surum zinciri acmak, tek dosya icin uc bagimsiz "v5"
    uretir; denetim kaydi hangi v5'in gercekten cihazda oldugunu
    soyleyemez hale gelirdi. Setler Yapilandirma sekmesinde kite yonlenir.
    """
    if device.parent_device_id is not None:
        return None
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


def apply_template_changes(
    db: Session, *, template_id: int, changes: dict[str, int], actor: str | None = None
) -> DeviceConfigTemplate:
    """Sablondaki degerleri degistirir (YERINDE — sablonlar surumlenmez).

    Cihaz surumlerinden FARKI bilincli: surumler denetim gecmisidir ve
    append-only'dir; sablon ise gelecekteki cihazlarin kaynagi olan TEK
    guncel dosyadir. Gecmis surumler baytlari kopyaladigi icin sablon
    degisikligi onlari ETKILEMEZ. Checksum codec tarafindan yeniden uretilir.
    """
    sablon = db.get(DeviceConfigTemplate, template_id)
    if sablon is None:
        raise LookupError(f"Sablon bulunamadi: {template_id}")
    doc = parse(bytes(sablon.raw))
    for cat_index, deger in changes.items():
        doc.set_int(cat_index, deger)
    sablon.raw = render(doc)
    db.flush()
    return sablon


def _alan_kurallarini_dogrula(changes: dict[str, int]) -> None:
    """Cihazin KENDI kabul kurallari — codec'in goremedigi kisitlar.

    Su an tek kural Dial-In Interval (`2010C6`): Horstmann bu degerin 1440'in
    boleni olmasini ister (katalog `desc`). 100 dk 2 bayta pekala siger, yani
    codec gecirir; cihaz ise reddeder ve bunu Grid'e SOYLEMEZ. Sonuc:
    ekranda 100 yazar, sahada eski deger calisir — sessiz ayrisma.

    Yeni bir alan kurali cikarsa buraya eklenir; boylece hem tekil duzenleme
    hem toplu sablon uygulama ayni kapidan gecer.
    """
    from app.schemas.dnp3_extended import (
        DIAL_IN_CAT_INDEX,
        DIAL_IN_DAY_MINUTES,
        DIAL_IN_INTERVAL_MAX,
        DIAL_IN_INTERVAL_MIN,
        dial_in_gecerli_degerler,
    )

    ham = changes.get(DIAL_IN_CAT_INDEX)
    if ham is None:
        return
    deger = int(ham)
    if not (DIAL_IN_INTERVAL_MIN <= deger <= DIAL_IN_INTERVAL_MAX):
        raise ValueError(
            f"Dial-In araligi {deger} dk gecersiz: "
            f"{DIAL_IN_INTERVAL_MIN}-{DIAL_IN_INTERVAL_MAX} dk arasinda olmalidir."
        )
    if DIAL_IN_DAY_MINUTES % deger != 0:
        gecerli = ", ".join(str(d) for d in dial_in_gecerli_degerler())
        raise ValueError(
            f"Dial-In araligi {deger} dk gecersiz: deger 1440'in (24 saat) "
            f"boleni olmalidir, yoksa cihaz yapilandirmayi kabul etmez. "
            f"Gecerli degerler: {gecerli}."
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

    ALAN OZEL KURALLAR (codec'in bilmedigi)
    ---------------------------------------
    Codec yalnizca "deger bu kac bayta siger mi" diye bakar. Bazi girdilerin
    CIHAZIN KENDISINDEN gelen ek kurallari var ve onlar buradan gecerse
    dosya yazilir, cihaza gonderilir, cihaz REDDEDER ve operator ayarin
    uygulandigini sanir. `_alan_kurallarini_dogrula` o sessiz basarisizligi
    kapatir.
    """
    guncel = current_version(db, device_id)
    if guncel is None:
        raise ConfigNotFound(f"cihaz {device_id} icin yapilandirma surumu yok")

    _alan_kurallarini_dogrula(changes)

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
#: CatIndex -> aciklama (katalogdaki opsiyonel "desc" alani; manuel kaynakli).
_aciklama_onbellek: dict[str, str] = {}


def builtin_catalog() -> dict[str, CatalogEntry]:
    """Gomulu katalogu dondurur (bir kez okunur).

    Dosya okunamazsa BOS doner, PATLAMAZ: katalog bir GOSTERIM zenginligidir,
    onun yoklugu yapilandirmayi goruntulenemez yapmamali.
    """
    global _katalog_onbellek, _aciklama_onbellek
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
            # Ayar ACIKLAMALARI (Horstmann manuelinden) ayni dosyadaki "desc"
            # alanindan gelir; arayuz tooltip'te gosterir. Alani olmayan
            # girdide tooltip yalnizca ad/kod gosterir — icerik dolduruldukca
            # zenginlesir, kod degismez.
            _aciklama_onbellek = {
                ci: v["desc"] for ci, v in ham.items() if v.get("desc")
            }
        except Exception:  # noqa: BLE001
            _katalog_onbellek = {}
            _aciklama_onbellek = {}
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
                "description": _aciklama_onbellek.get(e.cat_index),
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
