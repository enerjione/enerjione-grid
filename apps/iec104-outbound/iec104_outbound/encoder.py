"""IEC 60870-5-104 APCI + ASDU encode/decode (saf Python).

Genel frame yapisi (her APDU):

    [ 0x68 ][ LEN ][ CTRL1..CTRL4 ][ ASDU... ]

Format bit'leri ile 3 alt turu vardir:

    I-format (Information):   CTRL1 LSB = 0
    S-format (Supervisory):   CTRL1 = 0x01, CTRL2 = 0
    U-format (Unnumbered):    CTRL1 LSB bit'leri = 0b11

Sadece TCP uzerinden, maksimum APDU 253 bayt. Kucuk ve sabit bir alt kume
desteklenir:

    TypeID 1   M_SP_NA_1   binary single-point (1 byte SIQ)
    TypeID 13  M_ME_NC_1   measured short float (4 byte IEEE754 LE + QDS)
    TypeID 15  M_IT_NA_1   integrated totals (4 byte int32 LE + BCR)
    TypeID 100 C_IC_NA_1   general interrogation command (gelen)
    TypeID 103 C_CS_NA_1   clock sync (gelen; yok sayilir, ack)

Spesifikasyon: IEC 60870-5-104 / IEC 60870-5-101.
"""

from __future__ import annotations

import struct
from dataclasses import dataclass

# --- TypeID sabitleri (IEC 60870-5-101 Tablo) -------------------------------
TYPE_M_SP_NA_1 = 1
TYPE_M_ME_NC_1 = 13
TYPE_M_IT_NA_1 = 15
TYPE_C_IC_NA_1 = 100
TYPE_C_CS_NA_1 = 103

# --- Cause Of Transmission (COT) --------------------------------------------
COT_PERIODIC = 1
COT_BACKGROUND = 2
COT_SPONTANEOUS = 3
COT_INITIALIZED = 4
COT_REQUEST = 5
COT_ACTIVATION = 6
COT_ACTIVATION_CON = 7
COT_DEACTIVATION = 8
COT_DEACTIVATION_CON = 9
COT_ACTIVATION_TERM = 10
COT_INTERROGATION = 20

# --- APCI format tagleri ----------------------------------------------------
APCI_I_FORMAT = "I"
APCI_S_FORMAT = "S"
APCI_U_START_ACT = "STARTDT_ACT"
APCI_U_START_CONFIRM = "STARTDT_CON"
APCI_U_STOP_ACT = "STOPDT_ACT"
APCI_U_STOP_CONFIRM = "STOPDT_CON"
APCI_U_TEST_ACT = "TESTFR_ACT"
APCI_U_TEST_CONFIRM = "TESTFR_CON"

_U_CODES = {
    APCI_U_START_ACT:     bytes((0x07, 0x00, 0x00, 0x00)),
    APCI_U_START_CONFIRM: bytes((0x0B, 0x00, 0x00, 0x00)),
    APCI_U_STOP_ACT:      bytes((0x13, 0x00, 0x00, 0x00)),
    APCI_U_STOP_CONFIRM:  bytes((0x23, 0x00, 0x00, 0x00)),
    APCI_U_TEST_ACT:      bytes((0x43, 0x00, 0x00, 0x00)),
    APCI_U_TEST_CONFIRM:  bytes((0x83, 0x00, 0x00, 0x00)),
}

START_BYTE = 0x68


@dataclass(frozen=True)
class ParsedAPCI:
    kind: str
    length: int
    ns: int | None = None
    nr: int | None = None
    u_tag: str | None = None
    asdu: bytes = b""


def parse_apci(data: bytes) -> ParsedAPCI | None:
    if len(data) < 6:
        return None
    if data[0] != START_BYTE:
        raise ValueError(f"invalid start byte 0x{data[0]:02X}")
    length = data[1]
    if length < 4 or length > 253:
        raise ValueError(f"invalid APDU length {length}")
    total = 2 + length
    if len(data) < total:
        return None
    ctrl = data[2:6]
    asdu = data[6:total]

    if ctrl[0] & 0x01 == 0:
        ns = ((ctrl[0] >> 1) & 0x7F) | (ctrl[1] << 7)
        nr = ((ctrl[2] >> 1) & 0x7F) | (ctrl[3] << 7)
        return ParsedAPCI(kind="I", length=length, ns=ns, nr=nr, asdu=asdu)
    if ctrl[0] & 0x03 == 0x01:
        nr = ((ctrl[2] >> 1) & 0x7F) | (ctrl[3] << 7)
        return ParsedAPCI(kind="S", length=length, nr=nr, asdu=b"")
    for tag, expected in _U_CODES.items():
        if ctrl == expected:
            return ParsedAPCI(kind=f"U-{tag}", length=length, u_tag=tag, asdu=b"")
    raise ValueError(f"unrecognized U-frame CTRL {ctrl.hex()}")


def build_u_frame(tag: str) -> bytes:
    if tag not in _U_CODES:
        raise ValueError(f"unknown U-frame tag {tag}")
    ctrl = _U_CODES[tag]
    return bytes((START_BYTE, 4)) + ctrl


def build_s_frame(*, nr: int) -> bytes:
    if not 0 <= nr < 32768:
        raise ValueError("nr out of range")
    ctrl = bytes((0x01, 0x00, (nr << 1) & 0xFF, (nr >> 7) & 0xFF))
    return bytes((START_BYTE, 4)) + ctrl


def build_i_frame_asdu(*, asdu: bytes, ns: int, nr: int) -> bytes:
    if not 0 <= ns < 32768 or not 0 <= nr < 32768:
        raise ValueError("ns/nr out of range")
    if len(asdu) > 249:
        raise ValueError("ASDU too large for single APDU")
    ctrl = bytes((
        (ns << 1) & 0xFF,
        (ns >> 7) & 0xFF,
        (nr << 1) & 0xFF,
        (nr >> 7) & 0xFF,
    ))
    length = 4 + len(asdu)
    return bytes((START_BYTE, length)) + ctrl + asdu


def _encode_dui(
    *,
    type_id: int,
    num_ix: int,
    sq: bool,
    cause: int,
    originator: int,
    common_address: int,
    test: bool = False,
    negative: bool = False,
) -> bytes:
    if not 0 <= num_ix <= 0x7F:
        raise ValueError("num information objects must be <= 127")
    vsq = (0x80 if sq else 0x00) | (num_ix & 0x7F)
    cot_byte = (cause & 0x3F) | (0x80 if test else 0) | (0x40 if negative else 0)
    return struct.pack("<BBBBH", type_id, vsq, cot_byte, originator & 0xFF, common_address & 0xFFFF)


def _encode_ioa(ioa: int) -> bytes:
    if not 0 <= ioa <= 0xFFFFFF:
        raise ValueError(f"IOA {ioa} out of range (0..16777215)")
    return bytes((ioa & 0xFF, (ioa >> 8) & 0xFF, (ioa >> 16) & 0xFF))


def encode_asdu_single_point(
    *, common_address: int, cause: int, ioa: int,
    value: bool, good: bool = True, originator: int = 0,
) -> bytes:
    dui = _encode_dui(
        type_id=TYPE_M_SP_NA_1, num_ix=1, sq=False,
        cause=cause, originator=originator, common_address=common_address,
    )
    siq = (0x01 if value else 0x00) | (0x00 if good else 0x80)
    return dui + _encode_ioa(ioa) + bytes((siq,))


def encode_asdu_float(
    *, common_address: int, cause: int, ioa: int,
    value: float, good: bool = True, originator: int = 0,
) -> bytes:
    dui = _encode_dui(
        type_id=TYPE_M_ME_NC_1, num_ix=1, sq=False,
        cause=cause, originator=originator, common_address=common_address,
    )
    qds = 0x00 if good else 0x80
    payload = struct.pack("<f", float(value)) + bytes((qds,))
    return dui + _encode_ioa(ioa) + payload


def encode_asdu_counter(
    *, common_address: int, cause: int, ioa: int,
    value: int, good: bool = True, originator: int = 0, sequence: int = 0,
) -> bytes:
    dui = _encode_dui(
        type_id=TYPE_M_IT_NA_1, num_ix=1, sq=False,
        cause=cause, originator=originator, common_address=common_address,
    )
    bcr = (sequence & 0x1F) | (0x00 if good else 0x20)
    payload = struct.pack("<i", int(value)) + bytes((bcr,))
    return dui + _encode_ioa(ioa) + payload


def encode_interrogation_confirm(
    *, common_address: int, cause: int, qoi: int = 20,
    originator: int = 0, negative: bool = False,
) -> bytes:
    dui = _encode_dui(
        type_id=TYPE_C_IC_NA_1, num_ix=1, sq=False,
        cause=cause, originator=originator, common_address=common_address,
        negative=negative,
    )
    return dui + _encode_ioa(0) + bytes((qoi & 0xFF,))
