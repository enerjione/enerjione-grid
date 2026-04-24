"""IEC 60870-5-104 APCI/ASDU encoder unit testleri.

Bit-seviyesi beklentileri elle hesaplanmis referans frame'lere karsi dogrular.
Standarda uygun olmayan bir kodlama olursa testler hemen kirilir.
"""

from __future__ import annotations

import struct

import pytest

from app.services.iec104 import (
    APCI_U_START_ACT,
    APCI_U_START_CONFIRM,
    APCI_U_TEST_ACT,
    APCI_U_TEST_CONFIRM,
    COT_ACTIVATION_CON,
    COT_ACTIVATION_TERM,
    COT_INTERROGATION,
    COT_SPONTANEOUS,
    build_i_frame_asdu,
    build_s_frame,
    build_u_frame,
    encode_asdu_counter,
    encode_asdu_float,
    encode_asdu_single_point,
    encode_interrogation_confirm,
    parse_apci,
)


# ============================================================================
# U-frame (Unnumbered control)
# ============================================================================

def test_u_frame_startdt_act_matches_standard() -> None:
    """STARTDT ACT APDU = 68 04 07 00 00 00 (standart)."""
    assert build_u_frame(APCI_U_START_ACT) == bytes.fromhex("6804070000 00".replace(" ", ""))


def test_u_frame_startdt_con_matches_standard() -> None:
    assert build_u_frame(APCI_U_START_CONFIRM) == bytes.fromhex("68040B000000")


def test_u_frame_testfr_act_con_round_trip() -> None:
    act = build_u_frame(APCI_U_TEST_ACT)
    con = build_u_frame(APCI_U_TEST_CONFIRM)
    assert parse_apci(act).u_tag == APCI_U_TEST_ACT
    assert parse_apci(con).u_tag == APCI_U_TEST_CONFIRM


# ============================================================================
# S-frame (Supervisory)
# ============================================================================

def test_s_frame_encodes_receive_counter() -> None:
    frame = build_s_frame(nr=5)
    # START=0x68, LEN=0x04, CTRL1=0x01 (S), CTRL2=0x00, CTRL3=(5<<1)=0x0A, CTRL4=0x00
    assert frame == bytes.fromhex("680401000A00")
    parsed = parse_apci(frame)
    assert parsed.kind == "S"
    assert parsed.nr == 5


def test_s_frame_parses_large_nr() -> None:
    frame = build_s_frame(nr=300)  # 300 = 0x12C -> (300<<1)=0x258 -> CTRL3=0x58, CTRL4=0x02
    parsed = parse_apci(frame)
    assert parsed.kind == "S"
    assert parsed.nr == 300


# ============================================================================
# I-frame (Information) round trip + parse
# ============================================================================

def test_i_frame_round_trip() -> None:
    asdu = encode_asdu_single_point(common_address=1, cause=COT_SPONTANEOUS, ioa=100, value=True)
    frame = build_i_frame_asdu(asdu=asdu, ns=7, nr=3)
    parsed = parse_apci(frame)
    assert parsed.kind == "I"
    assert parsed.ns == 7
    assert parsed.nr == 3
    assert parsed.asdu == asdu


def test_parse_apci_rejects_invalid_start() -> None:
    with pytest.raises(ValueError):
        parse_apci(bytes.fromhex("FF040B000000"))


def test_parse_apci_returns_none_when_buffer_short() -> None:
    # APCI 4-byte CTRL + 2-byte ASDU gerekli (len=6) ama sadece 4 verdik
    assert parse_apci(b"\x68\x06\x01\x00") is None


# ============================================================================
# ASDU encode — Single Point (M_SP_NA_1 TypeID 1)
# ============================================================================

def test_single_point_on_good_quality() -> None:
    asdu = encode_asdu_single_point(
        common_address=0x0102, cause=COT_SPONTANEOUS, ioa=0x000064, value=True
    )
    # DUI: type=01 vsq=01 cot=03 orig=00 ca=0201
    # IOA 3-byte LE: 64 00 00
    # SIQ: 01 (value=1, quality=good)
    assert asdu == bytes.fromhex("01 01 03 00 02 01 64 00 00 01".replace(" ", ""))


def test_single_point_off_invalid_quality() -> None:
    asdu = encode_asdu_single_point(
        common_address=1, cause=COT_SPONTANEOUS, ioa=5, value=False, good=False
    )
    # SIQ: value=0, IV bit set -> 0x80
    assert asdu[-1] == 0x80


# ============================================================================
# ASDU encode — Measured Float (M_ME_NC_1 TypeID 13)
# ============================================================================

def test_float_asdu_uses_ieee754_le() -> None:
    asdu = encode_asdu_float(common_address=1, cause=COT_SPONTANEOUS, ioa=1000, value=3.14)
    # Son 5 bayt: 4 bayt IEEE754 LE + 1 bayt QDS
    raw = asdu[-5:-1]
    qds = asdu[-1]
    assert struct.unpack("<f", raw)[0] == pytest.approx(3.14, rel=1e-6)
    assert qds == 0x00


def test_float_asdu_type_id_and_cot() -> None:
    asdu = encode_asdu_float(common_address=0x12, cause=COT_INTERROGATION, ioa=0, value=0.0)
    assert asdu[0] == 13        # TypeID
    assert asdu[1] == 1         # VSQ: num=1 sq=0
    assert asdu[2] == COT_INTERROGATION


# ============================================================================
# ASDU encode — Integrated Totals (M_IT_NA_1 TypeID 15)
# ============================================================================

def test_counter_asdu_negative_int() -> None:
    # Cause=1 (periodic)
    asdu = encode_asdu_counter(common_address=1, cause=1, ioa=2000, value=-5)
    # 4 bayt int32 LE + 1 bayt BCR
    raw = asdu[-5:-1]
    assert struct.unpack("<i", raw)[0] == -5


def test_counter_asdu_sequence_in_bcr() -> None:
    asdu = encode_asdu_counter(
        common_address=1, cause=1, ioa=2000, value=100, sequence=7
    )
    assert asdu[-1] & 0x1F == 7
    assert asdu[-1] & 0x20 == 0  # good=True -> IV clear


# ============================================================================
# Interrogation confirm/termination
# ============================================================================

def test_interrogation_activation_confirm() -> None:
    asdu = encode_interrogation_confirm(
        common_address=1, cause=COT_ACTIVATION_CON, qoi=20
    )
    assert asdu[0] == 100                 # TypeID = C_IC_NA_1
    assert asdu[2] == COT_ACTIVATION_CON  # 7
    assert asdu[-1] == 20                 # QOI


def test_interrogation_activation_termination() -> None:
    asdu = encode_interrogation_confirm(
        common_address=1, cause=COT_ACTIVATION_TERM, qoi=20
    )
    assert asdu[2] == COT_ACTIVATION_TERM


# ============================================================================
# Sinir kontrolleri
# ============================================================================

def test_ioa_overflow_rejected() -> None:
    with pytest.raises(ValueError):
        encode_asdu_single_point(common_address=1, cause=3, ioa=0x1000000, value=True)


def test_num_info_objects_max_127() -> None:
    from app.services.iec104.encoder import _encode_dui  # type: ignore[attr-defined]

    with pytest.raises(ValueError):
        _encode_dui(type_id=1, num_ix=128, sq=False, cause=3, originator=0, common_address=1)
