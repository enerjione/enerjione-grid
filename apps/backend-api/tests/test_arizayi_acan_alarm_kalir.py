"""ARIZAYI ACAN ALARM, ALARM NORMALE DONSE DE KARTTA KALIR.

SORUN
-----
`GET /faults` arizayi acan alarmlari yalnizca `reset=False` (yani SU AN
acik) olanlardan topluyordu. Horstmann SN2 arizayi gorup kirmizi yandiktan
kisa sure sonra sinyali normale dondurur; ariza kaydi hala ACIK dururken
alarm sifirlanir ve `trigger_alarms` BOSALIRDI.

Sonuc: operator hala ekranda duran bir arizaya bakarken "Arizayi acan
alarm" bloğunda "arizayi acan alarm normale donmus" yazisindan baska bir
sey goremiyordu. Oysa sorulan soru "su an alarm var mi" degil, "bu arizayi
NE acti" — ve o cevap alarm sifirlaninca degismez.

Bu dosya iki seyi birden korur:
  1. Sifirlanan alarm listede KALIR ve `reset` bayragiyla gelir.
  2. Pencere disindaki (baska bir arizaya ait) eski alarm SIZMAZ — filtre
     kalkarken cihazin tum alarm gecmisi kartta birikmemeli.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

import app.models  # noqa: F401
from app.api.faults import list_faults
from app.db.base import Base
from app.models.alarm import AlarmEvent
from app.models.device import Device
from app.models.enums import UserRole
from app.models.fault import FaultEvent
from app.models.grid_topology import Line, LineSegment, Pole, Region
from app.models.user import User
from app.services.fault_recompute_service import recompute_faults


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


@pytest.fixture()
def saha(db):
    """Tek hat, 3 direk, ilk aciklikta bir cihaz."""
    r = Region(name="Merkez", code="MRK")
    db.add(r)
    db.flush()

    hat = Line(name="ANA HAT", code="ANA", region_id=r.id)
    db.add(hat)
    db.flush()
    direkler = []
    for i in range(1, 4):
        p = Pole(line_id=hat.id, sequence_no=i, latitude=39.0 + i * 0.01, longitude=35.0)
        db.add(p)
        direkler.append(p)
    db.flush()

    d = Device(code="SN2-1", name="SN2-1", ip_address="10.0.0.1",
               latitude=39.0, longitude=35.0)
    db.add(d)
    db.flush()
    db.add(LineSegment(line_id=hat.id, from_pole_id=direkler[0].id,
                       to_pole_id=direkler[1].id, device_id=d.id))
    db.add(LineSegment(line_id=hat.id, from_pole_id=direkler[1].id,
                       to_pole_id=direkler[2].id))
    db.flush()

    u = User(username="muh", email="m@f.com", full_name="Muh",
             hashed_password="x", role=UserRole.ENGINEER)
    db.add(u)
    db.flush()
    return {"hat": hat, "cihaz": d, "user": u}


def _alarm(db, dev: Device, *, baslik: str, ne_zaman: datetime,
           reset: bool = False) -> AlarmEvent:
    a = AlarmEvent(device_id=dev.id, title=baslik, description="esik asildi",
                   level="critical", signal_key="master.overcurrent", kind="rule",
                   produces_fault=True, reset=reset,
                   reset_at=ne_zaman + timedelta(minutes=1) if reset else None,
                   created_at=ne_zaman)
    db.add(a)
    db.flush()
    return a


def _okunan(db, user):
    """Uc yeni acilan arizalari `fault_display_delay_sec` dolana kadar gizler;
    bu dosya gecikmeyi degil alarm eslesmesini olcuyor, kaydi olgunlastiriyoruz."""
    eski = datetime.now(timezone.utc) - timedelta(hours=1)
    for f in db.query(FaultEvent).all():
        f.opened_at = eski
    db.flush()
    return list_faults(status_filter="active", current_user=user, db=db)


def test_normale_DONEN_alarm_arizanin_kartinda_KALIR(db, saha):
    """Asil regresyon: alarm sifirlaninca blok bosalmamali."""
    simdi = datetime.now(timezone.utc)
    _alarm(db, saha["cihaz"], baslik="Asiri akim", ne_zaman=simdi)
    recompute_faults(db)

    # Cihaz normale dondu — ariza kaydi HALA acik.
    for a in db.query(AlarmEvent).all():
        a.reset = True
        a.reset_at = simdi + timedelta(minutes=2)
    db.flush()

    (kayit,) = _okunan(db, saha["user"])
    assert kayit.status != "closed"
    assert [a.title for a in kayit.trigger_alarms] == ["Asiri akim"]
    assert kayit.trigger_alarms[0].reset is True
    assert kayit.trigger_alarms[0].reset_at is not None


def test_acik_alarm_da_gelmeye_DEVAM_eder(db, saha):
    """Duzeltme eski davranisi bozmasin: sifirlanmamis alarm hala geliyor."""
    _alarm(db, saha["cihaz"], baslik="Asiri akim",
           ne_zaman=datetime.now(timezone.utc))
    recompute_faults(db)

    (kayit,) = _okunan(db, saha["user"])
    assert [a.title for a in kayit.trigger_alarms] == ["Asiri akim"]
    assert kayit.trigger_alarms[0].reset is False


def test_ESKI_arizanin_alarmi_yeni_kayda_SIZMAZ(db, saha):
    """`reset` filtresi kalkinca cihazin tum gecmisi kartta birikmemeli.

    Ayni cihaz aylar icinde defalarca arizalanir. Pencere disinda kalan
    (bir onceki arizaya ait) alarm bu kaydin cevabi degildir.
    """
    simdi = datetime.now(timezone.utc)
    _alarm(db, saha["cihaz"], baslik="GECEN AYKI ariza",
           ne_zaman=simdi - timedelta(days=30), reset=True)
    _alarm(db, saha["cihaz"], baslik="Asiri akim", ne_zaman=simdi)
    recompute_faults(db)

    (kayit,) = _okunan(db, saha["user"])
    basliklar = [a.title for a in kayit.trigger_alarms]
    assert "GECEN AYKI ariza" not in basliklar
    assert basliklar == ["Asiri akim"]
