"""Cihaz yapilandirma servisi — surum, sablon, duzenleme, geri alma.

Bu testlerin odagi "fonksiyon calisti mi" degil, GERI DONULEMEZ hatalarin
onlenmesi:

  - Surumun uzerine yazilmasi (gecmis kaybi)
  - Seri numarasi yokken cihazin HIC GORMEYECEGI adla dosya uretilmesi
  - Bozuk checksum'li bir dosyanin SABLON olup tum filoya yayilmasi
  - Sablon silinince cihaz gecmisinin de silinmesi
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.db.base import Base
from app.models.device import Device
from app.models.device_config import DeviceConfigTemplate
from app.models.telemetry_latest import TelemetryLatest
from app.services import device_config_service as svc
from app.services.device_update_files import InvalidUpdateTarget
from app.services.horstmann_config_codec import (
    MARKER,
    ConfigParseError,
    calculate_checksum,
    parse,
)


def _dosya(dial_in: int = 1440, voltage: int = 10000) -> bytes:
    govde = (
        b"\r\n".join(
            [
                b"2010,C6,02," + dial_in.to_bytes(2, "little").hex().upper().encode(),
                b"3200,01,04," + voltage.to_bytes(4, "little").hex().upper().encode(),
                b"1202,02,00",
            ]
        )
        + b"\r\n"
    )
    return govde + calculate_checksum(govde).to_bytes(2, "little") + MARKER


@pytest.fixture()
def db():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    s = Session()
    try:
        yield s
    finally:
        s.close()


@pytest.fixture()
def cihaz(db):
    d = Device(code="SN20", name="SN20", model="horstmann_sn_2_0", ip_address="192.168.1.10", latitude=0.0, longitude=0.0)
    db.add(d)
    db.flush()
    db.add(
        TelemetryLatest(
            device_id=d.id,
            signal_key=svc.SERIAL_SIGNAL,
            value=50984.0,
            source_timestamp=datetime.now(timezone.utc),
            updated_at=datetime.now(timezone.utc),
        )
    )
    db.flush()
    return d


# --- dosya adi -------------------------------------------------------------
def test_dosya_adi_TELEMETRIDEN_gelen_seriyi_kullanir(db, cihaz) -> None:
    assert svc.config_filename(db, cihaz.id) == "50984_Configuration.csv"


def test_KAYITTAKI_seri_telemetriden_ONCELIKLIDIR(db, cihaz) -> None:
    """Kalici kaynak cihaz kaydi: telemetri anlik sacmalasa da (yanlis/eski
    deger) dosya adi kayittaki seriden gider."""
    cihaz.serial_number = "77777"
    db.flush()
    assert svc.device_serial(db, cihaz.id) == "77777"


def test_seri_SIFIR_GECERSIZDIR_rakamsal_koda_duser(db) -> None:
    """SAHADA YASANDI: cihaz bir an `master.serial_number = 0` gonderdi ve
    sistem `0_Configuration.csv` uretmeye kalkti — o adi hicbir cihaz okumaz.
    Sifir seri YOK sayilir; operatorler kodu seri ile actigi icin salt-rakam
    kod son care olarak kullanilir."""
    d = Device(code="50984", name="SN20", model="horstmann_sn_2_0", ip_address="192.168.1.12", latitude=0.0, longitude=0.0)
    db.add(d)
    db.flush()
    db.add(
        TelemetryLatest(
            device_id=d.id,
            signal_key=svc.SERIAL_SIGNAL,
            value=0.0,
            source_timestamp=datetime.now(timezone.utc),
            updated_at=datetime.now(timezone.utc),
        )
    )
    db.flush()
    assert svc.device_serial(db, d.id) == "50984"
    assert svc.config_filename(db, d.id) == "50984_Configuration.csv"


def test_seri_sifir_ve_kod_rakam_DEGILSE_acik_hata(db) -> None:
    d = Device(code="sahte-x", name="x", model="horstmann_sn_2_0", ip_address="192.168.1.13", latitude=0.0, longitude=0.0)
    db.add(d)
    db.flush()
    db.add(
        TelemetryLatest(
            device_id=d.id,
            signal_key=svc.SERIAL_SIGNAL,
            value=0.0,
            source_timestamp=datetime.now(timezone.utc),
            updated_at=datetime.now(timezone.utc),
        )
    )
    db.flush()
    with pytest.raises(InvalidUpdateTarget):
        svc.config_filename(db, d.id)


def test_seri_ile_bulma_KAYITTAKI_seriyi_de_gorur(db, cihaz) -> None:
    """Cihaz dosyayi kayitli serisiyle yazar; telemetri o an 0 olsa bile
    eslesme kayit uzerinden kurulmali."""
    cihaz.serial_number = "88888"
    db.flush()
    assert svc.find_device_id_by_serial(db, "88888") == cihaz.id
    # Sifir seri hicbir cihazla eslesmez.
    assert svc.find_device_id_by_serial(db, "0") is None


def test_seri_ondalik_gelse_de_TAM_SAYI_yazilir(db, cihaz) -> None:
    """Telemetri sayisal (50984.0); dosya adinda ondalik olamaz."""
    assert svc.device_serial(db, cihaz.id) == "50984"


def test_seri_YOKSA_acik_hata(db) -> None:
    """Sessizce cihaz adina/id'ye dusmek, cihazin HIC GORMEYECEGI bir dosya
    uretirdi ve bu hicbir hata vermeden 'komut gitti, bir sey olmadi' olarak
    ortaya cikardi."""
    d = Device(code="serisiz", name="serisiz", model="horstmann_sn_2_0", ip_address="192.168.1.11", latitude=0.0, longitude=0.0)
    db.add(d)
    db.flush()
    with pytest.raises(InvalidUpdateTarget):
        svc.config_filename(db, d.id)


# --- sablon ----------------------------------------------------------------
def test_BOZUK_checksum_SABLON_olamaz(db) -> None:
    """Tek bozuk sablon, sonraki HER cihaza kopyalanirdi."""
    bozuk = _dosya()[:-4] + b"\x00\x00" + MARKER
    with pytest.raises(ConfigParseError):
        svc.create_template(
            db, name="bozuk", device_model="horstmann_sn_2_0", raw=bozuk
        )


def test_tip_basina_TEK_varsayilan_kalir(db) -> None:
    """Iki varsayilan, yeni cihazin hangisini alacagini sorgu SIRASINA
    birakirdi — belirsiz davranis."""
    a = svc.create_template(
        db, name="A", device_model="horstmann_sn_2_0", raw=_dosya(), is_default=True
    )
    b = svc.create_template(
        db, name="B", device_model="horstmann_sn_2_0", raw=_dosya(), is_default=True
    )
    db.flush()
    assert svc.default_template(db, "horstmann_sn_2_0").id == b.id
    assert db.get(DeviceConfigTemplate, a.id).is_default is False


# --- fabrika sablonu -------------------------------------------------------
def test_fabrika_sablonu_BOS_kurulumda_yuklenir(db) -> None:
    """Depoyla gelen dogrulanmis dosya, sablonsuz kurulumda varsayilan olur —
    'yeni cihaza otomatik config' kancasi ve 'Sablondan olustur' dugmesi
    buna dayanir."""
    sablon = svc.seed_factory_template(db)
    assert sablon is not None
    assert sablon.is_default is True
    assert sablon.name == svc.FABRIKA_SABLON_ADI
    assert parse(bytes(sablon.raw)).checksum_valid is True
    # Ikinci cagri yenisini URETMEZ (her acilista kosuyor).
    assert svc.seed_factory_template(db) is None


def test_fabrika_sablonu_KULLANICI_SABLONUNU_ezmez(db) -> None:
    """Kullanici kendi sablonunu tanimladiysa (varsayilan olmasa bile) seed
    DOKUNMAZ — fabrika dosyasini dayatmak kullanicinin kararini ezerdi."""
    svc.create_template(
        db, name="ozel", device_model="horstmann_sn_2_0", raw=_dosya(), is_default=False
    )
    assert svc.seed_factory_template(db) is None


# --- ilk surum -------------------------------------------------------------
def test_cihaz_eklendiginde_sablondan_ILK_SURUM_uretilir(db, cihaz) -> None:
    sablon = svc.create_template(
        db, name="fabrika", device_model="horstmann_sn_2_0", raw=_dosya(), is_default=True
    )
    surum = svc.ensure_initial_version(db, cihaz)
    assert surum is not None
    assert surum.version == 1
    assert surum.source == "sablon"
    assert surum.template_id == sablon.id
    assert bytes(surum.raw) == bytes(sablon.raw)


def test_ilk_surum_uretimi_IDEMPOTENT(db, cihaz) -> None:
    """Cihaz kaydi guncellenirken tekrar cagrilirsa duzenlemeleri EZMEMELI."""
    svc.create_template(
        db, name="f", device_model="horstmann_sn_2_0", raw=_dosya(), is_default=True
    )
    svc.ensure_initial_version(db, cihaz)
    assert svc.ensure_initial_version(db, cihaz) is None
    assert len(svc.list_versions(db, cihaz.id)) == 1


def test_sablon_yoksa_cihaz_eklemek_PATLAMAZ(db, cihaz) -> None:
    """Config sablonu tanimli olmamasi cihaz eklemeyi engellememeli."""
    assert svc.ensure_initial_version(db, cihaz) is None


def test_sablonun_BAYTI_kopyalanir_referans_tutulmaz(db, cihaz) -> None:
    """Sablon sonradan degisse de gecmis surum OLDUGU GIBI kalmali; aksi
    halde 'o gun bu cihazda ne vardi' sorusu cevapsiz kalir."""
    sablon = svc.create_template(
        db, name="f", device_model="horstmann_sn_2_0", raw=_dosya(1440), is_default=True
    )
    surum = svc.ensure_initial_version(db, cihaz)
    ilk_bayt = bytes(surum.raw)

    sablon.raw = _dosya(720)  # sablon degisti
    db.flush()
    assert bytes(surum.raw) == ilk_bayt


# --- duzenleme -------------------------------------------------------------
def test_duzenleme_YENI_surum_yaratir_eskisini_bozmaz(db, cihaz) -> None:
    svc.create_version(db, device_id=cihaz.id, raw=_dosya(1440), source="yuklendi")
    yeni = svc.apply_changes(db, device_id=cihaz.id, changes={"2010C6": 720})

    assert yeni.version == 2
    surumler = svc.list_versions(db, cihaz.id)
    assert [s.version for s in surumler] == [2, 1]
    # v1 DOKUNULMAMIS
    assert parse(bytes(surumler[1].raw)).get("2010C6").as_int() == 1440
    assert parse(bytes(yeni.raw)).get("2010C6").as_int() == 720


def test_duzenleme_sonrasi_checksum_GECERLI(db, cihaz) -> None:
    """Eski toplam tasinsaydi cihaz dosyayi reddederdi."""
    svc.create_version(db, device_id=cihaz.id, raw=_dosya(), source="yuklendi")
    yeni = svc.apply_changes(db, device_id=cihaz.id, changes={"2010C6": 720})
    assert parse(bytes(yeni.raw)).checksum_valid is True


def test_sigmayan_deger_SURUM_YARATMADAN_reddedilir(db, cihaz) -> None:
    svc.create_version(db, device_id=cihaz.id, raw=_dosya(), source="yuklendi")
    with pytest.raises(ConfigParseError):
        svc.apply_changes(db, device_id=cihaz.id, changes={"2010C6": 70000})


def test_surum_yokken_duzenleme_ACIK_hata(db, cihaz) -> None:
    with pytest.raises(svc.ConfigNotFound):
        svc.apply_changes(db, device_id=cihaz.id, changes={"2010C6": 1})


# --- sablon duzenleme ------------------------------------------------------
def test_sablon_duzenleme_YERINDE_ve_checksum_gecerli(db) -> None:
    """Sablon surumlenmez, yerinde degisir; gecmis cihaz surumleri baytlari
    kopyaladigi icin ETKILENMEZ. Checksum yeniden uretilmeli."""
    sablon = svc.create_template(
        db, name="f", device_model="horstmann_sn_2_0", raw=_dosya(1440), is_default=True
    )
    svc.apply_template_changes(db, template_id=sablon.id, changes={"2010C6": 720})
    doc = parse(bytes(sablon.raw))
    assert doc.get("2010C6").as_int() == 720
    assert doc.checksum_valid is True


def test_sablon_duzenleme_SIGMAYAN_degeri_reddeder(db) -> None:
    sablon = svc.create_template(
        db, name="f2", device_model="horstmann_sn_2_0", raw=_dosya(), is_default=False
    )
    onceki = bytes(sablon.raw)
    with pytest.raises(ConfigParseError):
        svc.apply_template_changes(db, template_id=sablon.id, changes={"2010C6": 70000})
    assert bytes(sablon.raw) == onceki


# --- geri alma -------------------------------------------------------------
def test_geri_alma_ESKIYI_GERI_YAZMAZ_yeni_surum_yaratir(db, cihaz) -> None:
    """Gecmis her zaman dogru kalmali: v1'e donmek v3 uretir, v2'yi SILMEZ."""
    svc.create_version(db, device_id=cihaz.id, raw=_dosya(1440), source="yuklendi")
    svc.apply_changes(db, device_id=cihaz.id, changes={"2010C6": 720})
    geri = svc.revert_to(db, device_id=cihaz.id, version=1)

    assert geri.version == 3
    assert [s.version for s in svc.list_versions(db, cihaz.id)] == [3, 2, 1]
    assert parse(bytes(geri.raw)).get("2010C6").as_int() == 1440


# --- gosterim / fark -------------------------------------------------------
def test_diff_yalnizca_DEGISENI_gosterir(db) -> None:
    farklar = svc.diff(_dosya(1440, 10000), _dosya(720, 10000))
    assert len(farklar) == 1
    assert farklar[0]["cat_index"] == "2010C6"
    assert farklar[0]["before_int"] == 1440
    assert farklar[0]["after_int"] == 720


def test_describe_GOMULU_katalogla_ANLAMLI_ad_verir() -> None:
    """Cihazdan gelen CSV yalnizca `GROUP,INDEX` icerir, ADLARI TASIMAZ.

    Katalog olmadan arayuz "2010C6 = 1440" gostermek zorunda kalir ve kullanici
    hangi ayari degistirdigini bilemez — bu, yanlis ayar degistirmenin en kolay
    yoludur. Gomulu katalog (Explorer XML'inden cikarildi) bunu kapatir.
    """
    satirlar = {s["cat_index"]: s for s in svc.describe(_dosya())}
    assert satirlar["2010C6"]["meaning"] == "Dial -In Interval"
    assert satirlar["2010C6"]["unit"] == "min"
    assert satirlar["2010C6"]["value_int"] == 1440
    assert satirlar["320001"]["meaning"] == "Nominal Voltage"


def test_describe_katalog_BOSSA_da_calisir() -> None:
    """Katalog eksikligi dosyayi GORUNTULENEMEZ yapmamali.

    Adi bilinmeyen girdi ham CatIndex ile doner; SATIR GIZLENMEZ — gizlemek
    "bu ayar yok" izlenimi verirdi.
    """
    satirlar = svc.describe(_dosya(), catalog={})
    assert any(s["cat_index"] == "2010C6" and s["value_int"] == 1440 for s in satirlar)
    assert all(s["meaning"] is None for s in satirlar)


def test_gomulu_katalog_GERCEK_dosyanin_alanlarini_kapsiyor() -> None:
    """Katalog var ama pratikte kullanilan alanlari adlandiramiyorsa ise yaramaz.

    Olcu sentetik dosya UZERINDEN YAPILMAZ (3 satirlik bir ornekte oran
    anlamsizdir). Bunun yerine gercek cihaz dosyasinda (seri 50984) GECEN
    CatIndex'lerin katalogda bulunma orani sinanir; o dosyada 60 girdinin
    56'si adlandirilabiliyordu.
    """
    katalog = svc.builtin_catalog()
    assert len(katalog) > 100, "gomulu katalog beklenenden kucuk"

    # Gercek dosyada gecen, farkli gruplardan ornek CatIndex'ler.
    gercek_alanlar = [
        "381101", "3A0601", "305201", "370B01", "320501", "2010C6", "2010C0",
        "2010E0", "210701", "211101", "215801", "220501", "301001", "305002",
        "320001", "330101", "370101", "370601", "380001", "381001", "442601",
    ]
    eksik = [ci for ci in gercek_alanlar if ci not in katalog]
    assert not eksik, f"katalogda olmayan gercek alanlar: {eksik}"
