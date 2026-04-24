"""IEC 60870-5-104 APCI + ASDU encode/decode (saf Python, harici paket yok).

Genel frame yapisi (her APDU):

    [ 0x68 ][ LEN ][ CTRL1..CTRL4 ][ ASDU... ]

  * `0x68` start byte (START).
  * `LEN`  APCI kontrol 4 bayt + ASDU uzunlugu (bayt).
  * CTRL1..CTRL4 APCI kontrol alani. Format bit'leri ile 3 alt turu vardir:

      I-format (Information):   CTRL1 LSB = 0
      S-format (Supervisory):   CTRL1 = 0x01, CTRL2 = 0
      U-format (Unnumbered):    CTRL1 LSB bit'leri = 0b11

Sadece TCP uzerinden, maksimum APDU 253 bayt (START + 253 = 255 toplam). Kucuk
ve sabit bir alt kume desteklenir:

    TypeID 1   M_SP_NA_1   binary single-point (1 byte SIQ)
    TypeID 13  M_ME_NC_1   measured short float (4 byte IEEE754 LE + QDS)
    TypeID 15  M_IT_NA_1   integrated totals (4 byte int32 LE + BCR)
    TypeID 100 C_IC_NA_1   general interrogation command (gelen)
    TypeID 103 C_CS_NA_1   clock sync (gelen; yok sayilir, ack)

Amac: harici SCADA sistemlerine spontaneous event + general interrogation
(tum degerler) gonderebilmek. Spesifikasyon: IEC 60870-5-104 / IEC 60870-5-101.
"""

from __future__ import annotations

import struct
from dataclasses import dataclass

# --- TypeID sabitleri (IEC 60870-5-101 Tablo) -------------------------------
TYPE_M_SP_NA_1 = 1
TYPE_M_ME_NC_1 = 13
TYPE_M_IT_NA_1 = 15
TYPE_C_IC_NA_1 = 100  # General interrogation command
TYPE_C_CS_NA_1 = 103  # Clock synchronization command

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
COT_INTERROGATION = 20  # responded to station interrogation (Qoi=20)

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
    # 4-byte control field (CTRL1..CTRL4) degerleri:
    APCI_U_START_ACT:     bytes((0x07, 0x00, 0x00, 0x00)),
    APCI_U_START_CONFIRM: bytes((0x0B, 0x00, 0x00, 0x00)),
    APCI_U_STOP_ACT:      bytes((0x13, 0x00, 0x00, 0x00)),
    APCI_U_STOP_CONFIRM:  bytes((0x23, 0x00, 0x00, 0x00)),
    APCI_U_TEST_ACT:      bytes((0x43, 0x00, 0x00, 0x00)),
    APCI_U_TEST_CONFIRM:  bytes((0x83, 0x00, 0x00, 0x00)),
}

START_BYTE = 0x68


# ============================================================================
# APCI parse + build
# ============================================================================

@dataclass(frozen=True)
class ParsedAPCI:
    """Gelen APDU'nun APCI kisminin yapili sonucu.

    `kind` degeri: "I" | "S" | "U-<tag>". I-frame icin `ns`/`nr` pencereleri
    doludur. S-frame icin sadece `nr`. U-frame icin `u_tag` (STARTDT_ACT gibi).
    """

    kind: str
    length: int
    ns: int | None = None
    nr: int | None = None
    u_tag: str | None = None
    asdu: bytes = b""


def parse_apci(data: bytes) -> ParsedAPCI | None:
    """Tek bir APDU'yu ayristirir. Yetersiz byte varsa None doner.

    `data` stream baslangicindan olabilir; yalnizca ilk geçerli APDU alinir.
    Caller geri kalan bytes'i kendi buffer'inda tutar.
    """
    if len(data) < 6:
        return None
    if data[0] != START_BYTE:
        raise ValueError(f"invalid start byte 0x{data[0]:02X}")
    length = data[1]
    if length < 4 or length > 253:
        raise ValueError(f"invalid APDU length {length}")
    total = 2 + length
    if len(data) < total:
        return None  # buffer eksik
    ctrl = data[2:6]
    asdu = data[6:total]

    if ctrl[0] & 0x01 == 0:
        # I-format: ns = CTRL1(7..1) | CTRL2<<7 ; nr = CTRL3(7..1) | CTRL4<<7
        ns = ((ctrl[0] >> 1) & 0x7F) | (ctrl[1] << 7)
        nr = ((ctrl[2] >> 1) & 0x7F) | (ctrl[3] << 7)
        return ParsedAPCI(kind="I", length=length, ns=ns, nr=nr, asdu=asdu)
    if ctrl[0] & 0x03 == 0x01:
        nr = ((ctrl[2] >> 1) & 0x7F) | (ctrl[3] << 7)
        return ParsedAPCI(kind="S", length=length, nr=nr, asdu=b"")
    # U-format
    for tag, expected in _U_CODES.items():
        if ctrl == expected:
            return ParsedAPCI(kind=f"U-{tag}", length=length, u_tag=tag, asdu=b"")
    raise ValueError(f"unrecognized U-frame CTRL {ctrl.hex()}")


def build_u_frame(tag: str) -> bytes:
    """STARTDT/STOPDT/TESTFR unnumbered frame uretir."""
    if tag not in _U_CODES:
        raise ValueError(f"unknown U-frame tag {tag}")
    ctrl = _U_CODES[tag]
    return bytes((START_BYTE, 4)) + ctrl


def build_s_frame(*, nr: int) -> bytes:
    """Supervisory frame (kabul/teslim onayi)."""
    if not 0 <= nr < 32768:
        raise ValueError("nr out of range")
    ctrl = bytes((0x01, 0x00, (nr << 1) & 0xFF, (nr >> 7) & 0xFF))
    return bytes((START_BYTE, 4)) + ctrl


def build_i_frame_asdu(*, asdu: bytes, ns: int, nr: int) -> bytes:
    """I-frame: bilgi ASDU + gonderim/alinma sayaclari."""
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


# ============================================================================
# ASDU (Data Unit Identifier + Information Objects) encode
# ============================================================================

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
    """ASDU baslik (Data Unit Identifier) - 6 bayt.

    Byte 0: TypeID
    Byte 1: VSQ (SQ bit + number of elements, max 127)
    Byte 2: COT (T + P/N bits) low byte
    Byte 3: Originator Address
    Byte 4-5: Common Address of ASDU (LE)
    """
    if not 0 <= num_ix <= 0x7F:
        raise ValueError("num information objects must be <= 127")
    vsq = (0x80 if sq else 0x00) | (num_ix & 0x7F)
    cot_byte = (cause & 0x3F) | (0x80 if test else 0) | (0x40 if negative else 0)
    return struct.pack("<BBBBH", type_id, vsq, cot_byte, originator & 0xFF, common_address & 0xFFFF)


def _encode_ioa(ioa: int) -> bytes:
    """3 bayt little-endian Information Object Address."""
    if not 0 <= ioa <= 0xFFFFFF:
        raise ValueError(f"IOA {ioa} out of range (0..16777215)")
    return bytes((ioa & 0xFF, (ioa >> 8) & 0xFF, (ioa >> 16) & 0xFF))


def _quality_byte(*, good: bool) -> int:
    """QDS (quality descriptor) — tek bayt.
    bit 7=IV invalid, 6=NT not topical, 5=SB substituted, 4=BL blocked,
    bit 3-1 reserved, bit 0=OV overflow.
    `good=True` -> 0, `False` -> IV set (0x80).
    """
    return 0x00 if good else 0x80


def encode_asdu_single_point(
    *,
    common_address: int,
    cause: int,
    ioa: int,
    value: bool,
    good: bool = True,
    originator: int = 0,
) -> bytes:
    """M_SP_NA_1 (TypeID=1) — tek nokta binary."""
    dui = _encode_dui(
        type_id=TYPE_M_SP_NA_1,
        num_ix=1,
        sq=False,
        cause=cause,
        originator=originator,
        common_address=common_address,
    )
    siq = (0x01 if value else 0x00) | (0x00 if good else 0x80)
    return dui + _encode_ioa(ioa) + bytes((siq,))


def encode_asdu_float(
    *,
    common_address: int,
    cause: int,
    ioa: int,
    value: float,
    good: bool = True,
    originator: int = 0,
) -> bytes:
    """M_ME_NC_1 (TypeID=13) — measured, short float IEEE754 LE + QDS."""
    dui = _encode_dui(
        type_id=TYPE_M_ME_NC_1,
        num_ix=1,
        sq=False,
        cause=cause,
        originator=originator,
        common_address=common_address,
    )
    payload = struct.pack("<f", float(value)) + bytes((_quality_byte(good=good),))
    return dui + _encode_ioa(ioa) + payload


def encode_asdu_counter(
    *,
    common_address: int,
    cause: int,
    ioa: int,
    value: int,
    good: bool = True,
    originator: int = 0,
    sequence: int = 0,
) -> bytes:
    """M_IT_NA_1 (TypeID=15) — integrated total (4 byte int32 LE + BCR).

    BCR byte: bit 5=IV, bit 6=CA carry adjusted, bit 7=CY carry.
    Biz sadece IV bit'ini kullaniyoruz; sequence 5 bit (0..31).
    """
    dui = _encode_dui(
        type_id=TYPE_M_IT_NA_1,
        num_ix=1,
        sq=False,
        cause=cause,
        originator=originator,
        common_address=common_address,
    )
    bcr = (sequence & 0x1F) | (0x00 if good else 0x20)
    payload = struct.pack("<i", int(value)) + bytes((bcr,))
    return dui + _encode_ioa(ioa) + payload


def encode_interrogation_confirm(
    *,
    common_address: int,
    cause: int,
    qoi: int = 20,
    originator: int = 0,
    negative: bool = False,
) -> bytes:
    """C_IC_NA_1 (TypeID=100) cevap/termination ASDU'su.

    Cause tipik olarak:
      * ACT_CON (7)  — interrogation kabul edildi
      * ACT_TERM (10) — interrogation tamamlandi (tum noktalar yayinlandi)
    """
    dui = _encode_dui(
        type_id=TYPE_C_IC_NA_1,
        num_ix=1,
        sq=False,
        cause=cause,
        originator=originator,
        common_address=common_address,
        negative=negative,
    )
    return dui + _encode_ioa(0) + bytes((qoi & 0xFF,))
