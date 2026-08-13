"""Model bazli cihaz profili ayarlari (batarya esikleri).

Kritik nokta: esikler ONCEDEN proje genelinde tek cifttti. Bu testler
zincirin (model -> proje -> kod) her katmanini ve Pole Master Kit setinin
uc bataryali ozel durumunu kilitler.
"""

from datetime import datetime, timezone

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.db.base import Base
from app.data.device_models import DEFAULT_MODEL, PMK_SET_MODEL
from app.models.device import Device
from app.models.device_model_settings import DeviceModelSettings
from app.models.project_settings import ProjectSettings
from app.models.telemetry_latest import TelemetryLatest
from app.services import device_profile_service as dps


@pytest.fixture()
def db():
    # Tum model modullerini yukle — `create_all` yalnizca o ana kadar import
    # edilmis tablolari kurar.
    import importlib
    import pkgutil

    import app.models

    for m in pkgutil.iter_modules(app.models.__path__):
        importlib.import_module(f"app.models.{m.name}")

    eng = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(eng)
    s = sessionmaker(bind=eng, autoflush=True)()
    try:
        yield s
    finally:
        s.close()
        eng.dispose()


@pytest.fixture(autouse=True)
def _cache_temizle():
    # Esik cache'i modul seviyesindedir; testler arasinda sizmamali.
    dps.invalidate_cache()
    yield
    dps.invalidate_cache()


class _Kullanici:
    username = "installer"


def _cihaz(db, code: str, model: str) -> Device:
    d = Device(
        code=code,
        name=code,
        model=model,
        ip_address="10.0.0.1",
        latitude=40.0,
        longitude=29.0,
    )
    db.add(d)
    db.commit()
    db.refresh(d)
    return d


def test_kayit_yoksa_kod_varsayilani(db):
    low, full = dps.battery_thresholds(db, DEFAULT_MODEL)
    assert (low, full) == (dps.DEFAULT_BATTERY_VOLTAGE_LOW, dps.DEFAULT_BATTERY_VOLTAGE_FULL)


def test_proje_ayari_modele_miras_kalir(db):
    db.add(ProjectSettings(id=1, battery_voltage_low=3.0, battery_voltage_full=4.0))
    db.commit()
    low, full = dps.battery_thresholds(db, DEFAULT_MODEL)
    assert (low, full) == (3.0, 4.0)


def test_model_ayari_proje_ayarini_EZER(db):
    db.add(ProjectSettings(id=1, battery_voltage_low=3.0, battery_voltage_full=4.0))
    db.add(
        DeviceModelSettings(
            model=PMK_SET_MODEL, battery_voltage_low=2.5, battery_voltage_full=3.2
        )
    )
    db.commit()
    # Kit seti kendi esigini kullanir...
    assert dps.battery_thresholds(db, PMK_SET_MODEL) == (2.5, 3.2)
    # ...SN 2.0 proje ayarindan devam eder. Iki model AYNI ANDA farkli esik.
    assert dps.battery_thresholds(db, DEFAULT_MODEL) == (3.0, 4.0)


def test_KISMI_doldurma_ust_katmandan_tamamlanir(db):
    db.add(ProjectSettings(id=1, battery_voltage_low=3.0, battery_voltage_full=4.0))
    db.add(DeviceModelSettings(model=PMK_SET_MODEL, battery_voltage_low=2.5))
    db.commit()
    # low modelden, full projeden.
    assert dps.battery_thresholds(db, PMK_SET_MODEL) == (2.5, 4.0)


def test_ters_aralik_koda_geri_duser(db):
    db.add(
        DeviceModelSettings(
            model=PMK_SET_MODEL, battery_voltage_low=4.0, battery_voltage_full=3.0
        )
    )
    db.commit()
    assert dps.battery_thresholds(db, PMK_SET_MODEL) == (
        dps.DEFAULT_BATTERY_VOLTAGE_LOW,
        dps.DEFAULT_BATTERY_VOLTAGE_FULL,
    )


def test_sn2_bataryasi_master_unitesinden(db):
    d = _cihaz(db, "SN-1", DEFAULT_MODEL)
    yuzde = dps.battery_percent_for_device(
        db, d.id, DEFAULT_MODEL, "master.battery_voltage_satellite", 3.71
    )
    assert yuzde == 100.0
    # Uydu bataryasi SN 2.0'da cihaz yuzdesini SURUKLEMEZ.
    assert (
        dps.battery_percent_for_device(
            db, d.id, DEFAULT_MODEL, "sat01.battery_voltage_satellite", 3.40
        )
        is None
    )


def test_kit_setinde_uydu_bataryasi_ARTIK_islenir(db):
    """Onceden yalnizca `master.*` kabul ediliyordu; kit setinde master YOK,
    bu yuzden setlerin batarya yuzdesi hic hesaplanmiyordu."""
    d = _cihaz(db, "PMK-S1", PMK_SET_MODEL)
    yuzde = dps.battery_percent_for_device(
        db, d.id, PMK_SET_MODEL, "sat01.battery_voltage_satellite", 3.71
    )
    assert yuzde == 100.0


def test_kit_setinde_EN_DUSUK_batarya_esas_alinir(db):
    d = _cihaz(db, "PMK-S2", PMK_SET_MODEL)
    simdi = datetime.now(timezone.utc)
    for key, deger in (
        ("sat02.battery_voltage_satellite", 3.40),  # bos
        ("sat03.battery_voltage_satellite", 3.71),  # dolu
    ):
        db.add(
            TelemetryLatest(
                device_id=d.id,
                signal_key=key,
                value=deger,
                quality="good",
                source_timestamp=simdi,
                updated_at=simdi,
            )
        )
    db.commit()
    # Gelen sat01 dolu olsa bile sat02 bos: yuzde EN DUSUKten cikar.
    yuzde = dps.battery_percent_for_device(
        db, d.id, PMK_SET_MODEL, "sat01.battery_voltage_satellite", 3.71
    )
    assert yuzde == 0.0


def test_api_ayar_yazar_ve_cozer(db):
    from app.api.device_models import update_device_model_settings
    from app.schemas.device_model_settings import DeviceModelSettingsUpdate

    body = update_device_model_settings(
        PMK_SET_MODEL,
        DeviceModelSettingsUpdate(battery_voltage_low=2.5, battery_voltage_full=3.2),
        current_user=_Kullanici(),
        db=db,
    )
    assert body["battery_voltage_low"] == 2.5
    assert body["resolved_battery_voltage_full"] == 3.2
    assert body["battery_units"] == ["sat01", "sat02", "sat03"]


def test_api_ters_aralik_REDDEDILIR():
    from pydantic import ValidationError

    from app.schemas.device_model_settings import DeviceModelSettingsUpdate

    with pytest.raises(ValidationError):
        DeviceModelSettingsUpdate(battery_voltage_low=4.0, battery_voltage_full=3.0)


def test_api_bilinmeyen_model_404(db):
    from fastapi import HTTPException

    from app.api.device_models import update_device_model_settings
    from app.schemas.device_model_settings import DeviceModelSettingsUpdate

    with pytest.raises(HTTPException) as exc:
        update_device_model_settings(
            "olmayan_model",
            DeviceModelSettingsUpdate(battery_voltage_low=3.0),
            current_user=_Kullanici(),
            db=db,
        )
    assert exc.value.status_code == 404


def test_api_liste_tum_modelleri_doner(db):
    from app.api.device_models import list_device_model_settings

    satirlar = list_device_model_settings(_=_Kullanici(), db=db)
    kodlar = {item["model"] for item in satirlar}
    assert DEFAULT_MODEL in kodlar
    for item in satirlar:
        # Kayit olmasa bile cozulmus deger HER ZAMAN dolu gelir.
        assert item["resolved_battery_voltage_low"] is not None
        assert item["resolved_battery_voltage_full"] is not None


# ---------------------------------------------------------------------------
# UYDU HUCRESI AYRI OLCULUR
#
# Uydunun bataryasi RTU'yu besleyen master hucresiyle ayni voltaj araliginda
# calismaz. Proje ayarinda tek cift esik vardi ve uydular da onunla
# olculuyordu: sahada saglam uydular ekranda SURKELI %0 gorunuyordu (olculen
# ~3,05 V, master esigi 3,40 V). Sessiz bir yanlislik — ne hata ne uyari
# uretir, ustelik gercekten biten bir hucreyi de gizler.
# ---------------------------------------------------------------------------


def test_uydu_cifti_YALNIZCA_uydu_unitesine_uygulanir(db):
    db.add(
        ProjectSettings(
            id=1,
            battery_voltage_low=3.40,
            battery_voltage_full=3.71,
            battery_voltage_low_sat=2.90,
            battery_voltage_full_sat=3.30,
        )
    )
    db.commit()
    assert dps.battery_thresholds(db, DEFAULT_MODEL, unit="master") == (3.40, 3.71)
    for unite in ("sat01", "sat02", "sat03"):
        assert dps.battery_thresholds(db, DEFAULT_MODEL, unit=unite) == (2.90, 3.30)


def test_uydu_cifti_BOSSA_master_esigi_kullanilir(db):
    # Guncelleyen kurulumda alanlar bos gelir; davranis aynen korunmali.
    db.add(ProjectSettings(id=1, battery_voltage_low=3.0, battery_voltage_full=4.0))
    db.commit()
    assert dps.battery_thresholds(db, DEFAULT_MODEL, unit="sat01") == (3.0, 4.0)


def test_uydu_esigi_yuzdeyi_DUZELTIR(db):
    # Sahadan gelen gercek deger: 3,05 V. Master esigiyle %0, uydu esigiyle
    # gercekci bir sayi. Eskiden bu cihazlar surekli "bitmis" gorunuyordu.
    db.add(
        ProjectSettings(
            id=1,
            battery_voltage_low=3.40,
            battery_voltage_full=3.71,
            battery_voltage_low_sat=2.90,
            battery_voltage_full_sat=3.30,
        )
    )
    db.commit()
    d = _cihaz(db, "PMK-SET-1", PMK_SET_MODEL)
    yuzde = dps.battery_percent_for_device(
        db, d.id, PMK_SET_MODEL, "sat01.battery_voltage_satellite", 3.05
    )
    assert yuzde is not None and yuzde > 0, "uydu hala %0 gosteriyor"
    # (3.05 - 2.90) / (3.30 - 2.90) = %37,5
    assert abs(yuzde - 37.5) < 0.1


def test_setin_yuzdesi_EN_ZAYIF_unite_YUZDESIDIR(db):
    # Uniteler farkli araliklarda olabilir; karsilastirma ham voltaj uzerinden
    # yapilirsa yanlis unite "en zayif" secilir.
    db.add(
        ProjectSettings(
            id=1,
            battery_voltage_low=3.40,
            battery_voltage_full=3.71,
            battery_voltage_low_sat=2.90,
            battery_voltage_full_sat=3.30,
        )
    )
    db.commit()
    d = _cihaz(db, "PMK-SET-2", PMK_SET_MODEL)
    for anahtar, deger in (
        ("sat02.battery_voltage_satellite", 3.30),  # %100
        ("sat03.battery_voltage_satellite", 2.98),  # %20
    ):
        db.add(
            TelemetryLatest(
                device_id=d.id,
                signal_key=anahtar,
                value=deger,
                quality='good',
                source_timestamp=datetime.now(timezone.utc),
                updated_at=datetime.now(timezone.utc),
            )
        )
    db.commit()
    yuzde = dps.battery_percent_for_device(
        db, d.id, PMK_SET_MODEL, "sat01.battery_voltage_satellite", 3.20
    )
    assert yuzde is not None and abs(yuzde - 20.0) < 0.1, f"en zayif unite alinmadi: {yuzde}"


def test_model_ayari_uydu_ciftini_de_EZER(db):
    # Zincir bozulmasin: modele ozel bir esik girildiyse (o donanimin gercegi)
    # proje uydu cifti onun yerine gecmez.
    db.add(
        ProjectSettings(
            id=1,
            battery_voltage_low=3.40,
            battery_voltage_full=3.71,
            battery_voltage_low_sat=2.90,
            battery_voltage_full_sat=3.30,
        )
    )
    db.add(
        DeviceModelSettings(
            model=PMK_SET_MODEL, battery_voltage_low=2.50, battery_voltage_full=3.20
        )
    )
    db.commit()
    assert dps.battery_thresholds(db, PMK_SET_MODEL, unit="sat01") == (2.50, 3.20)


def test_api_cevabinda_uydu_cifti_de_gelir(db):
    # Arayuz ayni yuzdeyi hesaplayabilmeli; yoksa backend bir, ekran baska
    # sayi gosterir.
    from app.api.device_models import list_device_model_settings

    db.add(
        ProjectSettings(
            id=1,
            battery_voltage_low=3.40,
            battery_voltage_full=3.71,
            battery_voltage_low_sat=2.90,
            battery_voltage_full_sat=3.30,
        )
    )
    db.commit()
    satirlar = list_device_model_settings(_=_Kullanici(), db=db)
    for item in satirlar:
        assert item["resolved_battery_voltage_low_sat"] == 2.90
        assert item["resolved_battery_voltage_full_sat"] == 3.30


def test_uydu_unitesi_TANIMI_dar(db):
    # "sat" oneki uydu demek; master ya da bos deger uydu SAYILMAZ, aksi halde
    # master hucresi uydu esigiyle olculur ve bu sessizce yanlis olur.
    assert dps.is_satellite_unit("sat01") is True
    assert dps.is_satellite_unit("SAT03") is True
    assert dps.is_satellite_unit("master") is False
    assert dps.is_satellite_unit("") is False
    assert dps.is_satellite_unit(None) is False
