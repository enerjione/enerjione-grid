"""B2 — cihaz olay zaman damgasi.

TASARIMIN TEMEL KARARI: `source_timestamp`'in ANLAMI DEGISMEDI.

Gateway ekibi "artik cihaz zamani olsun" onerdi; reddedildi cunku o alan
ayni anda telemetry_history PK'sinin parcasi, hypertable partition kolonu,
retention silme kriteri ve continuous aggregate ekseni. Anlamini degistirmek
somut felaketler uretirdi:

  * gecmise damgali satir historian INSERT'ini patlatir -> TUM telemetri durur
  * ileriye damgali satir `telemetry`'de OLUMSUZ olur (retention goremez)
  * ayni saniyeye dusen "ariza gecti / ariza kalkti" cifti AYNI PK'ya duser
    ve ikincisi sessizce kaybolur — B2'nin korumak istedigi veri yok olur
  * RTC pili biten cihaz 2000-01-01 damgalar, olcum bir gun icinde silinir

Bunun yerine AYRI, nullable bir kolon eklendi. Kotu bir cihaz saati artik
yalnizca analiz verisini bozar; depolamayi ETKILEMEZ. Bu testler o siniri
kilitler.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from app.services.device_clock_service import (
    TS_INVALID,
    TS_SYNCHRONIZED,
    TS_UNSYNCHRONIZED,
    assess_device_timestamp,
)

SIMDI = datetime(2026, 7, 31, 12, 0, 0, tzinfo=timezone.utc)


# ------------------------------------------------------------ eski gateway


def test_damga_yoksa_davranis_DEGISMEZ():
    """0.4.x gateway bu alani gondermez — None donmeli, hata olmamali."""
    assert assess_device_timestamp(None, now=SIMDI) == (None, None)


def test_eski_gateway_payloadi_semadan_GECER():
    from app.schemas.telemetry import TelemetryIn

    t = TelemetryIn(
        device_code="DEV1",
        signal_key="master.current",
        value=1.0,
        source_timestamp=SIMDI,
    )
    assert t.device_event_at is None
    assert t.timestamp_quality is None


# ------------------------------------------------------------ makul damga


def test_makul_damga_oldugu_gibi_saklanir():
    olay = SIMDI - timedelta(minutes=3)
    ts, kalite = assess_device_timestamp(olay, now=SIMDI)
    assert ts == olay
    assert kalite is None  # gateway bir sey bildirmediyse uydurmuyoruz


def test_gateway_kalitesi_TASINIR():
    olay = SIMDI - timedelta(seconds=30)
    assert assess_device_timestamp(
        olay, reported_quality="synchronized", now=SIMDI
    ) == (olay, TS_SYNCHRONIZED)
    assert assess_device_timestamp(
        olay, reported_quality="UNSYNCHRONIZED", now=SIMDI
    ) == (olay, TS_UNSYNCHRONIZED)


def test_kopma_sonrasi_biriken_olaylar_KABUL_edilir():
    """4G kopmasi sonrasi event buffer gunlerce birikebilir.

    Pencere geriye 7 gun: gercek ve degerli olay verisini atmiyoruz. B2'nin
    varlik sebebi tam olarak bu senaryo.
    """
    olay = SIMDI - timedelta(days=3)
    ts, kalite = assess_device_timestamp(olay, now=SIMDI)
    assert ts == olay
    assert kalite != TS_INVALID


# ------------------------------------------------------- bozuk cihaz saati


def test_RTC_resetlenmis_cihaz_INVALID_isaretlenir():
    """Pili biten RTC 2000-01-01'e doner.

    Damga YINE SAKLANIR — teshis icin onemli: "2000-01-01" gormek pilin
    bittigini soyler, None gormek hicbir sey soylemez.
    """
    olay = datetime(2000, 1, 1, tzinfo=timezone.utc)
    ts, kalite = assess_device_timestamp(olay, now=SIMDI)
    assert ts == olay, "ham deger atildi — teshis bilgisi kayboldu"
    assert kalite == TS_INVALID


def test_gelecege_damgali_olay_INVALID():
    """Gelecege damgali olay fizik olarak anlamsizdir."""
    olay = SIMDI + timedelta(hours=2)
    _ts, kalite = assess_device_timestamp(olay, now=SIMDI)
    assert kalite == TS_INVALID


def test_kucuk_ileri_kayma_TOLERE_edilir():
    """Birkac saniyelik saat kaymasi/gecikme invalid sayilmamali."""
    olay = SIMDI + timedelta(seconds=30)
    _ts, kalite = assess_device_timestamp(olay, now=SIMDI)
    assert kalite != TS_INVALID


def test_cihaz_kendi_saatinin_bozuk_oldugunu_soylerse_GUVENILIR():
    """Cihazin kendi bildirimi bizim tahminimizden iyidir."""
    olay = SIMDI - timedelta(seconds=10)  # makul gorunuyor
    _ts, kalite = assess_device_timestamp(
        olay, reported_quality="invalid", now=SIMDI
    )
    assert kalite == TS_INVALID


def test_naive_datetime_UTC_kabul_edilir():
    """Naive damga karsilastirmada patlamamali."""
    olay = datetime(2026, 7, 31, 11, 59, 0)  # tzinfo yok
    ts, _kalite = assess_device_timestamp(olay, now=SIMDI)
    assert ts is not None and ts.tzinfo is not None


# --------------------------------------------------- SINIR: PK'ya dokunma


def test_device_event_at_PK_NIN_PARCASI_DEGIL():
    """En kritik yapisal koruma.

    Biri ileride `device_event_at`i birincil anahtara veya partition'a
    eklerse, B2'nin kacindigi TUM felaketler geri gelir. Bu test o adimi
    kirmizi yapar.
    """
    from app.models.telemetry_history import TelemetryHistory

    pk_kolonlari = {c.name for c in TelemetryHistory.__table__.primary_key.columns}
    assert pk_kolonlari == {"device_id", "signal_key", "source_timestamp"}, (
        f"telemetry_history PK degismis: {sorted(pk_kolonlari)} — "
        "device_event_at PK'ya girerse bozuk cihaz saati depolamayi bozar"
    )
    assert "device_event_at" not in pk_kolonlari
    assert "timestamp_quality" not in pk_kolonlari


def test_yeni_kolonlar_NULLABLE():
    """Zorunlu olsalardi eski gateway'lerin telemetrisi yazilamazdi."""
    from app.models.telemetry_history import TelemetryHistory

    kolonlar = TelemetryHistory.__table__.columns
    assert kolonlar["device_event_at"].nullable is True
    assert kolonlar["timestamp_quality"].nullable is True
