"""grid_import_service — sablon uret + parse + apply (in-memory SQLite).

Kolon sirasi (COLUMNS):
  Bolge_Kodu, Bolge_Adi, Hat_Kodu, Hat_Adi, Direk_Sira, Direk_Adi,
  Enlem, Boylam, Direk_Tipi, Cihaz, Cihaz_Sira, Yon,
  Bransman_Hat_Kodu, Bransman_Direk_Sira
"""

from __future__ import annotations

import io

import pytest
from openpyxl import Workbook, load_workbook
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.db.base import Base
from app.models.device import Device
from app.models.grid_topology import Line, LineSegment, Pole, Region  # noqa: F401
from app.services import grid_import_service as g


@pytest.fixture()
def db():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    s = Session()
    yield s
    s.close()


def _row(bolge_k="", bolge_a="", hat_k="", hat_a="", sira=None, direk_a="",
         lat=None, lon=None, tip="", cihaz="", cihaz_sira="", yon="",
         br_hat="", br_sira=""):
    return [bolge_k, bolge_a, hat_k, hat_a, sira, direk_a, lat, lon, tip,
            cihaz, cihaz_sira, yon, br_hat, br_sira]


def _sheet(rows: list[list]) -> bytes:
    wb = Workbook()
    ws = wb.active
    ws.title = "Topoloji"
    ws.append(g.COLUMNS)
    for r in rows:
        ws.append(r)
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def test_build_template_has_sheets_and_headers(db):
    buf = g.build_template_workbook(db)
    wb = load_workbook(buf)
    assert "Topoloji" in wb.sheetnames
    assert "Cihazlar" in wb.sheetnames
    assert "Yardim" in wb.sheetnames
    header = [c.value for c in wb["Topoloji"][1]]
    assert header == g.COLUMNS
    assert "Cihaz_Sira" in header and "Yon" in header


def test_build_template_fills_existing_data_as_tree(db):
    """Sablon mevcut topolojiyi DOLU + agac (forward-fill: alt satirda bolge bos)."""
    reg = Region(code="R1", name="Bolge 1")
    db.add(reg); db.flush()
    line = Line(region_id=reg.id, code="L1", name="Hat 1"); db.add(line); db.flush()
    p1 = Pole(line_id=line.id, sequence_no=1, latitude=40.0, longitude=29.0, pole_type="pole")
    p2 = Pole(line_id=line.id, sequence_no=2, latitude=40.1, longitude=29.1, pole_type="pole")
    db.add_all([p1, p2]); db.flush()
    db.commit()

    wb = load_workbook(g.build_template_workbook(db))
    ws = wb["Topoloji"]
    # 1. veri satiri bolge dolu, 2. satir bolge bos (agac girinti).
    assert ws.cell(row=2, column=1).value == "R1"
    assert ws.cell(row=3, column=1).value in (None, "")   # forward-fill: bos


def test_parse_valid_and_invalid_rows(db):
    db.add(Device(code="DEV-1", name="Cihaz 1", ip_address="10.0.0.1", latitude=0, longitude=0))
    db.commit()
    rows = [
        _row("R1", "Bolge 1", "L1", "Hat 1", 1, "Bas", 40.0, 29.0, "transformer"),
        _row(sira=2, lat=40.1, lon=29.1, tip="pole", cihaz="DEV-1"),          # forward-fill bolge/hat
        _row(sira=3, lat=999.0, lon=29.2, tip="pole"),                        # gecersiz enlem
        _row(sira=4, lat=40.3, lon=29.3, tip="pole", cihaz="YOK"),            # bilinmeyen cihaz
    ]
    plan = g.parse_and_plan(_sheet(rows), db)
    c = plan.counts()
    assert c["regions"] == 1
    assert c["lines"] == 1
    assert c["poles"] == 3           # direk 1,2,4 gecerli (3 enlem hatali)
    assert c["devices"] == 1         # DEV-1
    assert c["errors"] == 2          # gecersiz enlem + bilinmeyen cihaz


def test_parse_multi_device_slot_with_order_and_direction(db):
    """Iki direk arasinda 2 cihaz + sira + yon."""
    db.add(Device(code="D1", name="c1", ip_address="1.1.1.1", latitude=0, longitude=0))
    db.add(Device(code="D2", name="c2", ip_address="1.1.1.2", latitude=0, longitude=0))
    db.commit()
    rows = [
        _row("R1", "Bolge 1", "L1", "Hat 1", 1, "P1", 40.0, 29.0, "pole", "D1", 1, "yesil"),
        _row(sira=1, cihaz="D2", cihaz_sira=2, yon="kirmizi"),  # ayni direk-1 slotu, 2. cihaz
        _row(sira=2, lat=40.1, lon=29.1, tip="pole"),
    ]
    plan = g.parse_and_plan(_sheet(rows), db)
    assert plan.counts()["devices"] == 2
    assert plan.counts()["errors"] == 0
    # apply -> 2 segment, orientation + position_t
    result = g.apply_plan(plan, db); db.commit()
    assert result.segments_created == 2
    segs = db.query(LineSegment).order_by(LineSegment.device_position_t).all()
    assert [s.device_orientation for s in segs] == ["green_forward", "red_forward"]
    assert segs[0].device_position_t < segs[1].device_position_t   # sira korunur


def test_parse_invalid_direction(db):
    db.add(Device(code="D1", name="c1", ip_address="1.1.1.1", latitude=0, longitude=0)); db.commit()
    rows = [
        _row("R1", "B", "L1", "H", 1, "", 40.0, 29.0, "pole", "D1", 1, "mavi"),  # gecersiz yon
        _row(sira=2, lat=40.1, lon=29.1, tip="pole"),
    ]
    plan = g.parse_and_plan(_sheet(rows), db)
    assert any("Yon" in e.message for e in plan.errors)


def test_apply_creates_topology(db):
    db.add(Device(code="DEV-1", name="Cihaz 1", ip_address="10.0.0.1", latitude=0, longitude=0)); db.commit()
    rows = [
        _row("R1", "Bolge 1", "L1", "Hat 1", 1, "P1", 40.0, 29.0, "pole", "DEV-1"),
        _row(sira=2, lat=40.1, lon=29.1, tip="pole"),
    ]
    result = g.apply_plan(g.parse_and_plan(_sheet(rows), db), db); db.commit()
    assert result.regions_created == 1
    assert result.lines_created == 1
    assert result.poles_created == 2
    assert result.segments_created == 1
    assert db.query(Region).count() == 1


def test_apply_is_idempotent_upsert(db):
    rows = [
        _row("R1", "Bolge 1", "L1", "Hat 1", 1, "P1", 40.0, 29.0, "pole"),
        _row(sira=2, lat=40.1, lon=29.1, tip="pole"),
    ]
    data = _sheet(rows)
    g.apply_plan(g.parse_and_plan(data, db), db); db.commit()
    r2 = g.apply_plan(g.parse_and_plan(data, db), db); db.commit()
    assert r2.regions_created == 0
    assert r2.poles_created == 0
    assert r2.poles_updated == 2
    assert db.query(Pole).count() == 2


def test_import_template_round_trip_keeps_existing_device(db):
    dev = Device(code="D1", name="c1", ip_address="1.1.1.1", latitude=0, longitude=0)
    db.add(dev); db.flush()
    reg = Region(code="R1", name="Bolge 1"); db.add(reg); db.flush()
    line = Line(region_id=reg.id, code="L1", name="Hat 1"); db.add(line); db.flush()
    p1 = Pole(line_id=line.id, sequence_no=1, latitude=40.0, longitude=29.0, pole_type="pole")
    p2 = Pole(line_id=line.id, sequence_no=2, latitude=40.1, longitude=29.1, pole_type="pole")
    db.add_all([p1, p2]); db.flush()
    db.add(LineSegment(line_id=line.id, from_pole_id=p1.id, to_pole_id=p2.id, device_id=dev.id))
    db.commit()

    data = g.build_template_workbook(db).getvalue()
    plan = g.parse_and_plan(data, db)
    result = g.apply_plan(plan, db); db.commit()

    assert plan.counts()["errors"] == 0
    assert result.errors == []
    assert result.segments_created == 0
    assert db.query(LineSegment).count() == 1


def test_same_line_code_in_different_regions_does_not_merge_poles(db):
    rows = [
        _row("R1", "Bolge 1", "L1", "Hat 1", 1, "R1P1", 40.0, 29.0, "pole"),
        _row(sira=2, lat=40.1, lon=29.1, tip="pole"),
        _row("R2", "Bolge 2", "L1", "Hat 1", 1, "R2P1", 41.0, 30.0, "pole"),
        _row(sira=2, lat=41.1, lon=30.1, tip="pole"),
    ]
    g.apply_plan(g.parse_and_plan(_sheet(rows), db), db); db.commit()

    assert db.query(Region).count() == 2
    assert db.query(Line).count() == 2
    assert db.query(Pole).count() == 4
