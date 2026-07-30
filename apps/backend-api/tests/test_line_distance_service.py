"""line_distance_service — tel mesafesi hesabi testleri.

DB'ye ihtiyac yok: ORM nesneleri yalnizca alan tasiyicisi olarak kullanilir
(session'a eklenmez), build_line_distance_index saf fonksiyondur.
"""

from datetime import datetime, timezone

import pytest

from app.models.grid_topology import Line, LineSegment, Pole
from app.services.line_distance_service import (
    build_line_distance_index,
    haversine_m,
)


NOW = datetime(2026, 7, 30, 12, 0, tzinfo=timezone.utc)


def _pole(pid: int, line_id: int, seq: int, lat: float, lon: float) -> Pole:
    p = Pole(id=pid, line_id=line_id, sequence_no=seq, latitude=lat, longitude=lon)
    p.pole_type = "pole"
    return p


def _seg(
    sid: int,
    line_id: int,
    from_pole_id: int,
    to_pole_id: int,
    device_id: int | None,
    t: float | None = None,
) -> LineSegment:
    s = LineSegment(
        id=sid,
        line_id=line_id,
        from_pole_id=from_pole_id,
        to_pole_id=to_pole_id,
        device_id=device_id,
        device_position_t=t,
    )
    s.created_at = NOW
    return s


def test_haversine_known_distance():
    # 0.01 derece enlem ~ 1111.95 m (boylamdan bagimsiz).
    d = haversine_m(40.0, 32.0, 40.01, 32.0)
    assert d == pytest.approx(1111.95, abs=1.0)


def test_pole_distance_is_cumulative_along_line():
    line = Line(id=1, region_id=1, code="F1", name="Hat 1")
    line.branched_from_pole_id = None
    poles = [
        _pole(1, 1, 1, 40.00, 32.0),
        _pole(2, 1, 2, 40.01, 32.0),
        _pole(3, 1, 3, 40.02, 32.0),
    ]
    idx = build_line_distance_index([line], poles, [])

    assert idx.pole_distance[1] == pytest.approx(0.0)
    assert idx.pole_distance[2] == pytest.approx(1111.95, abs=1.0)
    assert idx.pole_distance[3] == pytest.approx(2223.9, abs=2.0)
    # Hat ucu = son direk.
    assert idx.line_end_distance(1) == pytest.approx(idx.pole_distance[3])


def test_pole_order_follows_sequence_no_not_input_order():
    """Direkler karisik sirada gelse bile mesafe sequence_no'ya gore toplanir."""
    line = Line(id=1, region_id=1, code="F1", name="Hat 1")
    line.branched_from_pole_id = None
    poles = [
        _pole(3, 1, 3, 40.02, 32.0),
        _pole(1, 1, 1, 40.00, 32.0),
        _pole(2, 1, 2, 40.01, 32.0),
    ]
    idx = build_line_distance_index([line], poles, [])
    assert idx.pole_distance[1] < idx.pole_distance[2] < idx.pole_distance[3]


def test_device_distance_uses_position_t():
    line = Line(id=1, region_id=1, code="F1", name="Hat 1")
    line.branched_from_pole_id = None
    poles = [_pole(1, 1, 1, 40.00, 32.0), _pole(2, 1, 2, 40.01, 32.0)]
    span = 1111.95

    # Manuel t=0.25 -> span'in dortte biri.
    idx = build_line_distance_index(
        [line], poles, [_seg(10, 1, 1, 2, device_id=100, t=0.25)]
    )
    assert idx.device_distance[10] == pytest.approx(span * 0.25, abs=1.0)


def test_multiple_devices_in_slot_are_evenly_distributed():
    """t verilmemis coklu cihaz: (idx+1)/(n+1) esit dagilim (frontend ile ayni)."""
    line = Line(id=1, region_id=1, code="F1", name="Hat 1")
    line.branched_from_pole_id = None
    poles = [_pole(1, 1, 1, 40.00, 32.0), _pole(2, 1, 2, 40.01, 32.0)]
    span = 1111.95
    segs = [
        _seg(10, 1, 1, 2, device_id=100),
        _seg(11, 1, 1, 2, device_id=101),
    ]
    idx = build_line_distance_index([line], poles, segs)

    assert idx.device_distance[10] == pytest.approx(span / 3, abs=1.0)
    assert idx.device_distance[11] == pytest.approx(2 * span / 3, abs=1.0)


def test_branch_line_offset_includes_parent_distance():
    """Bransmanin hat basi mesafesi, ana hattaki dallanma diregine kadar
    olan mesafenin uzerine biner."""
    main = Line(id=1, region_id=1, code="F1", name="Ana Hat")
    main.branched_from_pole_id = None
    branch = Line(id=2, region_id=1, code="F1-B", name="Bransman")
    branch.branched_from_pole_id = 3  # ana hattin 3. diregi

    poles = [
        _pole(1, 1, 1, 40.00, 32.0),
        _pole(2, 1, 2, 40.01, 32.0),
        _pole(3, 1, 3, 40.02, 32.0),
        # Bransman: dallanma diregi ile ayni noktadan basliyor.
        _pole(11, 2, 1, 40.02, 32.0),
        _pole(12, 2, 2, 40.02, 32.01),
    ]
    idx = build_line_distance_index([main, branch], poles, [])

    parent_at_branch = idx.pole_distance[3]
    assert parent_at_branch == pytest.approx(2223.9, abs=2.0)
    # Bransmanin ilk diregi ana hattaki mesafeyi devralir.
    assert idx.pole_distance[11] == pytest.approx(parent_at_branch, abs=0.01)
    assert idx.pole_distance[12] > parent_at_branch


def test_branch_cycle_does_not_recurse_forever():
    """Veri hatasiyla dongu olusursa offset 0 kabul edilir, patlamaz."""
    a = Line(id=1, region_id=1, code="A", name="A")
    a.branched_from_pole_id = 20  # B hattinin diregi
    b = Line(id=2, region_id=1, code="B", name="B")
    b.branched_from_pole_id = 10  # A hattinin diregi
    poles = [_pole(10, 1, 1, 40.0, 32.0), _pole(20, 2, 1, 41.0, 32.0)]

    idx = build_line_distance_index([a, b], poles, [])
    assert idx.pole_distance[10] >= 0.0
    assert idx.pole_distance[20] >= 0.0


def test_missing_pole_coordinates_do_not_crash_device_lookup():
    """Segment ucu bilinmeyen bir direge isaret ederse o cihaz atlanir."""
    line = Line(id=1, region_id=1, code="F1", name="Hat 1")
    line.branched_from_pole_id = None
    poles = [_pole(1, 1, 1, 40.0, 32.0)]
    segs = [_seg(10, 1, 1, 999, device_id=100)]  # 999 diye direk yok

    idx = build_line_distance_index([line], poles, segs)
    assert 10 not in idx.device_distance
