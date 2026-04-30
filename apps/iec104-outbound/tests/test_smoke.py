"""Akil-sagligi testleri (cihaz bazli ASDU CA modeli)."""

from iec104_outbound.encoder import (
    COT_SPONTANEOUS,
    encode_asdu_float,
    encode_asdu_single_point,
    parse_apci,
    build_i_frame_asdu,
)
from iec104_outbound.registry import build_point_registry


def test_encode_asdu_float_layout():
    asdu = encode_asdu_float(
        common_address=1, cause=COT_SPONTANEOUS, ioa=42, value=3.14, good=True,
    )
    # DUI (6) + IOA (3) + float (4) + QDS (1) = 14
    assert len(asdu) == 14
    assert asdu[0] == 13  # M_ME_NC_1


def test_encode_asdu_single_point_layout():
    asdu = encode_asdu_single_point(
        common_address=1, cause=COT_SPONTANEOUS, ioa=10, value=True, good=True,
    )
    # DUI (6) + IOA (3) + SIQ (1) = 10
    assert len(asdu) == 10
    assert asdu[0] == 1  # M_SP_NA_1


def test_i_frame_round_trip():
    asdu = encode_asdu_single_point(
        common_address=7, cause=3, ioa=100, value=False, good=False,
    )
    apdu = build_i_frame_asdu(asdu=asdu, ns=5, nr=11)
    parsed = parse_apci(apdu)
    assert parsed is not None
    assert parsed.kind == "I"
    assert parsed.ns == 5
    assert parsed.nr == 11
    assert parsed.asdu == asdu


def test_registry_uses_device_ca_when_present():
    devices = [
        {"code": "DEV-A", "iec104_common_address": 10, "is_active": True},
        {"code": "DEV-B", "iec104_common_address": 20, "is_active": True},
    ]
    signals = [
        {"key": "v", "iec104_type_id": 13, "iec104_ioa": 100, "is_active": True},
        {"key": "i", "iec104_type_id": 13, "iec104_ioa": 101, "is_active": True},
    ]
    reg = build_point_registry(
        target_id=1, default_common_address=99, devices=devices, signals=signals,
    )
    by_key = reg.by_key()
    assert by_key[("DEV-A", "v")].common_address == 10
    assert by_key[("DEV-A", "v")].ioa == 100
    assert by_key[("DEV-B", "i")].common_address == 20
    assert by_key[("DEV-B", "i")].ioa == 101
    assert reg.unique_common_addresses() == (10, 20)


def test_registry_falls_back_to_default_ca():
    devices = [{"code": "X", "is_active": True}]  # CA NULL
    signals = [{"key": "s", "iec104_type_id": 1, "iec104_ioa": 5, "is_active": True}]
    reg = build_point_registry(
        target_id=1, default_common_address=42, devices=devices, signals=signals,
    )
    assert reg.by_key()[("X", "s")].common_address == 42


def test_registry_legacy_offset_used_as_absolute():
    devices = [{"code": "L", "iec104_common_address": 5, "is_active": True}]
    signals = [
        {"key": "old", "iec104_type_id": 13, "iec104_ioa_offset": 2500, "is_active": True},
    ]
    reg = build_point_registry(
        target_id=1, default_common_address=1, devices=devices, signals=signals,
    )
    assert reg.by_key()[("L", "old")].ioa == 2500
