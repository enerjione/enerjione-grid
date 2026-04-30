"""IEC 104 nokta (point) kayit defteri.

Yeni model:
  - Her **cihaz** kendi ASDU Common Address'ine sahip olabilir
    (`device.iec104_common_address`). NULL ise outbound target'in default
    CA'si (`outbound_target.iec104_common_address`) kullanilir.
  - Her **sinyal** ait oldugu mutlak IOA'ya sahiptir (`signal.iec104_ioa`).
    Eski deploylar `iec104_ioa_offset` doldurmus olabilir; yeni alan dolu
    degilse offset mutlak IOA olarak yorumlanir.

Sonuc: tek TCP oturumu icinde farkli CA'lara ait ASDU'lar yayinlanir;
SCADA tarafi general interrogation'i CA bazli (veya broadcast=0xFFFF)
calistirabilir.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Iterable, Mapping

logger = logging.getLogger(__name__)

# IEC 60870-5-101: 0xFFFF rezerve edilmis; "broadcast" anlami tasir, cihaza
# atanmasi onerilmez.
BROADCAST_COMMON_ADDRESS = 0xFFFF


@dataclass(frozen=True)
class PointAddress:
    """Bir veri noktasi: hangi cihazin hangi sinyali, hangi (CA, IOA) ile yayinlanir."""

    device_code: str
    signal_key: str
    type_id: int
    common_address: int
    ioa: int


@dataclass(frozen=True)
class PointRegistry:
    """Bir outbound target'in tam point listesi.

    `default_common_address`: cihaz icin ozel CA tanimlanmamissa kullanilan
    fallback. Interrogation broadcast'inde "varsa hangi CA'lar var" sorgusu
    `unique_common_addresses` ile karsilanir.
    """

    target_id: int
    default_common_address: int
    points: tuple[PointAddress, ...]

    def by_key(self) -> dict[tuple[str, str], PointAddress]:
        return {(p.device_code, p.signal_key): p for p in self.points}

    def by_address(self) -> dict[tuple[int, int], PointAddress]:
        """(common_address, ioa) -> PointAddress. Carismalari log'lar (last-wins)."""
        result: dict[tuple[int, int], PointAddress] = {}
        for p in self.points:
            key = (p.common_address, p.ioa)
            if key in result:
                logger.warning(
                    "iec104_registry_collision ca=%d ioa=%d prev=(%s,%s) new=(%s,%s)",
                    p.common_address, p.ioa,
                    result[key].device_code, result[key].signal_key,
                    p.device_code, p.signal_key,
                )
            result[key] = p
        return result

    def unique_common_addresses(self) -> tuple[int, ...]:
        return tuple(sorted({p.common_address for p in self.points}))


def _resolve_signal_ioa(signal: Mapping) -> int | None:
    """Yeni `iec104_ioa` doluysa onu; degilse eski `iec104_ioa_offset`'i mutlak IOA olarak kullanir."""
    raw = signal.get("iec104_ioa")
    if raw is None:
        raw = signal.get("iec104_ioa_offset")
    if raw is None:
        return None
    try:
        ioa = int(raw)
    except (TypeError, ValueError):
        return None
    if not 0 <= ioa <= 0xFFFFFF:
        return None
    return ioa


def _resolve_device_ca(device: Mapping, *, default: int) -> int:
    raw = device.get("iec104_common_address")
    if raw is None:
        return default
    try:
        ca = int(raw)
    except (TypeError, ValueError):
        return default
    if not 0 <= ca <= 0xFFFE:
        return default
    return ca


def build_point_registry(
    *,
    target_id: int,
    default_common_address: int,
    devices: Iterable[Mapping],
    signals: Iterable[Mapping],
) -> PointRegistry:
    """Cihazlar + sinyal kataloguna gore deterministik bir point listesi uretir.

    - Sadece `is_active=True` cihazlar (default True).
    - Sadece `iec104_type_id` + (`iec104_ioa` ya da legacy `iec104_ioa_offset`)
      dolu olan sinyaller.
    - (CA, IOA) carismasi durumu log'lanir, son ekleme kazanir.
    """
    if not 0 <= default_common_address <= 0xFFFE:
        raise ValueError(f"default_common_address {default_common_address} out of range")

    active_devices = sorted(
        (d for d in devices if d.get("is_active", True)),
        key=lambda d: str(d.get("code") or ""),
    )
    mapped_signals: list[tuple[Mapping, int, int]] = []
    for s in signals:
        if not s.get("is_active", True):
            continue
        type_id_raw = s.get("iec104_type_id")
        if type_id_raw is None:
            continue
        ioa = _resolve_signal_ioa(s)
        if ioa is None:
            continue
        try:
            type_id = int(type_id_raw)
        except (TypeError, ValueError):
            continue
        mapped_signals.append((s, type_id, ioa))

    points: list[PointAddress] = []
    for device in active_devices:
        device_code = str(device.get("code") or "")
        if not device_code:
            continue
        ca = _resolve_device_ca(device, default=default_common_address)
        for signal, type_id, ioa in mapped_signals:
            signal_key = str(signal.get("key") or "")
            if not signal_key:
                continue
            points.append(
                PointAddress(
                    device_code=device_code,
                    signal_key=signal_key,
                    type_id=type_id,
                    common_address=ca,
                    ioa=ioa,
                )
            )

    return PointRegistry(
        target_id=target_id,
        default_common_address=default_common_address,
        points=tuple(points),
    )
