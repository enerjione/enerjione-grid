"""IEC 60870-5-104 nokta (point) kayit defteri.

Gorev: bir `(device_code, signal_key)` ciftini `(TypeID, mutlak IOA)` degerine
haritalayan sabit bir tablo uretmek. Dis SCADA bu IOA'lari referansa alacagi
icin tablo **deterministik ve kararli** olmali:

  * `device_index`   sirayla sorted device listesi (code'a gore).
  * `signal.iec104_ioa_offset`  goreli IOA (sinyal kataloguna gomuludur).
  * `absolute_ioa = device_index * device_stride + offset`.

Target basina stride bir outbound target'ta (`iec104_ioa_device_stride`)
tutulur; varsayilan = 10_000.

Bu modul sadece haritalamayi yapar; aktif deger yonetimi ve TCP trafigi
`server.py` icindedir.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Iterable

from app.models.device import Device
from app.models.signal_catalog import SignalCatalog

logger = logging.getLogger(__name__)

DEFAULT_IOA_DEVICE_STRIDE = 10_000


@dataclass(frozen=True)
class PointAddress:
    """Dis dunyaya aciklanan bir veri noktasi (device_code, signal_key).

    `type_id`        : IEC 60870-5-101 ASDU TypeID (1 / 13 / 15).
    `absolute_ioa`   : 24-bit IOA; server bunu dis clientlara gonderir.
    `signal_key`     : Platform tarafindaki etiket (istemci debug icin).
    `device_code`    : Ait oldugu cihazin kodu (istemci debug icin).
    """

    device_code: str
    signal_key: str
    type_id: int
    absolute_ioa: int


@dataclass(frozen=True)
class PointRegistry:
    """Bir outbound target'in IEC 104 adresleme kumesi."""

    target_id: int
    common_address: int
    device_stride: int
    points: tuple[PointAddress, ...]

    def by_key(self) -> dict[tuple[str, str], PointAddress]:
        return {(p.device_code, p.signal_key): p for p in self.points}


def build_point_registry(
    *,
    target_id: int,
    common_address: int,
    devices: Iterable[Device],
    signals: Iterable[SignalCatalog],
    device_stride: int | None = None,
) -> PointRegistry:
    """Aktif cihazlar + sinyal kataloguna gore deterministik bir point listesi uretir.

    Harici alan kurallari:
      * Cihazlar `code` alfabetik siralanir (index atamasi bu sirayla).
      * Sadece `is_active=True` cihazlar hesaba katilir.
      * `iec104_type_id` veya `iec104_ioa_offset` NULL olan sinyaller atlanir.
      * Ayni hesaplanan IOA'ya iki sinyal dusmesi beklenmez; dusulurse log'a
        yazilir ve son ekleme kazanir.
    """
    stride = device_stride or DEFAULT_IOA_DEVICE_STRIDE
    if stride < 1 or stride > 1_000_000:
        raise ValueError(f"device_stride {stride} out of sane range (1..1_000_000)")

    active_devices = sorted(
        (d for d in devices if getattr(d, "is_active", True)),
        key=lambda d: d.code,
    )
    mapped_signals = [
        s for s in signals
        if getattr(s, "iec104_type_id", None) is not None
        and getattr(s, "iec104_ioa_offset", None) is not None
        and getattr(s, "is_active", True)
    ]

    seen: dict[int, tuple[str, str]] = {}
    points: list[PointAddress] = []

    for device_index, device in enumerate(active_devices):
        if device_index * stride > 0xFFFFFF:
            logger.warning(
                "iec104_registry_truncated reason=ioa_overflow device=%s device_index=%d stride=%d",
                device.code,
                device_index,
                stride,
            )
            break
        for signal in mapped_signals:
            absolute_ioa = device_index * stride + int(signal.iec104_ioa_offset)
            if absolute_ioa > 0xFFFFFF:
                # Bir cihazdaki bir sinyal 24-bit sinirin uzerine cikmis; bilgi
                # vererek atla ki server hic baslamadan patlamasin.
                logger.warning(
                    "iec104_registry_skip reason=ioa_overflow device=%s signal=%s ioa=%d",
                    device.code,
                    signal.key,
                    absolute_ioa,
                )
                continue
            if absolute_ioa in seen:
                prev_dev, prev_sig = seen[absolute_ioa]
                logger.warning(
                    "iec104_registry_collision ioa=%d prev=(%s, %s) new=(%s, %s)",
                    absolute_ioa,
                    prev_dev,
                    prev_sig,
                    device.code,
                    signal.key,
                )
            seen[absolute_ioa] = (device.code, signal.key)
            points.append(
                PointAddress(
                    device_code=device.code,
                    signal_key=signal.key,
                    type_id=int(signal.iec104_type_id),
                    absolute_ioa=absolute_ioa,
                )
            )

    return PointRegistry(
        target_id=target_id,
        common_address=common_address,
        device_stride=stride,
        points=tuple(points),
    )
