"""_BackendNotifier: backend HTTP'nin event loop disina tasinmasi.

YASANAN SORUN (401 cihazlik yuk testi): drift-recovery clear POST'lari
senkron `requests.post` ile asyncio mesaj callback'inin icinde kosuyordu.
Alarm-service CPU %25'te bosta beklerken prio kuyrugu ~1.166 mesaj/sn
birikti; Postgres backend'in no-op clear sorgulariyla %173'e cikti.

Bu testler iki seyi kilitler:
1. Kural degerlendirme dongusu (`_process_rules_for_payload`) HTTP'yi
   DOGRUDAN cagirmaz — isler `_NOTIFIER` kuyruguna gider.
2. Gonderim thread'i sirayi korur (once backend POST -> alarm_id ->
   RabbitMQ publish) ve kuyruk dolunca sessizce takilmaz.
"""

from __future__ import annotations

import inspect
import time

import alarm_service.main as m


# ---------------------------------------------------------------- kaynak kodu
def test_kural_dongusu_dogrudan_http_cagirmaz():
    """Mesaj yolu bloklanmamali: requests.* cagrisi eval dongusunde olmamali."""
    kod = inspect.getsource(m._process_rules_for_payload)
    assert "requests." not in kod
    assert "_notify_backend(" not in kod
    assert "_notify_backend_clear(" not in kod
    assert "_publish_alarm_to_rabbitmq(" not in kod
    assert "_NOTIFIER.submit_raise(" in kod
    assert "_NOTIFIER.submit_clear(" in kod


def test_drift_clear_araligi_60sn_degil():
    """60 sn'lik tempo 400+ cihazda backend'i boguyordu; min 60, default 600."""
    assert m.DRIFT_CLEAR_INTERVAL_SEC >= 600.0
    kod = inspect.getsource(m._process_rules_for_payload)
    assert "DRIFT_CLEAR_INTERVAL_SEC" in kod
    # Eski sabit deger geri gelmesin.
    assert "now, 60.0" not in kod


# ------------------------------------------------------------- worker davranisi
def _bekle(kosul, timeout=5.0):
    son = time.monotonic() + timeout
    while time.monotonic() < son:
        if kosul():
            return True
        time.sleep(0.01)
    return kosul()


def test_raise_sirasi_backend_sonra_rabbitmq(monkeypatch):
    """Once backend POST (alarm_id doner), sonra RabbitMQ publish."""
    sira: list[str] = []

    def sahte_notify(payload, http=None):
        sira.append("backend")
        return 42

    def sahte_publish(payload):
        sira.append(("rabbit", payload.get("alarm_id")))

    monkeypatch.setattr(m, "_notify_backend", sahte_notify)
    monkeypatch.setattr(m, "_publish_alarm_to_rabbitmq", sahte_publish)

    n = m._BackendNotifier(maxsize=10)
    try:
        n.submit_raise({"title": "t"}, rule_id=1)
        assert _bekle(lambda: len(sira) == 2)
        assert sira[0] == "backend"
        assert sira[1] == ("rabbit", 42)  # alarm_id publish'ten ONCE eklendi
    finally:
        n.stop()


def test_clear_isleri_kuyruktan_gonderilir(monkeypatch):
    gidenler: list[dict] = []

    def sahte_clear(http=None, **alan):
        gidenler.append(alan)

    monkeypatch.setattr(m, "_notify_backend_clear", sahte_clear)

    n = m._BackendNotifier(maxsize=10)
    try:
        n.submit_clear(
            rule_id=7, rule_title="Kural", device_code="DEV1",
            source_gateway="gw1", signal_key="sat01.x", was_active=True,
        )
        assert _bekle(lambda: len(gidenler) == 1)
        assert gidenler[0]["rule_id"] == 7
        assert gidenler[0]["signal_key"] == "sat01.x"
    finally:
        n.stop()


def test_kuyruk_dolunca_clear_dusurulur_bloklamaz(monkeypatch):
    """Kuyruk doluyken submit_clear BLOKLAMAMALI (event loop'tan cagriliyor)."""
    n = m._BackendNotifier(maxsize=2)
    # Thread baslarsa kuyrugu bosaltir ve doluluk senaryosu test edilemez;
    # start'i etkisizlestirip kuyrugu elle dolduruyoruz.
    monkeypatch.setattr(n, "start", lambda: None)
    n._q.put_nowait(("clear", {}, False))
    n._q.put_nowait(("clear", {}, False))

    baslangic = time.monotonic()
    n.submit_clear(
        rule_id=1, rule_title="K", device_code=None,
        source_gateway=None, signal_key=None, was_active=False,
    )
    # put_nowait kullanildigi icin aninda donmeli; bloklu put 8sn beklerdi.
    assert time.monotonic() - baslangic < 1.0
    assert n._dusen_clear == 1


def test_backend_hatasi_worker_thread_dusurmez(monkeypatch):
    """POST patlasa da worker yasamali; sonraki isler islenmeli."""
    gidenler: list[int] = []

    def patlayan(payload, http=None):
        raise RuntimeError("backend kapali")

    def sahte_clear(http=None, **alan):
        gidenler.append(alan["rule_id"])

    monkeypatch.setattr(m, "_notify_backend", patlayan)
    monkeypatch.setattr(m, "_publish_alarm_to_rabbitmq", lambda p: None)
    monkeypatch.setattr(m, "_notify_backend_clear", sahte_clear)

    n = m._BackendNotifier(maxsize=10)
    try:
        n.submit_raise({"title": "t"}, rule_id=1)
        n.submit_clear(
            rule_id=2, rule_title="K", device_code=None,
            source_gateway=None, signal_key=None, was_active=False,
        )
        assert _bekle(lambda: gidenler == [2])
    finally:
        n.stop()
