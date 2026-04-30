"""IEC 60870-5-104 nokta (point) kayit defteri (cihaz bazli ASDU CA modeli).

Yeni model:
  * `signal.iec104_ioa`              — sinyalin mutlak IOA'si (24-bit).
  * `device.iec104_common_address`   — cihazin ASDU CA'si.
                                       NULL ise outbound target'in default
                                       CA'si kullanilir.

Geri uyumluluk:
  * `signal.iec104_ioa_offset` doluysa ve `iec104_ioa` yoksa, offset mutlak
    IOA olarak yorumlanir (eski kayitlar bozulmasin diye).

Bu modul yalnizca haritalamayi yapar; aktif deger yonetimi ve TCP trafigi
`server.py` icindedir.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Iterable

from app.models.device import Device
from app.models.signal_catalog import SignalCatalog

logger = logging.getLogger(__name__)

# IEC 60870-5-101: 0xFFFF rezerve "broadcast"; cihaza atanmasi onerilmez.
BROADCAST_COMMON_ADDRESS = 0xFFFF


@dataclass(frozen=True)
class PointAddress:
    device_code: str
    signal_key: str
    type_id: int
    common_address: int
    ioa: int


@dataclass(frozen=True)
class PointRegistry:
    target_id: int
    default_common_address: int
    points: tuple[PointAddress, ...]

    def by_key(self) -> dict[tuple[str, str], PointAddress]:
        return {(p.device_code, p.signal_key): p for p in self.points}

    def unique_common_addresses(self) -> tuple[int, ...]:
        return tuple(sorted({p.common_address for p in self.points}))


def _resolve_signal_ioa(signal: SignalCatalog) -> int | None:
    raw = getattr(signal, "iec104_ioa", None)
    if raw is None:
        raw = getattr(signal, "iec104_ioa_offset", None)
    if raw is None:
        return None
    try:
        ioa = int(raw)
    except (TypeError, ValueError):
        return None
    if not 0 <= ioa <= 0xFFFFFF:
        return None
    return ioa


def _resolve_device_ca(device: Device, *, default: int) -> int:
    raw = getattr(device, "iec104_common_address", None)
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
    devices: Iterable[Device],
    signals: Iterable[SignalCatalog],
) -> PointRegistry:
    """Aktif cihazlar + sinyal kataloguna gore point listesi uretir.

    - Pasif cihazlar atlanir.
    - `iec104_type_id` NULL veya IOA cozulemiyorsa sinyal atlanir.
    - (CA, IOA) carismasi log'lanir; son ekleme kazanir (caller silmez).
    """
    if not 0 <= default_common_address <= 0xFFFE:
        raise ValueError(f"default_common_address {default_common_address} out of range")

    active_devices = sorted(
        (d for d in devices if getattr(d, "is_active", True)),
        key=lambda d: d.code,
    )
    mapped_signals: list[tuple[SignalCatalog, int, int]] = []
    for s in signals:
        if not getattr(s, "is_active", True):
            continue
        type_id = getattr(s, "iec104_type_id", None)
        if type_id is None:
            continue
        ioa = _resolve_signal_ioa(s)
        if ioa is None:
            continue
        mapped_signals.append((s, int(type_id), ioa))

    seen: dict[tuple[int, int], tuple[str, str]] = {}
    points: list[PointAddress] = []

    for device in active_devices:
        ca = _resolve_device_ca(device, default=default_common_address)
        for signal, type_id, ioa in mapped_signals:
            key = (ca, ioa)
            if key in seen:
                prev_dev, prev_sig = seen[key]
                logger.warning(
                    "iec104_registry_collision ca=%d ioa=%d prev=(%s,%s) new=(%s,%s)",
                    ca, ioa, prev_dev, prev_sig, device.code, signal.key,
                )
            seen[key] = (device.code, signal.key)
            points.append(
                PointAddress(
                    device_code=device.code,
                    signal_key=signal.key,
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
