"""Disari cikan metinlerdeki saatleri YEREL saate cevirir — TEK KAYNAK.

YASANAN SORUN
-------------
Saat 11:00'de olusan bir alarmin WhatsApp mesajinda 08:00 yaziyordu.

Sistem zamani her yerde UTC-aware saklar (bkz. CLAUDE.md) ve bu DOGRU:
alarm sirasi, SLA olcumu ve dedup buna dayanir. Ama disari cikan metin
(WhatsApp / Telegram / SMS / e-posta) sahadaki insana gider ve o kisi duvar
saatine bakar. Arayuz bu donusumu tarayicinin saat diliminde kendisi
yapiyordu; sunucudan cikan metinlerde ise yapacak kimse yoktu ve ham UTC
degeri basiliyordu.

KURAL: UTC saklamaya devam; donusum YALNIZCA bicimlendirme aninda.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone, tzinfo

from app.core.config import settings

_cache: tzinfo | None = None


def display_tz() -> tzinfo:
    """Gosterim saat dilimi. IANA yoksa sabit farka duser.

    Sonuc onbelleklenir: her mesaj satirinda zoneinfo aramasi yapilmasin.
    """
    global _cache
    if _cache is not None:
        return _cache
    try:
        from zoneinfo import ZoneInfo

        _cache = ZoneInfo(settings.display_timezone)
    except Exception:  # noqa: BLE001 - tzdata yok / gecersiz ad
        # Minimal imajda IANA veritabani bulunmayabilir. Sabit fark Turkiye
        # icin dogru sonuc verir (2016'dan beri kalici UTC+3, yaz saati yok).
        _cache = timezone(timedelta(minutes=settings.display_utc_offset_minutes))
    return _cache


def to_local(dt: datetime | None) -> datetime | None:
    """UTC-aware (ya da naive-UTC) bir zamani gosterim saatine cevirir."""
    if dt is None:
        return None
    # Naive gelen deger UTC kabul edilir: DB'den tz bilgisi dusmus olabilir
    # (sqlite) ve yerel saat varsaymak degeri sessizce 3 saat kaydirirdi.
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(display_tz())


def fmt_local(dt: datetime | None, fmt: str = "%d.%m.%Y %H:%M") -> str:
    yerel = to_local(dt)
    return "-" if yerel is None else yerel.strftime(fmt)
