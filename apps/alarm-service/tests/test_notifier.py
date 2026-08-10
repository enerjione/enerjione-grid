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


def test_drift_clear_yolu_tamamen_kaldirildi():
    """Periyodik drift clear O(kural x cihaz) uretiyordu ve 401 cihazda
    gonderim kuyrugunu sinirsiz buyutuyordu. Drift telafisi backend'in
    alarm_reconciliation worker'inda; alarm-service yalnizca GERCEK gecis
    (aktif -> normal) clear'i gonderir."""
    kod = inspect.getsource(m._process_rules_for_payload)
    assert "should_send_clear" not in kod
    assert "DRIFT_CLEAR_INTERVAL_SEC" not in kod
    # Clear yalnizca was_active dalinda gonderilmeli.
    assert "submit_clear" in kod
    # Rate-limit altyapisi da geri gelmesin (olu kod olarak bile).
    assert not hasattr(m._RuleState, "should_send_clear")
    assert not hasattr(m._RuleState, "mark_clear_sent")


def test_transition_clear_hala_gonderiliyor(monkeypatch):
    """Drift yolu kalkarken gercek gecis clear'i YASAMALI: aktif bir alarm
    normale dondugunde submit_clear cagrilir (kalite gecisi kurali: gercek
    olay yutulmaz)."""
    gidenler: list[dict] = []

    class SahteNotifier:
        def submit_raise(self, payload, *, rule_id):
            pass

        def submit_clear(self, **alan):
            gidenler.append(alan)

    monkeypatch.setattr(m, "_NOTIFIER", SahteNotifier())

    class Kural:
        id = 1
        name = "Test Kurali"
        description = "t"
        level = "warning"
        rule_kind = "simple"
        expression = None
        signal_key = "sat01.x"
        comparator = "gt"
        threshold = 100.0
        debounce_sec = 0
        produces_fault = False

    monkeypatch.setattr(m._CACHE, "is_alarmable", lambda sk: True)
    monkeypatch.setattr(m._CACHE, "needs_samples", lambda sk: False)
    # `rules_for` artik uc parametreli: (signal_key, device_code, device_model).
    # Model kapsami eklendi — sinyaller modeller arasinda ortak degil.
    monkeypatch.setattr(m._CACHE, "rules_for", lambda sk, dc, dm=None: [Kural()])
    monkeypatch.setattr(m._CACHE, "device_model", lambda dc: None)
    monkeypatch.setattr(m, "evaluate_rule", lambda rule, value, prev_active: value > 100.0)

    payload = {"signal_key": "sat01.x", "device_code": "DEV1", "value": 150.0, "quality": "good"}
    m._process_rules_for_payload(None, payload)  # aktif olur
    payload["value"] = 50.0
    m._process_rules_for_payload(None, payload)  # normale doner -> clear
    assert len(gidenler) == 1
    assert gidenler[0]["was_active"] is True
    assert gidenler[0]["signal_key"] == "sat01.x"

    # Ikinci normal okuma: gecis yok, drift clear de yok -> yeni is YOK.
    m._process_rules_for_payload(None, payload)
    assert len(gidenler) == 1


# ------------------------------------------------------ haberlesme (comm_lost) alarmi
def test_haberlesme_arizasi_2026_08_07_olayi_regresyon_kilidi(monkeypatch):
    """`_QualityState`/`_build_quality_alarm` tanimliydi ama hicbir yerden
    cagrilmiyordu (2026-08-07): sahada bir cihaz saatlerce haberlesmeden
    dustu, operatore HICBIR bildirim gitmedi. Bu test _process_rules_for_payload
    icinde comm-alarm yolunun GERCEKTEN cagrildigini kilitler."""
    kod = inspect.getsource(m._process_rules_for_payload)
    assert "_process_device_comm_alarm(" in kod
    kod_comm = inspect.getsource(m._process_device_comm_alarm)
    assert "requests." not in kod_comm
    assert "_notify_backend(" not in kod_comm
    assert "_NOTIFIER.submit_raise(" in kod_comm
    assert "_NOTIFIER.submit_clear(" in kod_comm


def test_haberlesme_arizasi_ilk_kotu_kalitede_TEK_alarm_acar(monkeypatch):
    """Ayni cihazdan ardisik comm_lost okumalari TEK alarm uretmeli (dedup);
    saniyede onlarca ayni bildirim SMS/Telegram kotasini tuketmemeli."""
    raised: list[dict] = []

    class SahteNotifier:
        def submit_raise(self, payload, *, rule_id):
            raised.append(payload)

        def submit_clear(self, **alan):
            pass

    monkeypatch.setattr(m, "_NOTIFIER", SahteNotifier())
    m._QUALITY_STATE._bad.clear()  # onceki testlerden sizinti olmasin

    payload = {"device_code": "DEV-COMM", "quality": "comm_lost", "value": 0.0}
    m._process_device_comm_alarm(payload)
    m._process_device_comm_alarm(payload)
    m._process_device_comm_alarm(payload)

    assert len(raised) == 1
    assert raised[0]["title"] == "Haberleşme arızası"
    assert raised[0]["level"] == "critical"
    assert raised[0]["device_code"] == "DEV-COMM"


def test_haberlesme_arizasi_iyi_kaliteye_donunce_clear_gonderilir(monkeypatch):
    """Haberlesme geri gelince (quality=good) acik alarm CLEAR edilmeli —
    tekrar comm_lost gelirse yeni bir alarm ACILABILMELI (state sifirlanir)."""
    raised: list[dict] = []
    cleared: list[dict] = []

    class SahteNotifier:
        def submit_raise(self, payload, *, rule_id):
            raised.append(payload)

        def submit_clear(self, **alan):
            cleared.append(alan)

    monkeypatch.setattr(m, "_NOTIFIER", SahteNotifier())
    m._QUALITY_STATE._bad.clear()

    bad = {"device_code": "DEV-COMM2", "quality": "comm_lost", "value": 0.0}
    good = {"device_code": "DEV-COMM2", "quality": "good", "value": 230.0,
            "signal_key": "master.actual_voltage"}

    m._process_device_comm_alarm(bad)
    assert len(raised) == 1

    # Iyi kaliteye donus -> clear
    m._process_device_comm_alarm(good)
    assert len(cleared) == 1
    assert cleared[0]["rule_title"] == "Haberleşme arızası"
    assert cleared[0]["device_code"] == "DEV-COMM2"
    assert cleared[0]["signal_key"] is None
    assert cleared[0]["was_active"] is True

    # Ardisik iyi okumalar -> ikinci bir clear DAHA gitmemeli (spam yok).
    m._process_device_comm_alarm(good)
    assert len(cleared) == 1

    # Tekrar coktu -> YENI bir alarm acilabilmeli (state dogru sifirlanmis).
    m._process_device_comm_alarm(bad)
    assert len(raised) == 2


def test_haberlesme_alarmi_device_code_yoksa_sessizce_gecer(monkeypatch):
    """device_code bos/None ise ne raise ne clear denenmeli (anahtarsiz
    dedup state'i bozardi)."""
    calls: list[str] = []

    class SahteNotifier:
        def submit_raise(self, payload, *, rule_id):
            calls.append("raise")

        def submit_clear(self, **alan):
            calls.append("clear")

    monkeypatch.setattr(m, "_NOTIFIER", SahteNotifier())
    m._process_device_comm_alarm({"device_code": None, "quality": "comm_lost"})
    m._process_device_comm_alarm({"quality": "good"})
    assert calls == []


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
