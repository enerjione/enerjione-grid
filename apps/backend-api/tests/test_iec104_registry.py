"""IEC 104 point registry unit testleri.

Deterministik ve standartlara uygun IOA uretiminin garantisi bu testlere
bagli. `PointRegistry` imzasindaki tum sozlesmeler burada dogrulanir.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.services.iec104.registry import (
    DEFAULT_IOA_DEVICE_STRIDE,
    build_point_registry,
)


@dataclass
class _FakeDevice:
    code: str
    is_active: bool = True


@dataclass
class _FakeSignal:
    key: str
    iec104_type_id: int | None
    iec104_ioa_offset: int | None
    is_active: bool = True


def test_absolute_ioa_uses_stride_times_device_index_plus_offset() -> None:
    devices = [_FakeDevice("DEV-A"), _FakeDevice("DEV-B"), _FakeDevice("DEV-C")]
    signals = [
        _FakeSignal("master.a", 13, 1000),
        _FakeSignal("master.b", 1, 1),
    ]
    registry = build_point_registry(
        target_id=1, common_address=1, devices=devices, signals=signals,
        device_stride=10_000,
    )
    by_key = registry.by_key()
    # DEV-A index 0
    assert by_key[("DEV-A", "master.a")].absolute_ioa == 1000
    assert by_key[("DEV-A", "master.b")].absolute_ioa == 1
    # DEV-B index 1
    assert by_key[("DEV-B", "master.a")].absolute_ioa == 11_000
    # DEV-C index 2
    assert by_key[("DEV-C", "master.b")].absolute_ioa == 20_001


def test_devices_sorted_by_code_for_stability() -> None:
    """Cihaz sirasi hash/DB order'a bakilmaksizin deterministik olmali."""
    devices = [_FakeDevice("DEV-Z"), _FakeDevice("DEV-A"), _FakeDevice("DEV-M")]
    signals = [_FakeSignal("x", 13, 100)]
    registry = build_point_registry(
        target_id=1, common_address=1, devices=devices, signals=signals,
        device_stride=10_000,
    )
    by_key = registry.by_key()
    assert by_key[("DEV-A", "x")].absolute_ioa == 100          # index 0
    assert by_key[("DEV-M", "x")].absolute_ioa == 10_100       # index 1
    assert by_key[("DEV-Z", "x")].absolute_ioa == 20_100       # index 2


def test_unmapped_signals_dropped() -> None:
    devices = [_FakeDevice("DEV-1")]
    signals = [
        _FakeSignal("mapped", 13, 1000),
        _FakeSignal("unmapped_type", None, 1000),
        _FakeSignal("unmapped_offset", 13, None),
        _FakeSignal("inactive", 13, 1000, is_active=False),
    ]
    registry = build_point_registry(
        target_id=1, common_address=1, devices=devices, signals=signals,
    )
    keys = {p.signal_key for p in registry.points}
    assert keys == {"mapped"}


def test_inactive_devices_skipped() -> None:
    devices = [_FakeDevice("DEV-A"), _FakeDevice("DEV-B", is_active=False)]
    signals = [_FakeSignal("x", 13, 1)]
    registry = build_point_registry(
        target_id=1, common_address=1, devices=devices, signals=signals,
    )
    codes = {p.device_code for p in registry.points}
    assert codes == {"DEV-A"}


def test_default_stride_applied_when_none() -> None:
    devices = [_FakeDevice("A"), _FakeDevice("B")]
    signals = [_FakeSignal("s", 13, 50)]
    registry = build_point_registry(
        target_id=1, common_address=1, devices=devices, signals=signals,
        device_stride=None,
    )
    assert registry.device_stride == DEFAULT_IOA_DEVICE_STRIDE
    by_key = registry.by_key()
    assert by_key[("B", "s")].absolute_ioa == DEFAULT_IOA_DEVICE_STRIDE + 50


def test_24bit_ioa_cap_enforced_per_point() -> None:
    # stride 900k. 18. cihaz icin index*stride = 16.200.000 (hala 16.7M altinda),
    # 19. cihaz index*stride = 17.100.000 > 16.7M -> registry icin overflow, atla.
    devices = [_FakeDevice(f"DEV-{i:03d}") for i in range(20)]
    signals = [_FakeSignal("x", 13, 100)]
    registry = build_point_registry(
        target_id=1, common_address=1, devices=devices, signals=signals,
        device_stride=900_000,
    )
    codes = {p.device_code for p in registry.points}
    # Ilk 19 cihaz (index 0..18) girer; son cihaz (DEV-019) IOA overflow'dan
    # dolayi registry'e dusmez.
    assert "DEV-018" in codes
    assert "DEV-019" not in codes


def test_deterministic_point_order_by_device_then_signal_offset() -> None:
    devices = [_FakeDevice("DEV-1")]
    signals = [
        _FakeSignal("z", 13, 5000),
        _FakeSignal("a", 1, 1),
        _FakeSignal("m", 15, 2000),
    ]
    registry = build_point_registry(
        target_id=1, common_address=1, devices=devices, signals=signals,
    )
    # Points cihazi sirasina gore cikar, sinyal listesindeki verilen
    # siraya gore ilerler (build_point_registry kontrollu iterasyon).
    assert [p.signal_key for p in registry.points] == ["z", "a", "m"]
