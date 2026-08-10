"""Ariza sebebi ucu — analiz katmanina giren tek INSAN etiketi.

Bu uctan gecen her deger yillarca saklanacak ve "en sik ariza sebebi"
raporunu uretecek. Bozuk bir etiket sessizdir: hicbir yer patlamaz, sadece
istatistik yanlis birikir. O yuzden dogrulama ve ayrisma kurallari burada
kilitleniyor.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

import app.models  # noqa: F401
from app.api.faults import list_fault_causes, update_fault_cause
from app.data.fault_causes import CAUSE_CODES, FAULT_CAUSES
from app.db.base import Base
from app.models.device import Device
from app.models.enums import UserRole
from app.models.fault import FaultEvent
from app.models.grid_topology import Line, Pole, Region
from app.models.user import User
from app.schemas.fault import FaultCauseUpdate


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
    u = User(
        username=username, email=f"{username}@f.com", full_name="Muh",
        hashed_password="x", role=role,
    )
    return u


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
    f = FaultEvent(
        line_id=line.id, region_id=region.id, last_red_device_id=dev.id,
        from_pole_id=p1.id, to_pole_id=p2.id, status="open",
        opened_at=datetime.now(timezone.utc), **kw,
    )
    db.add(f)
    db.flush()
    return f


# ---- Katalog ucu -----------------------------------------------------------

def test_katalog_ucu_TEK_KAYNAKTAN_besleniyor():
    """Frontend'e ayri liste gomulseydi ikisi zamanla ayrisirdi."""
    payload = list_fault_causes()
    assert len(payload["causes"]) == len(FAULT_CAUSES)
    assert {c["code"] for c in payload["causes"]} == CAUSE_CODES
    for c in payload["causes"]:
        assert c["label_tr"] and c["label_en"] and c["group"]
    assert "transient" in {k["code"] for k in payload["kinds"]}


# ---- Sebep yazma -----------------------------------------------------------

def test_sebep_kaydedilir(db):
    f = _ariza(db)
    update_fault_cause(
        f.id,
        FaultCauseUpdate(cause_code="tree_contact", cause_detail="3. direkte dal"),
        current_user=_kullanici(),
        db=db,
    )
    db.refresh(f)
    assert f.cause_code == "tree_contact"
    assert f.cause_detail == "3. direkte dal"


def test_KATALOG_DISI_kod_reddedilir(db):
    """Sessizce kabul etmek, hicbir gruba dusmeyen olu etiket biriktirirdi."""
    f = _ariza(db)
    with pytest.raises(HTTPException) as exc:
        update_fault_cause(
            f.id, FaultCauseUpdate(cause_code="uydurma_kod"),
            current_user=_kullanici(), db=db,
        )
    assert exc.value.status_code == 400


def test_gecersiz_tur_ve_faz_reddedilir(db):
    f = _ariza(db)
    for alan, deger in (("fault_kind", "belki"), ("phase", "z")):
        with pytest.raises(HTTPException) as exc:
            update_fault_cause(
                f.id, FaultCauseUpdate(**{alan: deger}),
                current_user=_kullanici(), db=db,
            )
        assert exc.value.status_code == 400, alan


def test_sebep_GERI_ALINABILIR(db):
    """Yanlis secildiyse temizlenebilmeli."""
    f = _ariza(db, cause_code="animal")
    update_fault_cause(
        f.id, FaultCauseUpdate(cause_code=None), current_user=_kullanici(), db=db
    )
    db.refresh(f)
    assert f.cause_code is None


def test_KURAL_ONERISI_ezilmez(db):
    """`auto_cause_code` ayri kolonda durur — ikisini karsilastirmak
    kurallarin isabetini olcmeyi mumkun kilan tek sey."""
    f = _ariza(db, auto_cause_code="tree_contact")
    update_fault_cause(
        f.id, FaultCauseUpdate(cause_code="animal"), current_user=_kullanici(), db=db
    )
    db.refresh(f)
    assert f.cause_code == "animal", "insan etiketi kazanmali"
    assert f.auto_cause_code == "tree_contact", "kural onerisi silinmis"


def test_tur_GONDERILMEZSE_cihaz_verisi_KORUNUR(db):
    """Bos gonderimi 'sil' saymak, elle duzeltmeyen bir kaydin cihazdan
    gelen bilgisini silmesine yol acardi."""
    f = _ariza(db, fault_kind="permanent", phase="abc")
    update_fault_cause(
        f.id, FaultCauseUpdate(cause_code="lightning"), current_user=_kullanici(), db=db
    )
    db.refresh(f)
    assert f.fault_kind == "permanent"
    assert f.phase == "abc"


def test_tur_GONDERILIRSE_elle_duzeltilir(db):
    f = _ariza(db, fault_kind="transient")
    update_fault_cause(
        f.id, FaultCauseUpdate(fault_kind="permanent", phase="AB"),
        current_user=_kullanici(), db=db,
    )
    db.refresh(f)
    assert f.fault_kind == "permanent"
    assert f.phase == "ab", "faz kucuk harfe normalize edilmeli"


# ---- Yetki -----------------------------------------------------------------

def test_operator_KENDINE_ATANMAMIS_arizaya_yazamaz(db):
    f = _ariza(db, assigned_to_username="baskasi")
    with pytest.raises(HTTPException) as exc:
        update_fault_cause(
            f.id, FaultCauseUpdate(cause_code="animal"),
            current_user=_kullanici(UserRole.OPERATOR, "op1"), db=db,
        )
    assert exc.value.status_code == 403


def test_operator_KENDI_arizasina_yazabilir(db):
    f = _ariza(db, assigned_to_username="op1")
    update_fault_cause(
        f.id, FaultCauseUpdate(cause_code="animal"),
        current_user=_kullanici(UserRole.OPERATOR, "op1"), db=db,
    )
    db.refresh(f)
    assert f.cause_code == "animal"


def test_olmayan_ariza_404(db):
    with pytest.raises(HTTPException) as exc:
        update_fault_cause(
            9999, FaultCauseUpdate(cause_code="animal"), current_user=_kullanici(), db=db
        )
    assert exc.value.status_code == 404


def test_olay_kaydi_KURAL_ONERISINI_de_tutar(db):
    """Isabet olcumu gecmise donuk yapilabilsin."""
    from app.models.system_event import SystemEvent

    f = _ariza(db, auto_cause_code="overload")
    update_fault_cause(
        f.id, FaultCauseUpdate(cause_code="animal"), current_user=_kullanici(), db=db
    )
    olay = db.query(SystemEvent).filter(SystemEvent.event_type == "fault_cause_set").first()
    assert olay is not None
    assert "overload" in (olay.metadata_json or ""), "kural onerisi olaya yazilmadi"
