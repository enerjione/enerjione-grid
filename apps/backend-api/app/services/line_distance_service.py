"""Hat uzerinde "tel mesafesi" (iletken boyu) hesaplari.

Amac:
  Bir ariza bolgesinin hat basindan KAC METRE uzakta oldugunu vermek. Kus
  ucusu degil: direkler `sequence_no` sirasiyla birlestirilir ve ardisik
  direkler arasi haversine mesafeleri toplanir — yani sahadaki ekibin hat
  boyunca yuruyecegi mesafe.

Kullanilan veri:
  - Pole.latitude/longitude + sequence_no  -> hat guzergahi (polyline)
  - LineSegment.device_position_t          -> cihazin iki direk arasindaki
      0..1 konumu. Cihaz koordinati bu interpolasyondan cikar; boylece
      ariza araligi direk hassasiyetinde degil CIHAZ hassasiyetinde olur.
  - Line.branched_from_pole_id             -> bransman hatti. Bransmanin
      "hat basi" mesafesi, ana hattaki dallanma diregine kadar olan mesafe
      uzerine eklenir (zincir rekursif cozulur).

Sinirlar (bilincli kabuller):
  - 2B hesap: direkler arasi kot farki (egim) dikkate ALINMAZ.
  - Iletken sarkmasi (sag) payi yok. Gercek iletken boyu bu degerden
    tipik olarak %1-3 daha uzundur.
  - Cihaz `t` formulu `fault_map_render._line_devices_with_positions` ve
    frontend `shared/lineDistance.ts` ile AYNI tutulmalidir.
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass

from app.models.grid_topology import Line, LineSegment, Pole

logger = logging.getLogger(__name__)

# IUGG ortalama Dunya yaricapi (m).
EARTH_RADIUS_M = 6371008.8


def haversine_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Iki koordinat arasi kus ucusu mesafe (metre)."""
    phi1 = math.radians(lat1)
    phi2 = math.radians(lat2)
    d_phi = phi2 - phi1
    d_lambda = math.radians(lon2 - lon1)
    a = (
        math.sin(d_phi / 2) ** 2
        + math.cos(phi1) * math.cos(phi2) * math.sin(d_lambda / 2) ** 2
    )
    return 2.0 * EARTH_RADIUS_M * math.asin(min(1.0, math.sqrt(a)))


def slot_positions(slot: list[LineSegment]) -> list[float]:
    """Bir slot (iki direk arasi) icindeki cihazlarin 0..1 konumlari.

    `slot` YERINDE siralanir (device_position_t -> created_at -> id).
    Hepsi varsayilan (None/0.5) ise cihazlar esit araliklarla dagitilir;
    aksi halde kayitli t degerleri kullanilir. Bu kural
    `fault_map_render._line_devices_with_positions` ile birebir aynidir.
    """
    slot.sort(
        key=lambda s: (
            s.device_position_t if s.device_position_t is not None else 0.5,
            s.created_at,
            s.id,
        )
    )
    if len(slot) > 1 and all(
        (s.device_position_t is None or s.device_position_t == 0.5) for s in slot
    ):
        n = len(slot)
        return [(idx + 1) / (n + 1) for idx in range(n)]
    return [
        (s.device_position_t if s.device_position_t is not None else 0.5) for s in slot
    ]


@dataclass
class LineDistanceIndex:
    """Onceden hesaplanmis tel-mesafesi tablolari.

    Tum degerler METRE ve hattin BASINDAN (bransman offseti dahil) olcuulur.
    """

    #: pole_id -> hat basindan tel mesafesi
    pole_distance: dict[int, float]
    #: line_segments.id -> o segmentteki cihazin hat basindan tel mesafesi
    device_distance: dict[int, float]
    #: line_id -> hattin kendi uzunlugu (bransman offseti HARIC)
    line_length: dict[int, float]
    #: line_id -> hattin ilk diregine kadar olan offset (bransman zinciri)
    line_start: dict[int, float]

    def line_end_distance(self, line_id: int) -> float | None:
        """Hat basindan hattin SON diregine kadar tel mesafesi."""
        start = self.line_start.get(line_id)
        length = self.line_length.get(line_id)
        if start is None or length is None:
            return None
        return start + length


def build_line_distance_index(
    lines: list[Line],
    poles: list[Pole],
    segments: list[LineSegment],
) -> LineDistanceIndex:
    """Topoloji anlik goruntusunden tel-mesafesi indeksini kur.

    Veriyi DB'den KENDI cekmez; cagiran zaten yuklemis olur (bkz.
    `fault_recompute_service.recompute_faults`). Bu sayede fonksiyon saf ve
    test edilebilir kalir.
    """
    poles_by_id = {p.id: p for p in poles}
    poles_by_line: dict[int, list[Pole]] = {}
    for p in poles:
        poles_by_line.setdefault(p.line_id, []).append(p)
    for arr in poles_by_line.values():
        arr.sort(key=lambda p: p.sequence_no)

    # 1) Hat ICI kumulatif mesafe — her hattin kendi ilk diregi 0 kabul edilir.
    local: dict[int, float] = {}
    line_length: dict[int, float] = {}
    for line_id, arr in poles_by_line.items():
        acc = 0.0
        prev: Pole | None = None
        for p in arr:
            if prev is not None:
                acc += haversine_m(
                    prev.latitude, prev.longitude, p.latitude, p.longitude
                )
            local[p.id] = acc
            prev = p
        line_length[line_id] = acc

    # 2) Bransman offseti — hattin baslangici, bagli oldugu ana hattaki
    #    dallanma diregine kadar olan mesafedir. Bransmanin bransmani icin
    #    zincir rekursif cozulur; veri hatasiyla dongu olusursa 0 kabul edilir
    #    (sonsuz rekursiyon yerine uyari logu).
    lines_by_id = {ln.id: ln for ln in lines}
    line_start: dict[int, float] = {}

    def resolve_start(line_id: int, seen: frozenset[int]) -> float:
        cached = line_start.get(line_id)
        if cached is not None:
            return cached
        if line_id in seen:
            logger.warning("line_distance_branch_cycle line_id=%d", line_id)
            return 0.0
        line = lines_by_id.get(line_id)
        parent_pole = (
            poles_by_id.get(line.branched_from_pole_id)
            if line is not None and line.branched_from_pole_id is not None
            else None
        )
        if parent_pole is None:
            value = 0.0
        else:
            value = resolve_start(parent_pole.line_id, seen | {line_id}) + local.get(
                parent_pole.id, 0.0
            )
        line_start[line_id] = value
        return value

    for line in lines:
        resolve_start(line.id, frozenset())

    # 3) Mutlak direk mesafesi = hat offseti + hat ici kumulatif.
    pole_distance: dict[int, float] = {}
    for pole_id, d in local.items():
        pole = poles_by_id.get(pole_id)
        if pole is None:
            continue
        pole_distance[pole_id] = line_start.get(pole.line_id, 0.0) + d

    # 4) Cihaz mesafesi — slot uclarindaki direk mesafeleri arasinda t ile
    #    interpolasyon. Ayni (from, to) ciftinde birden fazla cihaz olabilir.
    by_slot: dict[tuple[int, int], list[LineSegment]] = {}
    for s in segments:
        if s.device_id is None:
            continue
        by_slot.setdefault((s.from_pole_id, s.to_pole_id), []).append(s)

    device_distance: dict[int, float] = {}
    for (from_id, to_id), slot in by_slot.items():
        a = pole_distance.get(from_id)
        b = pole_distance.get(to_id)
        if a is None or b is None:
            continue
        for seg, t in zip(slot, slot_positions(slot)):
            device_distance[seg.id] = a + (b - a) * t

    return LineDistanceIndex(
        pole_distance=pole_distance,
        device_distance=device_distance,
        line_length=line_length,
        line_start=line_start,
    )
