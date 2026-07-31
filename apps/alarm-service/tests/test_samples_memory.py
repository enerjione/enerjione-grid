"""Ornek tamponu yalnizca IHTIYAC DUYULAN sinyaller icin dolar (denetim A5).

YASANAN SORUN
-------------
Her telemetri okumasi — composite/agg kurali olsun olmasin — (sinyal, cihaz)
basina 5000 orneklik bir deque'e yaziliyordu. Oysa ornekler YALNIZCA
`kind == "agg"` terimlerinde okunuyor; katalogdaki 175 sinyalin cogunun boyle
bir kurali yok.

OLCEK
-----
200 cihaz x 20 aktif sinyal = 4000 anahtar. Her sinyal ~10 sn'de bir
guncellenirse her deque ~14 saatte maxlen=5000'e doyar -> ~20M tuple, kabaca
1.5-2 GB. Container tavani 512M; alarm-service birkac saatte OOM-kill yiyor.

ASIL ZARAR RESTART DEGIL, SONRASI
---------------------------------
`restart: unless-stopped` servisi geri kaldiriyor ama `_STATE` (aktif alarm
durumu) BELLEKTE tutuluyor ve sifirlaniyor. Acik alarmlar yeniden "yeni alarm"
olarak backend'e POST ediliyor: mukerrer bildirim seli — ve bu birkac saatte
bir tekrarliyor. Yani operatorun telefonu, hicbir sey degismedigi halde ayni
alarmlarla tekrar tekrar caliyor.

TTL temizligi bunu COZMUYORDU: yalnizca 24 saattir veri gelmeyen anahtarlari
atiyor, aktif cihazlarda hicbir sey serbest birakmiyor.
"""

from __future__ import annotations

from alarm_service.rules import (
    AlarmRule,
    AlarmRuleCache,
    CompositeExpression,
    CompositeTerm,
)


def _terim(signal_key: str, kind: str, window: int = 60) -> CompositeTerm:
    return CompositeTerm(
        signal_key=signal_key,
        device_code="*",
        comparator="gt",
        threshold=1.0,
        threshold_high=None,
        kind=kind,
        agg_fn="avg" if kind == "agg" else None,
        agg_window_sec=window,
    )


def _kural(rule_id: int, signal_key: str, terimler) -> AlarmRule:
    return AlarmRule(
        id=rule_id,
        signal_key=signal_key,
        name=f"kural-{rule_id}",
        description="",
        level="warning",
        comparator="gt",
        threshold=1.0,
        threshold_high=None,
        hysteresis=0.0,
        debounce_sec=0,
        device_code_filter=None,
        is_active=True,
        rule_kind="composite",
        expression=CompositeExpression(logic="AND", terms=tuple(terimler)),
    )


def _cache_kur(kurallar_by_signal) -> AlarmRuleCache:
    """`refresh()` HTTP ister; burada sonucunu dogrudan kuruyoruz."""
    cache = AlarmRuleCache(base_url="http://x", service_token="t")
    agg_keys = set()
    max_window = 0
    for kurallar in kurallar_by_signal.values():
        for rule in kurallar:
            if rule.expression is None:
                continue
            for term in rule.expression.terms:
                if term.kind == "agg":
                    agg_keys.add(term.signal_key)
                    max_window = max(max_window, term.agg_window_sec)
    cache._rules_by_signal = kurallar_by_signal
    cache._agg_keys = agg_keys
    cache._max_agg_window_sec = max_window
    cache._ready = True
    return cache


# ---------------------------------------------------------- needs_samples


def test_agg_kurali_olmayan_sinyal_ornek_TUTMAZ():
    """Testin ozu: bellek israfinin kaynagi buydu."""
    cache = _cache_kur(
        {"master.current": [_kural(1, "master.current", [_terim("master.current", "compare")])]}
    )
    assert cache.needs_samples("master.current") is False


def test_agg_kurali_olan_sinyal_ornek_TUTAR():
    cache = _cache_kur(
        {"master.current": [_kural(1, "master.current", [_terim("master.current", "agg")])]}
    )
    assert cache.needs_samples("master.current") is True


def test_hicbir_kurali_olmayan_sinyal_TUTMAZ():
    cache = _cache_kur({})
    assert cache.needs_samples("master.voltage") is False


def test_agg_terimi_BASKA_sinyale_referans_verirse_o_da_tutulur():
    """Composite terim, kuralin ana sinyalinden farkli bir sinyale bakabilir.

    Yalnizca `rule.signal_key`e bakilsaydi o sinyalin ornekleri hic
    biriktirilmez ve kural sessizce HIC tetiklenmezdi.
    """
    cache = _cache_kur(
        {
            "master.trip": [
                _kural(
                    1,
                    "master.trip",
                    [_terim("master.trip", "compare"), _terim("sat01.current", "agg")],
                )
            ]
        }
    )
    assert cache.needs_samples("sat01.current") is True
    assert cache.needs_samples("master.trip") is False


# ------------------------------------------------------ saklama penceresi


def test_saklama_penceresi_EN_UZUN_agg_penceresine_gore():
    cache = _cache_kur(
        {
            "a": [_kural(1, "a", [_terim("a", "agg", window=300)])],
            "b": [_kural(2, "b", [_terim("b", "agg", window=1800)])],
        }
    )
    assert cache.max_agg_window_sec() == 1800


def test_agg_yoksa_pencere_SIFIR():
    cache = _cache_kur(
        {"a": [_kural(1, "a", [_terim("a", "compare")])]}
    )
    assert cache.max_agg_window_sec() == 0


# ------------------------------------------------- yapisal: kosulsuz yazim


def test_put_KOSULSUZ_cagrilmiyor():
    """En kritik yapisal koruma.

    Biri ileride `_SAMPLES.put(...)`i tekrar kosulsuz hale getirirse bellek
    tasmasi geri gelir — ve bu ancak sahada, birkac saat sonra OOM ile fark
    edilir.
    """
    import inspect
    import re
    from pathlib import Path

    kaynak = (
        Path(__file__).resolve().parents[1] / "alarm_service" / "main.py"
    ).read_text(encoding="utf-8")

    # `_SAMPLES.put(` cagrisinin bulundugu satirin ONUNDE bir kosul olmali.
    satirlar = kaynak.splitlines()
    put_satirlari = [i for i, s in enumerate(satirlar) if "_SAMPLES.put(" in s]
    assert put_satirlari, "_SAMPLES.put cagrisi bulunamadi"
    for i in put_satirlari:
        onceki = "\n".join(satirlar[max(0, i - 6) : i])
        assert "needs_samples" in onceki, (
            f"satir {i + 1}: _SAMPLES.put kosulsuz cagriliyor — agg kurali "
            "olmayan sinyaller icin de ornek biriktirilir (bellek tasmasi)"
        )
    _ = inspect, re
