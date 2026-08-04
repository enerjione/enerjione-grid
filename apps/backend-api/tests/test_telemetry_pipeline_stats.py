"""Telemetri boru hatti olcum katmani testleri.

NEDEN BU OLCUM VAR (ve neden testi onemli):
Stream `discard=old` ile calisiyor — tampon dolarsa EN ESKI mesajlar
SESSIZCE dusuruluyor. "Sistem durmasin" tercihinin bedeli bu sessizlik;
onu tehlikeli olmaktan cikaran TEK sey tasmaya yaklasildigini gosteren bu
olcum. Olcum bozulursa kimse fark etmez — cunku bozuk bir gosterge de
"her sey yolunda" der.

Ayrica olcek karari (tek worker yetiyor mu, tuketiciyi ayri container'a
tasimali miyiz) bu sayilara dayanacak.
"""

from __future__ import annotations

import pytest

from app.core.config import settings
from app.services import telemetry_consumer as tc


@pytest.fixture(autouse=True)
def _reset_stats():
    """Her test temiz sayaclarla baslasin (modul duzeyi durum paylasiliyor)."""
    with tc._stats_lock:
        tc._stats.update(
            running=False,
            connected=False,
            last_fetch_at=None,
            last_batch_size=0,
            last_batch_duration_sec=0.0,
            backlog=None,
            processed_total=0,
            bad_total=0,
            reconnects=0,
            last_error=None,
        )
        tc._throughput_window.clear()
    # None = "henuz hic uyarilmadi". 0.0 yazmak testleri makinenin UPTIME'ina
    # bagimli kilardi: monotonic() sabit bir baslangic noktasi vermez ve Linux'ta
    # acilistan beri gecen suredir. Yeni acilmis bir CI konteynerinde
    # `now - 0.0 < interval` cikip uyari bastiriliyordu — bu testler tam olarak
    # oyle kirmizi oldu.
    tc._last_backlog_warn_at.clear()
    tc._hat_stats.clear()
    tc._kayip_isareti.clear()
    tc._son_kayip_denetimi.clear()
    yield


def test_stats_start_empty():
    s = tc.get_stats()
    assert s["running"] is False
    assert s["backlog"] is None
    assert s["throughput_msgs_per_sec"] == 0.0


def test_batch_updates_counters():
    tc._stats_record_batch(sinif=tc.SINIF_ONCELIKLI, size=500, duration=0.25, backlog=1200, bad=2)

    s = tc.get_stats()
    assert s["processed_total"] == 500
    assert s["bad_total"] == 2
    assert s["backlog"] == 1200
    assert s["last_batch_size"] == 500
    assert s["last_batch_duration_sec"] == 0.25
    assert s["last_fetch_at"] is not None


def test_counters_accumulate_across_batches():
    tc._stats_record_batch(sinif=tc.SINIF_ONCELIKLI, size=100, duration=0.1, backlog=10, bad=0)
    tc._stats_record_batch(sinif=tc.SINIF_ONCELIKLI, size=250, duration=0.2, backlog=5, bad=1)

    s = tc.get_stats()
    assert s["processed_total"] == 350
    assert s["bad_total"] == 1
    assert s["backlog"] == 5, "backlog EN SON degeri yansitmali, toplam degil"


def test_backlog_none_does_not_overwrite_last_known():
    """metadata okunamazsa (backlog=None) bilinen deger KORUNMALI.

    Aksi halde tek bir okunamayan batch gostergeyi sifirlar ve 'backlog yok'
    gibi gorunur — tam da yanlis guven veren durum.
    """
    tc._stats_record_batch(sinif=tc.SINIF_ONCELIKLI, size=10, duration=0.1, backlog=9999, bad=0)
    tc._stats_record_batch(sinif=tc.SINIF_ONCELIKLI, size=10, duration=0.1, backlog=None, bad=0)

    assert tc.get_stats()["backlog"] == 9999


def test_throughput_is_computed():
    for _ in range(4):
        tc._stats_record_batch(sinif=tc.SINIF_ONCELIKLI, size=100, duration=0.05, backlog=0, bad=0)

    s = tc.get_stats()
    assert s["throughput_msgs_per_sec"] > 0, "throughput hesaplanmadi"


# ------------------------------------------------------------ esik uyarisi


def test_backlog_below_threshold_does_not_warn(monkeypatch):
    monkeypatch.setattr(settings, "telemetry_backlog_warn_threshold", 50_000)
    calls = []
    monkeypatch.setattr(tc, "get_stats", lambda: {"throughput_msgs_per_sec": 1.0})
    monkeypatch.setattr(tc.logger, "warning", lambda *a, **k: calls.append(a))

    tc._warn_if_backlog_high(tc.SINIF_ONCELIKLI, 49_999)

    assert calls == [], "esigin altinda uyari uretildi"


def test_backlog_above_threshold_warns(monkeypatch):
    monkeypatch.setattr(settings, "telemetry_backlog_warn_threshold", 1_000)
    monkeypatch.setattr(settings, "telemetry_backlog_warn_interval_sec", 300)
    calls = []
    monkeypatch.setattr(tc.logger, "warning", lambda *a, **k: calls.append(a))
    # Olay kaydi DB ister; bu testte ilgilenmiyoruz.
    monkeypatch.setattr(tc, "SessionLocal", lambda: (_ for _ in ()).throw(RuntimeError("db yok")))

    tc._warn_if_backlog_high(tc.SINIF_ONCELIKLI, 5_000)

    assert len(calls) == 1, "esik asildi ama uyari uretilmedi"


def test_warning_is_rate_limited(monkeypatch):
    """Backlog saatlerce yuksek kalsa bile system_events dolmamali."""
    monkeypatch.setattr(settings, "telemetry_backlog_warn_threshold", 1_000)
    monkeypatch.setattr(settings, "telemetry_backlog_warn_interval_sec", 300)
    calls = []
    monkeypatch.setattr(tc.logger, "warning", lambda *a, **k: calls.append(a))
    monkeypatch.setattr(tc, "SessionLocal", lambda: (_ for _ in ()).throw(RuntimeError("db yok")))

    for _ in range(10):
        tc._warn_if_backlog_high(tc.SINIF_ONCELIKLI, 5_000)

    assert len(calls) == 1, f"rate-limit calismadi: {len(calls)} uyari uretildi"


def test_none_backlog_never_warns(monkeypatch):
    monkeypatch.setattr(settings, "telemetry_backlog_warn_threshold", 1)
    calls = []
    monkeypatch.setattr(tc.logger, "warning", lambda *a, **k: calls.append(a))

    tc._warn_if_backlog_high(tc.SINIF_ONCELIKLI, None)

    assert calls == []


# ------------------------------------------------------------------ severity


def _report(monkeypatch, **stats):
    """Endpoint'i cagirip severity'sini doner (auth bagimliligi olmadan)."""
    from app.api.system_status import get_telemetry_pipeline_status

    base = {
        "running": True,
        "connected": True,
        "backlog": 0,
        "throughput_msgs_per_sec": 10.0,
        "last_batch_size": 1,
        "last_batch_duration_sec": 0.1,
        "last_fetch_at": None,
        "processed_total": 1,
        "bad_total": 0,
        "reconnects": 0,
        "last_error": None,
    }
    base.update(stats)
    monkeypatch.setattr(tc, "get_stats", lambda: base)
    return get_telemetry_pipeline_status(_=None)


def test_severity_ok_when_healthy(monkeypatch):
    assert _report(monkeypatch).severity == "ok"


def test_severity_warning_when_backlog_high(monkeypatch):
    monkeypatch.setattr(settings, "telemetry_backlog_warn_threshold", 1_000)
    assert _report(monkeypatch, backlog=5_000).severity == "warning"


def test_severity_critical_when_not_connected(monkeypatch):
    """NATS baglantisi yoksa telemetri DB'ye HIC yazilmiyor demektir.

    Bu, yuksek backlog'dan DAHA kotu bir durum: backlog'da veri birikiyor
    ama akiyor; baglanti yoksa hicbir sey islenmiyor.
    """
    assert _report(monkeypatch, connected=False).severity == "critical"


def test_severity_critical_when_not_running(monkeypatch):
    assert _report(monkeypatch, running=False).severity == "critical"


def test_ILK_uyari_makine_UPTIME_inden_BAGIMSIZ(monkeypatch):
    """Uretim hatasi + CI kirmizisi ayni kokten geliyordu.

    `time.monotonic()` sabit bir baslangic noktasi VERMEZ; Linux'ta makine
    acilisindan beri gecen suredir. Rate-limit durumu 0.0 ile baslarsa,
    acilistan sonraki ilk `interval` saniye boyunca
    `now - 0.0 < interval` cikar ve ILK uyari bastirilir — hem de tam
    backlog'un en yuksek oldugu an, yeniden baslatmanin hemen ardindan.

    Windows'ta uptime buyuk oldugu icin bu gorunmuyordu; hatayi yeni ayaga
    kalkmis bir Linux CI konteyneri ortaya cikardi. Bu test o kosulu
    (monotonic() = 42 sn) kalici olarak kilitler.
    """
    monkeypatch.setattr(settings, "telemetry_backlog_warn_threshold", 1_000)
    monkeypatch.setattr(settings, "telemetry_backlog_warn_interval_sec", 300)
    monkeypatch.setattr(tc._time, "monotonic", lambda: 42.0)
    calls = []
    monkeypatch.setattr(tc.logger, "warning", lambda *a, **k: calls.append(a))
    monkeypatch.setattr(tc, "SessionLocal", lambda: (_ for _ in ()).throw(RuntimeError("db yok")))

    tc._warn_if_backlog_high(tc.SINIF_ONCELIKLI, 5_000)

    assert len(calls) == 1, (
        "yeni acilmis makinede ilk backlog uyarisi bastirildi — "
        "operator tam da en kritik anda uyarilmiyor"
    )


def test_rate_limit_ILK_uyaridan_SONRA_isler(monkeypatch):
    """Ilk uyariyi serbest birakmak rate-limit'i bozmamali."""
    monkeypatch.setattr(settings, "telemetry_backlog_warn_threshold", 1_000)
    monkeypatch.setattr(settings, "telemetry_backlog_warn_interval_sec", 300)
    saat = {"t": 42.0}
    monkeypatch.setattr(tc._time, "monotonic", lambda: saat["t"])
    calls = []
    monkeypatch.setattr(tc.logger, "warning", lambda *a, **k: calls.append(a))
    monkeypatch.setattr(tc, "SessionLocal", lambda: (_ for _ in ()).throw(RuntimeError("db yok")))

    tc._warn_if_backlog_high(tc.SINIF_ONCELIKLI, 5_000)
    assert len(calls) == 1

    saat["t"] = 100.0          # 58 sn sonra — pencere icinde
    tc._warn_if_backlog_high(tc.SINIF_ONCELIKLI, 5_000)
    assert len(calls) == 1, "rate-limit calismadi"

    saat["t"] = 400.0          # 358 sn sonra — pencere disi
    tc._warn_if_backlog_high(tc.SINIF_ONCELIKLI, 5_000)
    assert len(calls) == 2, "pencere dolduktan sonra uyari gelmedi"
