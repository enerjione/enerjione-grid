"""Ariza KAPATMA kurali.

Onceden her gecis serbestti: sahada devam eden bir ariza dogrudan `closed`
yapilabiliyor, ekrandan dusuyor ve kimse ilgilenmedigi halde kapali
gorunuyordu. Kapanma gerekcesi de hicbir yerde yazmiyordu.

Bu testler iki kurali kilitler:
  1. `closed`a gecis yalnizca ariza SAHADA DUZELDIYSE (`resolved_at` dolu).
  2. Kapatirken cozum notu ZORUNLU.
"""

from datetime import datetime, timezone

import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.db.base import Base
from app.models.enums import UserRole
from app.models.fault import FaultEvent
from app.schemas.fault import FaultEventStatusUpdate


@pytest.fixture()
def db():
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


class _Kullanici:
    username = "eng"
    role = UserRole.ENGINEER


def _ariza(db, **kw) -> FaultEvent:
    f = FaultEvent(
        line_id=1,
        region_id=1,
        last_red_device_id=1,
        from_pole_id=1,
        to_pole_id=2,
        from_pole_seq=1,
        to_pole_seq=2,
        opened_at=datetime.now(timezone.utc),
        status=kw.pop("status", "open"),
        **kw,
    )
    db.add(f)
    db.commit()
    db.refresh(f)
    return f


def _durum(db, fault_id: int, **payload):
    from app.api.faults import update_fault_status

    return update_fault_status(
        fault_id,
        FaultEventStatusUpdate(**payload),
        current_user=_Kullanici(),
        db=db,
    )


def test_ACIK_ariza_kapatilamaz(db):
    f = _ariza(db, status="open")
    with pytest.raises(HTTPException) as exc:
        _durum(db, f.id, status="closed", resolution_note="degistirildi")
    assert exc.value.status_code == 409


def test_devam_eden_ariza_da_kapatilamaz(db):
    f = _ariza(db, status="in_progress")
    with pytest.raises(HTTPException) as exc:
        _durum(db, f.id, status="closed", resolution_note="degistirildi")
    assert exc.value.status_code == 409


def test_duzelen_ariza_COZUM_NOTUSUZ_kapatilamaz(db):
    f = _ariza(db, status="resolved", resolved_at=datetime.now(timezone.utc))
    with pytest.raises(HTTPException) as exc:
        _durum(db, f.id, status="closed")
    assert exc.value.status_code == 400


def test_bosluktan_ibaret_not_kabul_edilmez(db):
    f = _ariza(db, status="resolved", resolved_at=datetime.now(timezone.utc))
    with pytest.raises(HTTPException) as exc:
        _durum(db, f.id, status="closed", resolution_note="   ")
    assert exc.value.status_code == 400


def test_duzelen_ariza_notla_KAPATILIR(db):
    f = _ariza(db, status="resolved", resolved_at=datetime.now(timezone.utc))
    _durum(db, f.id, status="closed", resolution_note="Izolator degistirildi")
    db.refresh(f)
    assert f.status == "closed"
    assert f.closed_at is not None
    assert f.resolution_note == "Izolator degistirildi"


def test_cozum_notu_SONRADAN_SILINMEZ(db):
    """Kapanis gerekcesi kalici: ikinci bir status cagrisi bos not
    gonderirse eskisi korunur."""
    f = _ariza(db, status="resolved", resolved_at=datetime.now(timezone.utc))
    _durum(db, f.id, status="closed", resolution_note="Izolator degistirildi")
    _durum(db, f.id, status="closed")
    db.refresh(f)
    assert f.resolution_note == "Izolator degistirildi"


def test_resolved_gecisi_serbest(db):
    """`resolved`a gecis kurala tabi degil — onu cihaz belirler."""
    f = _ariza(db, status="open")
    _durum(db, f.id, status="resolved")
    db.refresh(f)
    assert f.status == "resolved"
    assert f.resolved_at is not None
