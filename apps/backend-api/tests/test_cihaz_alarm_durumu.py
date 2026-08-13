"""CIHAZ KARTI "Normal" DIYORDU, CIHAZDA ALARM VARKEN.

SORUN
-----
`devices.alarm_active` bir kolon olarak duruyordu ama HICBIR YERDE
yazilmiyordu — alarm acan yollarin hicbiri True yapmiyor, kapanan alarm da
False'a cekmiyordu. Yani her cihaz sonsuza kadar "alarm yok" diyordu.

Bunu okuyan her ekran yaniliyordu: haritadaki cihaz karti ("Normal / Aktif
alarm yok"), anasayfanin "alarmli" filtresi ve sayaci, ust arama, sebeke
yonetimi cipleri, cihaz ozeti KPI'i. Haritanin PIN rengi dogruydu cunku o,
alarm listesinden kendi kumesini cikariyordu — ayni ekranda pin kirmizi,
kart yesil oluyordu.

Cozum: alan cihaz serialize edilirken canli alarmdan doldurulur
(`device_kit_service.annotate`). Bu dosya olcutu korur.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

import app.models  # noqa: F401
from app.db.base import Base
from app.models.alarm import AlarmEvent
from app.models.device import Device
from app.services import device_kit_service


@pytest.fixture()
def db():
    eng = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(eng)
    Session = sessionmaker(bind=eng, autoflush=True)
    s = Session()
    try:
        yield s
    finally:
        s.close()
        eng.dispose()


def _cihaz(db, kod: str) -> Device:
    d = Device(code=kod, name=kod, ip_address=f"10.0.0.{len(kod)}",
               latitude=39.0, longitude=35.0)
    db.add(d)
    db.commit()
    return d


def _alarm(db, dev: Device, *, reset: bool = False, superseded: bool = False,
           produces_fault: bool = True) -> AlarmEvent:
    simdi = datetime.now(timezone.utc)
    a = AlarmEvent(
        device_id=dev.id,
        title="Asiri akim",
        description="esik asildi",
        level="critical",
        signal_key="master.overcurrent",
        kind="rule",
        produces_fault=produces_fault,
        reset=reset,
        reset_at=simdi if reset else None,
        superseded_at=simdi if superseded else None,
        created_at=simdi,
    )
    db.add(a)
    db.commit()
    return a


def test_ACIK_alarm_cihazi_alarmli_gosterir(db):
    """Asil regresyon: alarm varken kart "Normal" dememeli."""
    d = _cihaz(db, "DEMO-5")
    _alarm(db, d)

    (cikti,) = device_kit_service.annotate(db, [d])

    assert cikti.alarm_active is True


def test_alarm_YOKKEN_normal_kalir(db):
    d = _cihaz(db, "DEMO-6")

    (cikti,) = device_kit_service.annotate(db, [d])

    assert cikti.alarm_active is False


def test_NORMALE_DONMUS_alarm_cihazi_alarmli_YAPMAZ(db):
    """`reset` = cihaz normale dondu; kayit gecmiste kalir ama kart yesildir."""
    d = _cihaz(db, "DEMO-7")
    _alarm(db, d, reset=True)

    (cikti,) = device_kit_service.annotate(db, [d])

    assert cikti.alarm_active is False


def test_ARSIVLENMIS_alarm_cihazi_alarmli_YAPMAZ(db):
    """`superseded_at` dolu satir yerini yenisine birakmis bir arsiv kaydi;
    canli listelerden duser (bkz. AlarmEvent.superseded_at)."""
    d = _cihaz(db, "DEMO-8")
    _alarm(db, d, superseded=True)

    (cikti,) = device_kit_service.annotate(db, [d])

    assert cikti.alarm_active is False


def test_HABERLESME_alarmi_da_alarmdir(db):
    """Kartin metni "aktif alarm" — hat arizasi degil. `produces_fault=False`
    olan haberlesme alarmi da cihazda ACIK bir alarmdir."""
    d = _cihaz(db, "DEMO-9")
    _alarm(db, d, produces_fault=False)

    (cikti,) = device_kit_service.annotate(db, [d])

    assert cikti.alarm_active is True


def test_alarm_BASKA_cihaza_bulasmaz(db):
    """Sorgu tek DISTINCT ile tum alarmlari okuyor; eslestirme cihaz
    bazinda kalmali."""
    alarmli = _cihaz(db, "DEMO-10")
    temiz = _cihaz(db, "DEMO-11")
    _alarm(db, alarmli)

    cikti = device_kit_service.annotate(db, [alarmli, temiz])

    assert [c.alarm_active for c in cikti] == [True, False]


def test_KOLONDAKI_bayat_deger_EZILIR(db):
    """Kolon yillarca yazilmadi; elde kalmis yanlis bir deger okunmamali."""
    d = _cihaz(db, "DEMO-12")
    d.alarm_active = True  # bayat/yanlis
    db.commit()

    (cikti,) = device_kit_service.annotate(db, [d])

    assert cikti.alarm_active is False
