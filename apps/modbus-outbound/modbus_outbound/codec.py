"""Deger -> Modbus register kodlamasi (saf fonksiyonlar, IO yok).

Modbus register'lari 16 bit isaretsiz kelimelerdir; olcum degerleri buraya iki
sekilde sigdirilir:

  int16   : raw = (deger - offset) / scale  -> isaretli 16 bit
            SCADA tarafi ayni scale/offset ile geri cevirir (adres tablosunda
            ve CSV'de bu iki katsayi her sinyal icin verilir).
  float32 : IEEE 754 tek duyarlik, 2 register. Olcek yok, muhendislik birimi
            dogrudan okunur.

Counter'lar formattan bagimsiz olarak 32 bit isaretsiz tamsayidir (2 register);
bir sayaci 16 bite sigdirmak 65535'te sarmasi demek olurdu.

Word sirasi: `big` = yuksek word once (ABCD, Modbus'un dogal siralamasi),
`little` = dusuk word once (CDAB). Cok sayida PLC/SCADA ikincisini bekler,
bu yuzden hedef bazinda secilebilir.
"""

from __future__ import annotations

import math
import struct

INT16_MIN = -32_768
INT16_MAX = 32_767
UINT16_MASK = 0xFFFF
UINT32_MAX = 0xFFFF_FFFF


def clamp_int16(value: float) -> int:
    """Tasmayi sessizce sarmak yerine sinira kilitle.

    Sarma (wrap) olsaydi 40000 A'lik hatali bir okuma SCADA'da -25536 gibi
    tamamen anlamsiz ve inandirici gorunen bir degere donusurdu; sinirda
    kalmak operatore "olcek disi" oldugunu gosterir.
    """
    if value != value or math.isinf(value):  # NaN / inf
        return 0
    rounded = int(round(value))
    return max(INT16_MIN, min(INT16_MAX, rounded))


def to_wire_word(signed: int) -> int:
    """Isaretli 16 bit -> tel uzerindeki isaretsiz gosterim (two's complement)."""
    return signed & UINT16_MASK


def apply_scale(value: float, *, scale: float, offset: float) -> float:
    """Gercek deger -> ham (olceklenmis) deger. real = raw*scale + offset."""
    if scale in (0, None):
        scale = 1.0
    return (value - (offset or 0.0)) / scale


def encode_int16(value: float, *, scale: float, offset: float) -> list[int]:
    return [to_wire_word(clamp_int16(apply_scale(value, scale=scale, offset=offset)))]


def encode_float32(value: float, *, word_order: str = "big") -> list[int]:
    if value != value or math.isinf(value):
        value = 0.0
    high, low = struct.unpack(">HH", struct.pack(">f", float(value)))
    return [high, low] if word_order != "little" else [low, high]


def encode_uint32(value: float, *, word_order: str = "big") -> list[int]:
    if value != value or math.isinf(value):
        value = 0.0
    raw = int(value)
    # Negatif sayac anlamsiz; 0'a kilitle. Ust sinirda sar (sayaclar zaten sarar).
    raw = 0 if raw < 0 else raw & UINT32_MAX
    high = (raw >> 16) & UINT16_MASK
    low = raw & UINT16_MASK
    return [high, low] if word_order != "little" else [low, high]


def encode_registers(
    value: float,
    *,
    data_type: str,
    value_format: str,
    word_order: str,
    scale: float,
    offset: float,
) -> list[int]:
    """Bir olcum degerini register listesine cevir (word_count kadar eleman)."""
    if data_type == "counter":
        return encode_uint32(value, word_order=word_order)
    if value_format == "float32":
        return encode_float32(value, word_order=word_order)
    return encode_int16(value, scale=scale, offset=offset)


def coerce_bool(value) -> bool:
    """Telemetriden gelen degeri bit'e cevir.

    Sahadan bool, 0/1, "true"/"false", "ON"/"OFF" gelebiliyor; hepsi ayni
    sekilde yorumlanmali yoksa bir cihazin ariza biti sessizce hep 0 kalir.
    """
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value != 0
    text = str(value or "").strip().lower()
    return text in ("1", "true", "on", "yes", "closed", "active", "set")


def coerce_number(value) -> float | None:
    """Telemetri degerini sayiya cevir; cevrilemiyorsa None (yazma atlanir)."""
    if isinstance(value, bool):
        return 1.0 if value else 0.0
    if isinstance(value, (int, float)):
        return float(value)
    try:
        return float(str(value).strip())
    except (TypeError, ValueError):
        return None
