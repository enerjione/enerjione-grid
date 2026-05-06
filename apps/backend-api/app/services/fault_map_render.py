"""Ariza haritasi PNG rendering — email ekleri icin.

Frontend'deki FaultDetailModal mini haritasini (yesil hat / kirmizi
kesik ariza araligi / direk + cihaz marker'lari) sunucu tarafinda
PNG olarak uretir. Email gonderimi sirasinda inline ek olarak gonderilir.

Bagimliliklar:
  - staticmap: OSM tile fetch + line/marker render (Pillow uzerinde)
  - Pillow:    yazi/font ekleme

OSM tile sunucusuna erisim yoksa staticmap exception firlatir; cagiran
yer try/except ile maili METIN-ONLY moda dusurur.
"""

from __future__ import annotations

import io
import logging
from dataclasses import dataclass
from typing import List, Tuple

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.alarm import AlarmEvent
from app.models.fault import FaultEvent
from app.models.grid_topology import LineSegment, Pole

logger = logging.getLogger(__name__)


@dataclass
class _DevicePoint:
    lat: float
    lon: float
    is_red: bool


def _line_devices_with_positions(
    db: Session, line_id: int, active_red_device_ids: set[int]
) -> List[_DevicePoint]:
    """Hatın tum cihazlarini sirali olarak (slot from_pole_seq + slot ici t)
    pozisyonlariyla birlikte donder."""
    poles = list(
        db.scalars(
            select(Pole).where(Pole.line_id == line_id).order_by(Pole.sequence_no.asc())
        ).all()
    )
    poles_by_id = {p.id: p for p in poles}
    segs = list(
        db.scalars(
            select(LineSegment).where(LineSegment.line_id == line_id)
        ).all()
    )
    # Slot bazinda grupla
    by_slot: dict[tuple[int, int], list[LineSegment]] = {}
    for s in segs:
        if s.device_id is None:
            continue
        by_slot.setdefault((s.from_pole_id, s.to_pole_id), []).append(s)

    out: List[_DevicePoint] = []
    for i in range(len(poles) - 1):
        a = poles[i]
        b = poles[i + 1]
        slot = by_slot.get((a.id, b.id), [])
        if not slot:
            continue
        slot.sort(
            key=lambda s: (
                s.device_position_t if s.device_position_t is not None else 0.5,
                s.created_at,
                s.id,
            )
        )
        # Eger hepsi ayni 0.5 ise esit dagit
        if len(slot) > 1 and all(
            (s.device_position_t is None or s.device_position_t == 0.5) for s in slot
        ):
            n = len(slot)
            ts = [(idx + 1) / (n + 1) for idx in range(n)]
        else:
            ts = [
                (s.device_position_t if s.device_position_t is not None else 0.5)
                for s in slot
            ]
        for s, t in zip(slot, ts):
            lat = a.latitude + (b.latitude - a.latitude) * t
            lon = a.longitude + (b.longitude - a.longitude) * t
            out.append(
                _DevicePoint(
                    lat=lat, lon=lon, is_red=s.device_id in active_red_device_ids
                )
            )
    _ = poles_by_id  # silenced
    return out


def render_fault_map_png(
    db: Session,
    fault: FaultEvent,
    *,
    width: int = 720,
    height: int = 380,
) -> bytes | None:
    """Verilen fault icin hattin haritasini PNG olarak render et.

    Donus: PNG byte'lari. Hata durumunda None (cagiran loglar ve mail'i
    haritasiz gonderir)."""
    try:
        from staticmap import StaticMap, Line, CircleMarker  # type: ignore
    except Exception:  # noqa: BLE001
        logger.warning("staticmap_not_installed — fault map skip")
        return None

    line_id = fault.line_id
    poles = list(
        db.scalars(
            select(Pole).where(Pole.line_id == line_id).order_by(Pole.sequence_no.asc())
        ).all()
    )
    if len(poles) < 2:
        return None

    # Aktif alarmli cihaz id'leri
    active_red = {
        row[0]
        for row in db.execute(
            select(AlarmEvent.device_id).where(AlarmEvent.reset.is_(False))
        ).all()
    }
    devices = _line_devices_with_positions(db, line_id, active_red)

    # Polyline noktalari (lon, lat) sirasi staticmap icin
    line_pts: List[Tuple[float, float]] = [(p.longitude, p.latitude) for p in poles]

    # Ariza araligi: from_pole_seq..to_pole_seq arasi direkler
    fs = fault.from_pole_seq
    ts_ = fault.to_pole_seq
    if fs is not None and ts_ is not None:
        lo, hi = (min(fs, ts_), max(fs, ts_))
    else:
        lo = hi = None
    fault_pts: List[Tuple[float, float]] = []
    if lo is not None and hi is not None:
        for p in poles:
            if lo <= p.sequence_no <= hi:
                fault_pts.append((p.longitude, p.latitude))

    try:
        # OSM tile + base map
        m = StaticMap(width, height, url_template="https://a.tile.openstreetmap.org/{z}/{x}/{y}.png")

        # Saglikli yesil polyline (tum hat)
        if len(line_pts) >= 2:
            m.add_line(Line(line_pts, "#16a34a", 5))
        # Ariza araligi kirmizi (uzerine cizer)
        if len(fault_pts) >= 2:
            m.add_line(Line(fault_pts, "#ef4444", 7))

        # Direk marker'lari
        for p in poles:
            color = "#1e293b"
            if p.id == fault.from_pole_id:
                color = "#ef4444"
            elif p.id == fault.to_pole_id:
                color = "#10b981"
            m.add_marker(CircleMarker((p.longitude, p.latitude), color, 9))
            m.add_marker(CircleMarker((p.longitude, p.latitude), "#ffffff", 4))

        # Cihaz marker'lari (kirmizi=alarmli, yesil=normal)
        for d in devices:
            c = "#dc2626" if d.is_red else "#10b981"
            m.add_marker(CircleMarker((d.lon, d.lat), "#ffffff", 8))
            m.add_marker(CircleMarker((d.lon, d.lat), c, 6))

        image = m.render()
        buf = io.BytesIO()
        image.save(buf, format="PNG")
        return buf.getvalue()
    except Exception as exc:  # noqa: BLE001
        # OSM tile fetch agi olmasa veya staticmap render hatasi varsa
        # mail'i haritasiz gonderebilelim.
        logger.warning("fault_map_render_failed fault_id=%s error=%s", fault.id, exc)
        return None
