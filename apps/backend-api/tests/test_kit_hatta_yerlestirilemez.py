"""Pole Master Kit'in KENDISI bir hat acikligina baglanamaz.

NEDEN
-----
Kit FIZIKSEL kayittir: DNP3 baglantisini (IP, port, master adresi), gateway
bagini ve seri numarasini O tasir. Sahada bir yer kaplamaz. Yer kaplayan,
arizasi dusen ve kendi detay sayfasi olan sey onun SETLERIDIR
(`horstmann_pmk_set`) — tasarim notunda da boyle yaziyor: "kullanicinin
gordugu, hatta yerlestirdigi kayit budur".

Kit bir acikliga baglanirsa uc setin ucu birden tek bir direk araligina
cakilir: uc ayri olcum noktasi tek noktaya duser ve ariza yeri hesabi
anlamsizlasir. Ustelik bu SESSIZ bir bozulmadur — harita bir sey cizmeye
devam eder.

KURAL UCTA DA VAR, yalnizca arayuzde gizlemek yetmez: API'yi dogrudan
kullanan ya da eski bir arayuz surumu calistiran biri kiti yine baglardi.
Bu dosya ucu SURUYOR.
"""

from __future__ import annotations

import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

import app.models  # noqa: F401
from app.api.grid_topology import create_segment, update_segment
from app.data.device_models import PMK_SET_MODEL, POLE_MASTER_KIT_MODEL
from app.db.base import Base
from app.models.device import Device
from app.models.enums import UserRole
from app.models.grid_topology import Line, LineSegment, Pole, Region
from app.models.user import User
from app.schemas.grid_topology import LineSegmentCreate, LineSegmentUpdate


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
    r = Region(name="Merkez", code="MRK")
    db.add(r)
    db.flush()
    ln = Line(name="ANA HAT", code="ANA", region_id=r.id)
    db.add(ln)
    db.flush()
    p1 = Pole(line_id=ln.id, sequence_no=1, latitude=39.0, longitude=35.0)
    p2 = Pole(line_id=ln.id, sequence_no=2, latitude=39.1, longitude=35.1)
    db.add_all([p1, p2])
    db.flush()

    kit = Device(code="a", name="a", ip_address="10.0.0.5",
                 model=POLE_MASTER_KIT_MODEL, latitude=39.0, longitude=35.0)
    db.add(kit)
    db.flush()
    set1 = Device(code="a-S1", name="a / Set 1", ip_address="10.0.0.5",
                  model=PMK_SET_MODEL, parent_device_id=kit.id, subunit_index=1,
                  latitude=39.0, longitude=35.0)
    db.add(set1)
    db.flush()

    u = User(username="kur", email="k@f.com", full_name="Kurulumcu",
             hashed_password="x", role=UserRole.INSTALLER)
    db.add(u)
    db.flush()
    return {"line": ln, "p1": p1, "p2": p2, "kit": kit, "set1": set1, "user": u}


def test_kit_yeni_segmente_BAGLANAMAZ(db, saha):
    with pytest.raises(HTTPException) as exc:
        create_segment(
            payload=LineSegmentCreate(
                line_id=saha["line"].id,
                from_pole_id=saha["p1"].id,
                to_pole_id=saha["p2"].id,
                device_id=saha["kit"].id,
            ),
            current_user=saha["user"],
            db=db,
        )
    assert exc.value.status_code == 400
    # Mesaj kullaniciya NE YAPACAGINI soylemeli; "gecersiz" demek yetmez.
    assert "set" in str(exc.value.detail).lower()
    assert db.query(LineSegment).count() == 0, "reddedildi ama segment yaratilmis"


def test_SET_baglanabilir(db, saha):
    """Kapinin dar oldugunu dogrula: yasak kite, sete degil. Set
    baglanamasaydi kit ozelligi tamamen kullanilamaz olurdu."""
    row = create_segment(
        payload=LineSegmentCreate(
            line_id=saha["line"].id,
            from_pole_id=saha["p1"].id,
            to_pole_id=saha["p2"].id,
            device_id=saha["set1"].id,
        ),
        current_user=saha["user"],
        db=db,
    )
    assert row.device_id == saha["set1"].id


def test_var_olan_segment_kite_GUNCELLENEMEZ(db, saha):
    """Yaratma kapisini kapatip guncelleme kapisini acik birakmak, kurali
    bir adim oteye tasimaktan ibaret olurdu: bos segment yarat, sonra kiti
    ata."""
    bos = create_segment(
        payload=LineSegmentCreate(
            line_id=saha["line"].id,
            from_pole_id=saha["p1"].id,
            to_pole_id=saha["p2"].id,
        ),
        current_user=saha["user"],
        db=db,
    )
    with pytest.raises(HTTPException) as exc:
        update_segment(
            segment_id=bos.id,
            payload=LineSegmentUpdate(device_id=saha["kit"].id),
            current_user=saha["user"],
            db=db,
        )
    assert exc.value.status_code == 400
    db.expire_all()
    assert db.get(LineSegment, bos.id).device_id is None, "reddedildi ama cihaz yazilmis"


def test_SIRADAN_cihaz_etkilenmez(db, saha):
    """Kural yalnizca kit modelini hedefler; normal SN2 cihazlari her zamanki
    gibi baglanir."""
    sn2 = Device(code="SN2-1", name="SN2-1", ip_address="10.0.0.9",
                 latitude=39.0, longitude=35.0)
    db.add(sn2)
    db.flush()
    row = create_segment(
        payload=LineSegmentCreate(
            line_id=saha["line"].id,
            from_pole_id=saha["p1"].id,
            to_pole_id=saha["p2"].id,
            device_id=sn2.id,
        ),
        current_user=saha["user"],
        db=db,
    )
    assert row.device_id == sn2.id
