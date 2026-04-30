"""IEC 104 point registry unit testleri (cihaz bazli ASDU CA modeli).

Yeni model: her cihaz kendi `iec104_common_address`'ine sahip olabilir;
sinyaller `iec104_ioa` (mutlak IOA) tasir. Eski deploylar `iec104_ioa_offset`
ile gelirse bu mutlak IOA olarak yorumlanir.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.services.iec104.registry import build_point_registry


@dataclass
class _FakeDevice:
    code: str
    is_active: bool = True
    iec104_common_address: int | None = None


@dataclass
class _FakeSignal:
    key: str
    iec104_type_id: int | None
    iec104_ioa: int | None = None
    iec104_ioa_offset: int | None = None
    is_active: bool = True


def test_device_specific_ca_used_when_present() -> None:
    devices = [
        _FakeDevice("DEV-A", iec104_common_address=10),
        _FakeDevice("DEV-B", iec104_common_address=20),
    ]
    signals = [_FakeSignal("voltage", 13, iec104_ioa=100)]
    registry = build_point_registry(
        target_id=1, default_common_address=1, devices=devices, signals=signals,
    )
    by_key = registry.by_key()
    assert by_key[("DEV-A", "voltage")].common_address == 10
    assert by_key[("DEV-A", "voltage")].ioa == 100
    assert by_key[("DEV-B", "voltage")].common_address == 20
    assert by_key[("DEV-B", "voltage")].ioa == 100


def test_default_ca_used_when_device_missing() -> None:
    devices = [_FakeDevice("DEV-X")]  # iec104_common_address NULL
    signals = [_FakeSignal("v", 13, iec104_ioa=42)]
    registry = build_point_registry(
        target_id=1, default_common_address=7, devices=devices, signals=signals,
    )
    p = registry.by_key()[("DEV-X", "v")]
    assert p.common_address == 7
    assert p.ioa == 42


def test_legacy_offset_treated_as_absolute_ioa() -> None:
    devices = [_FakeDevice("D", iec104_common_address=3)]
    signals = [_FakeSignal("legacy", 13, iec104_ioa=None, iec104_ioa_offset=999)]
    registry = build_point_registry(
        target_id=1, default_common_address=1, devices=devices, signals=signals,
    )
    p = registry.by_key()[("D", "legacy")]
    assert p.ioa == 999


def test_new_ioa_takes_precedence_over_offset() -> None:
    devices = [_FakeDevice("D", iec104_common_address=3)]
    signals = [_FakeSignal("both", 13, iec104_ioa=100, iec104_ioa_offset=999)]
    registry = build_point_registry(
        target_id=1, default_common_address=1, devices=devices, signals=signals,
    )
    p = registry.by_key()[("D", "both")]
    assert p.ioa == 100


def test_unmapped_signals_dropped() -> None:
    devices = [_FakeDevice("DEV-1", iec104_common_address=1)]
    signals = [
        _FakeSignal("mapped", 13, iec104_ioa=1000),
        _FakeSignal("unmapped_type", None, iec104_ioa=1000),
        _FakeSignal("unmapped_ioa", 13, iec104_ioa=None, iec104_ioa_offset=None),
        _FakeSignal("inactive", 13, iec104_ioa=1000, is_active=False),
    ]
    registry = build_point_registry(
        target_id=1, default_common_address=1, devices=devices, signals=signals,
    )
    keys = {p.signal_key for p in registry.points}
    assert keys == {"mapped"}


def test_inactive_devices_skipped() -> None:
    devices = [_FakeDevice("A", iec104_common_address=1), _FakeDevice("B", is_active=False)]
    signals = [_FakeSignal("x", 13, iec104_ioa=1)]
    registry = build_point_registry(
        target_id=1, default_common_address=1, devices=devices, signals=signals,
    )
    codes = {p.device_code for p in registry.points}
    assert codes == {"A"}


def test_unique_common_addresses_listed() -> None:
    devices = [
        _FakeDevice("A", iec104_common_address=10),
        _FakeDevice("B", iec104_common_address=20),
        _FakeDevice("C", iec104_common_address=10),  # ayni CA tekrar
        _FakeDevice("D"),  # default'a duser
    ]
    signals = [_FakeSignal("s", 13, iec104_ioa=1)]
    registry = build_point_registry(
        target_id=1, default_common_address=99, devices=devices, signals=signals,
    )
    assert registry.unique_common_addresses() == (10, 20, 99)


def test_devices_sorted_by_code_for_determinism() -> None:
    devices = [
        _FakeDevice("DEV-Z", iec104_common_address=3),
        _FakeDevice("DEV-A", iec104_common_address=1),
        _FakeDevice("DEV-M", iec104_common_address=2),
    ]
    signals = [_FakeSignal("x", 13, iec104_ioa=100)]
    registry = build_point_registry(
        target_id=1, default_common_address=1, devices=devices, signals=signals,
    )
    ordered_codes = [p.device_code for p in registry.points]
    assert ordered_codes == ["DEV-A", "DEV-M", "DEV-Z"]
