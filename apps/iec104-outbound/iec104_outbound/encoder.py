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
    TypeID 30  M_SP_TB_1   single-point with CP56Time2a timestamp
    TypeID 36  M_ME_TF_1   measured short float with CP56Time2a timestamp
    TypeID 100 C_IC_NA_1   general interrogation command (gelen)
    TypeID 103 C_CS_NA_1   clock sync (gelen; yok sayilir, ack)

Spesifikasyon: IEC 60870-5-104 / IEC 60870-5-101.
"""

from __future__ import annotations

import struct
from dataclasses import dataclass
from datetime import datetime, timezone

# --- TypeID sabitleri (IEC 60870-5-101 Tablo) -------------------------------
TYPE_M_SP_NA_1 = 1
TYPE_M_ME_NC_1 = 13
TYPE_M_IT_NA_1 = 15
TYPE_M_SP_TB_1 = 30   # single-point + CP56Time2a
TYPE_M_ME_TF_1 = 36   # short float + CP56Time2a
TYPE_C_SC_NA_1 = 45    # tek noktali komut (single command)
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


def _encode_cp56time2a(ts: datetime | None) -> bytes:
    """7 byte CP56Time2a (IEC 60870-5-101 / 7.2.6.18).

    Bayt duzeni:
      [0..1] millisecond: 16-bit LE (saniye + milisaniye birlestirilmis,
             0..59999 araliginda — saniye*1000 + ms)
      [2]    minute:    bit 0..5 = 0..59, bit 6 = RES1, bit 7 = IV (invalid)
      [3]    hour:      bit 0..4 = 0..23, bit 5..6 = RES2, bit 7 = SU (sum.time)
      [4]    day-of-month: bit 0..4 = 1..31, bit 5..7 = day-of-week (0..7)
      [5]    month:    bit 0..3 = 1..12, bit 4..7 = RES3
      [6]    year:     bit 0..6 = 0..99 (yil-2000), bit 7 = RES4

    ts None ise simdiki UTC zamani kullanilir; aksi halde verilen datetime
    UTC'ye cevrilir. CP56Time2a TZ flag'i tasimaz, kararlilik icin UTC.
    """
    if ts is None:
        ts = datetime.now(timezone.utc)
    elif ts.tzinfo is None:
        # Naive datetime'i UTC kabul et
        ts = ts.replace(tzinfo=timezone.utc)
    else:
        ts = ts.astimezone(timezone.utc)
    ms_field = (ts.second * 1000 + (ts.microsecond // 1000)) & 0xFFFF
    minute = ts.minute & 0x3F          # IV=0, RES1=0
    hour = ts.hour & 0x1F              # SU=0, RES2=0
    # ISO weekday: Mon=1..Sun=7. CP56Time2a 1..7 ayni anlam (Pazar=7).
    day_of_week = ts.isoweekday() & 0x07
    day_of_month = ts.day & 0x1F
    dow_dom = ((day_of_week & 0x07) << 5) | day_of_month
    month = ts.month & 0x0F            # RES3=0
    year = (ts.year - 2000) & 0x7F     # RES4=0
    return struct.pack(
        "<HBBBBB",
        ms_field,
        minute,
        hour,
        dow_dom,
        month,
        year,
    )


def encode_asdu_single_point_t(
    *, common_address: int, cause: int, ioa: int,
    value: bool, good: bool = True, originator: int = 0,
    timestamp: datetime | None = None,
) -> bytes:
    """TypeID 30 — single-point + CP56Time2a (1+3+1+7 = 12 byte info object)."""
    dui = _encode_dui(
        type_id=TYPE_M_SP_TB_1, num_ix=1, sq=False,
        cause=cause, originator=originator, common_address=common_address,
    )
    siq = (0x01 if value else 0x00) | (0x00 if good else 0x80)
    return dui + _encode_ioa(ioa) + bytes((siq,)) + _encode_cp56time2a(timestamp)


def encode_asdu_float_t(
    *, common_address: int, cause: int, ioa: int,
    value: float, good: bool = True, originator: int = 0,
    timestamp: datetime | None = None,
) -> bytes:
    """TypeID 36 — short float + QDS + CP56Time2a (3+4+1+7 = 15 byte info object)."""
    dui = _encode_dui(
        type_id=TYPE_M_ME_TF_1, num_ix=1, sq=False,
        cause=cause, originator=originator, common_address=common_address,
    )
    qds = 0x00 if good else 0x80
    payload = struct.pack("<f", float(value)) + bytes((qds,))
    return dui + _encode_ioa(ioa) + payload + _encode_cp56time2a(timestamp)


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


# ---------------------------------------------------------------------------
# C_SC_NA_1 (tip 45) — tek noktali komut
#
# KAPSAM (urun karari): IEC 104 uzerinden kabul edilen TEK kontrol komutu
# ariza gostergesi RESET'idir. Analog cikis / setpoint (C_SE_NA/NB/NC) ve
# diger kontrol tipleri kapsam DISIDIR; server onlara negatif onay doner.
# ---------------------------------------------------------------------------

#: SCO bayti icindeki S/E (select/execute) biti. 1 = SELECT, 0 = EXECUTE.
SCO_SELECT_BIT = 0x80
#: SCO bayti icindeki SCS (komut durumu) biti. Reset icin 1 (ON) beklenir.
SCO_ON_BIT = 0x01


@dataclass(frozen=True)
class SingleCommand:
    """Cozulmus C_SC_NA_1 nesnesi."""

    common_address: int
    ioa: int
    #: True = SELECT asamasi (henuz uygulanmaz), False = EXECUTE.
    select: bool
    #: SCS biti — komutun ON/OFF yonu.
    on: bool
    #: Ham SCO bayti; onay cercevesinde AYNEN geri yansitilir.
    sco: int


def parse_single_command(asdu: bytes) -> SingleCommand | None:
    """C_SC_NA_1 ASDU'sunu cozer. Bicim bozuksa None.

    Bicim: [tip][vsq][cot][oa][ca:2][ioa:3][sco:1] = 10 bayt.
    Yalnizca TEK nesne (num_ix=1, sq=0) desteklenir — reset komutu icin
    coklu nesne anlamli degil ve kabul etmek kapsami sessizce genisletirdi.
    """
    if len(asdu) < 10 or asdu[0] != TYPE_C_SC_NA_1:
        return None
    vsq = asdu[1]
    if vsq & 0x80:            # SQ=1 (ardisik adresleme) desteklenmiyor
        return None
    if (vsq & 0x7F) != 1:     # tek nesne disi kabul edilmiyor
        return None
    common_address = asdu[4] | (asdu[5] << 8)
    ioa = asdu[6] | (asdu[7] << 8) | (asdu[8] << 16)
    sco = asdu[9]
    return SingleCommand(
        common_address=common_address,
        ioa=ioa,
        select=bool(sco & SCO_SELECT_BIT),
        on=bool(sco & SCO_ON_BIT),
        sco=sco,
    )


def encode_single_command_reply(
    *, common_address: int, ioa: int, sco: int, cause: int,
    originator: int = 0, negative: bool = False,
) -> bytes:
    """C_SC_NA_1 icin onay/sonlandirma cercevesi.

    IEC 60870-5-101 §7.3.2: onay, ALINAN komut nesnesini aynen yansitir;
    yalnizca COT degisir (ACT_CON=7, ACT_TERM=10) ve reddedilmisse P/N biti
    (0x40) kurulur. Master komutu bu yansimayla eslestirir; IOA veya SCO
    degistirilirse eslestiremez ve komut TIMEOUT'a duser.
    """
    dui = _encode_dui(
        type_id=TYPE_C_SC_NA_1, num_ix=1, sq=False,
        cause=cause, originator=originator, common_address=common_address,
        negative=negative,
    )
    return dui + _encode_ioa(ioa) + bytes((sco & 0xFF,))
