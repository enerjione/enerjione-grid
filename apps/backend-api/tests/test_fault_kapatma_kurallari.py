"""Ariza kapatma ve KAPATILMIS KAYDIN dokunulmazligi.

Iki kural bu dosyada kilitleniyor:

1. ARIZA SAHADA DUZELMEDEN KAPATILAMAZ. `resolved_at`i cihaz yazar (alarm
   kalkinca otomatik); kullanicinin isi duzelen arizayi raporlayip
   kapatmaktir. Acik bir arizanin kapatilabilmesi, sahada devam eden isin
   ekrandan dusmesi ve kimse ilgilenmedigi halde "kapali" gorunmesi demekti.

2. KAPATILMIS KAYIT SALT OKUNURDUR. Yorum eklemek ya da kisa notu duzeltmek,
   arsivlenmis kapanis raporunun sonradan sessizce degismesi olurdu; raporu
   okuyan kisi bu degisimden haberdar olmaz.

Ikisi de SESSIZ hata sinifina girer: hicbir yer patlamaz, yalnizca kayit
gercekle ortusmez. O yuzden testle sabitleniyor.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

import app.models  # noqa: F401
from app.api.faults import (
    create_fault_comment,
    list_fault_comments,
    update_fault_note,
    update_fault_status,
)
from app.db.base import Base
from app.models.device import Device
from app.models.enums import UserRole
from app.models.fault import FaultComment, FaultEvent
from app.models.grid_topology import Line, Pole, Region
from app.models.user import User
from app.schemas.fault import (
    FaultCommentCreate,
    FaultEventNoteUpdate,
    FaultEventStatusUpdate,
)


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


def _kullanici(role=UserRole.ENGINEER, username="muh") -> User:
    return User(
        username=username,
        email=f"{username}@f.com",
        full_name="Muh",
        hashed_password="x",
        role=role,
    )


def _ariza(db, **kw) -> FaultEvent:
    region = Region(name="Merkez", code="MRK")
    db.add(region)
    db.flush()
    line = Line(name="HAT-1", code="HAT-1", region_id=region.id)
    db.add(line)
    db.flush()
    dev = Device(code="SN2-1", name="F1", ip_address="10.0.0.5", latitude=39.0, longitude=35.0)
    db.add(dev)
    db.flush()
    p1 = Pole(line_id=line.id, sequence_no=1, latitude=39.0, longitude=35.0)
    p2 = Pole(line_id=line.id, sequence_no=2, latitude=39.1, longitude=35.1)
    db.add_all([p1, p2])
    db.flush()
    kw.setdefault("status", "open")
    f = FaultEvent(
        line_id=line.id,
        region_id=region.id,
        last_red_device_id=dev.id,
        from_pole_id=p1.id,
        to_pole_id=p2.id,
        opened_at=datetime.now(timezone.utc) - timedelta(hours=2),
        **kw,
    )
    db.add(f)
    db.flush()
    return f


# ---- 1) Kapatma normale donuse bagli --------------------------------------

def test_NORMALE_DONMEDEN_kapatilamaz(db):
    """Acik ariza dogrudan `closed` yapilamaz — cozum notu yazilmis olsa bile."""
    f = _ariza(db)  # resolved_at yok
    with pytest.raises(HTTPException) as exc:
        update_fault_status(
            f.id,
            FaultEventStatusUpdate(status="closed", resolution_note="izolator degisti"),
            current_user=_kullanici(),
            db=db,
        )
    assert exc.value.status_code == 409
    db.refresh(f)
    assert f.status == "open", "reddedilen istek kaydi degistirmis"


def test_normale_donmus_ariza_COZUM_NOTU_OLMADAN_kapatilamaz(db):
    f = _ariza(db, status="resolved", resolved_at=datetime.now(timezone.utc))
    with pytest.raises(HTTPException) as exc:
        update_fault_status(
            f.id,
            FaultEventStatusUpdate(status="closed", resolution_note="   "),
            current_user=_kullanici(),
            db=db,
        )
    assert exc.value.status_code == 400
    db.refresh(f)
    assert f.status == "resolved"


def test_normale_donmus_ariza_cozum_notuyla_KAPANIR(db):
    donus = datetime.now(timezone.utc) - timedelta(minutes=10)
    f = _ariza(db, status="resolved", resolved_at=donus)
    update_fault_status(
        f.id,
        FaultEventStatusUpdate(status="closed", resolution_note="  izolator degisti  "),
        current_user=_kullanici(),
        db=db,
    )
    db.refresh(f)
    assert f.status == "closed"
    assert f.resolution_note == "izolator degisti", "not kirpilmadan yazilmis"
    assert f.closed_at is not None


# ---- 2) Kapatilmis kayit salt okunur ---------------------------------------

def test_kapatilmis_arizaya_YORUM_EKLENEMEZ(db):
    f = _ariza(
        db,
        status="closed",
        resolved_at=datetime.now(timezone.utc),
        closed_at=datetime.now(timezone.utc),
    )
    with pytest.raises(HTTPException) as exc:
        create_fault_comment(
            f.id,
            FaultCommentCreate(body="sonradan eklenen yorum"),
            current_user=_kullanici(),
            db=db,
        )
    assert exc.value.status_code == 409
    assert db.scalars(select(FaultComment).where(FaultComment.fault_id == f.id)).first() is None


def test_kapatilmis_arizanin_KISA_NOTU_degistirilemez(db):
    f = _ariza(
        db,
        status="closed",
        note="orijinal not",
        resolved_at=datetime.now(timezone.utc),
        closed_at=datetime.now(timezone.utc),
    )
    with pytest.raises(HTTPException) as exc:
        update_fault_note(
            f.id,
            FaultEventNoteUpdate(note="sonradan degistirildi"),
            current_user=_kullanici(),
            db=db,
        )
    assert exc.value.status_code == 409
    db.refresh(f)
    assert f.note == "orijinal not"


def test_kapatilmis_arizanin_ESKI_YORUMLARI_OKUNABILIR(db):
    """Yalnizca YAZMA kapali. Kapanis raporu okunamazsa arsivin anlami kalmaz."""
    f = _ariza(db, status="open")
    create_fault_comment(
        f.id, FaultCommentCreate(body="direge cikildi"), current_user=_kullanici(), db=db
    )
    f.status = "closed"
    f.resolved_at = datetime.now(timezone.utc)
    f.closed_at = datetime.now(timezone.utc)
    db.flush()

    rows = list_fault_comments(f.id, current_user=_kullanici(), db=db)
    assert [r.body for r in rows] == ["direge cikildi"]


# ---- Acik ariza normal calismaya devam eder --------------------------------

def test_ACIK_arizada_yorum_ve_not_hala_yazilabilir(db):
    """Kilit yalnizca `closed` icin; aksi halde saha ekibi calisamaz."""
    f = _ariza(db, status="in_progress")
    create_fault_comment(
        f.id, FaultCommentCreate(body="parca degisti"), current_user=_kullanici(), db=db
    )
    update_fault_note(
        f.id, FaultEventNoteUpdate(note="dal temasi suphesi"), current_user=_kullanici(), db=db
    )
    db.refresh(f)
    assert f.note == "dal temasi suphesi"
    assert db.scalars(select(FaultComment).where(FaultComment.fault_id == f.id)).first() is not None
