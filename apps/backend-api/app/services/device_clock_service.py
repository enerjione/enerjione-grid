"""Cihaz olay zaman damgasinin makullugu (B2).

NE ISE YARAR
  Saha cihazlarinin RTC pili biter, saat 2000-01-01'e doner; ya da zaman
  senkronu yapilmamis bir outstation aylik 10-60 sn surukleniyor olur.
  Bu damgalar SOE (olay sirasi) analizini bozar.

NEDEN "SADECE ISARETLE", "REDDETME"
  `device_event_at` depolama mimarisinin HICBIR parcasi degil: ne birincil
  anahtar, ne partition kolonu, ne retention kriteri. Bu yuzden kotu bir
  deger sistemi BOZAMAZ — yalnizca analizi yaniltir. Dolayisiyla dogru
  davranis olcumu REDDETMEK degil, damgayi guvenilmez olarak ISARETLEMEK ve
  ham degeri saklamaktir. Reddetseydik, saati bozuk bir cihazin TUM olay
  gecmisini kaybederdik.

  (Karsilastirma: `source_timestamp` partition kolonu oldugu icin ORADA
  makullik zorunlu olurdu — B2'nin o alana dokunmamasinin sebeplerinden biri
  de budur.)
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

# DNP3 zaman kalitesi sozlugu — gateway'den geldigi gibi tasinir.
TS_SYNCHRONIZED = "synchronized"
TS_UNSYNCHRONIZED = "unsynchronized"
TS_INVALID = "invalid"

# Makul pencere. Cihaz damgasi bu araligin DISINDAYSA guvenilmez sayilir.
#
# GERIYE 7 GUN: outstation event buffer'i 4G kopmasinda gunlerce birikebilir;
# gercek ve degerli veriyi atmamak icin genis tutuluyor.
# ILERIYE 5 DAKIKA: gelecege damgali olay fiziksel olarak anlamsizdir; kucuk
# tolerans yalnizca saat kaymasi/gecikme icindir.
_MAX_PAST = timedelta(days=7)
_MAX_FUTURE = timedelta(minutes=5)


def assess_device_timestamp(
    device_event_at: datetime | None,
    *,
    reported_quality: str | None = None,
    now: datetime | None = None,
) -> tuple[datetime | None, str | None]:
    """(saklanacak_damga, zaman_kalitesi) doner.

    * Damga yoksa (eski gateway) -> (None, None). Davranis degismez.
    * Gateway zaten "invalid" demisse ona GUVENILIR; kendi kontrolumuzu
      yapmayiz — cihazin kendi bildirimi bizim tahminimizden iyidir.
    * Damga makul pencerenin disindaysa deger YINE SAKLANIR ama kalite
      "invalid" olarak isaretlenir. Ham degeri saklamak teshis icin onemli:
      "2000-01-01" gormek RTC pilinin bittigini soyler, None gormek hicbir
      sey soylemez.
    """
    if device_event_at is None:
        return None, _normalize_ts_quality(reported_quality)

    ts = device_event_at
    if ts.tzinfo is None:
        # Naive gelirse UTC kabul et — aksi halde karsilastirma patlar.
        ts = ts.replace(tzinfo=timezone.utc)

    quality = _normalize_ts_quality(reported_quality)
    if quality == TS_INVALID:
        return ts, TS_INVALID  # cihaz zaten "saatim bozuk" diyor

    reference = now or datetime.now(timezone.utc)
    if ts < reference - _MAX_PAST or ts > reference + _MAX_FUTURE:
        return ts, TS_INVALID

    # Gateway bir sey soylemediyse ve damga makulse: "bilinmiyor" demek
    # yerine senkronize VARSAYMIYORUZ — bilgi yoksa None birakiyoruz.
    return ts, quality


def _normalize_ts_quality(raw: str | None) -> str | None:
    if raw is None:
        return None
    value = str(raw).strip().lower()
    if value in (TS_SYNCHRONIZED, TS_UNSYNCHRONIZED, TS_INVALID):
        return value
    # Bilinmeyen token: sessizce yutup None dondurmek yerine "unsynchronized"
    # demek de yanlis olurdu. Bilgiyi kaybetmemek icin ham degeri kirpip
    # geciriyoruz; kolon serbest string.
    return value[:20] or None
