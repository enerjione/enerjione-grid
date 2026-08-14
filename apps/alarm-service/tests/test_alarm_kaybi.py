"""Alarmin SESSIZCE kaybolmasini kapatan bekciler (denetim 2026-08-13).

Uc ayri kusur, ayni sonuc: alarm uretiliyor ama operatore HIC ulasmiyor ve
hicbir yerde "kayboldu" demiyor. Bir ariza izleme urununde en agir sinif bu.
"""

from __future__ import annotations

import alarm_service.main as m
from alarm_service.rules import AlarmRule


# --------------------------------------------------------------------------
# 1) Backend POST'u basarisiz olan RAISE, durumu geri alip yeniden denenmeli
# --------------------------------------------------------------------------
def test_raise_backend_hatasinda_durum_GERI_ALINIR(monkeypatch):
    """POST patlarsa aktiflik isareti geri alinmali.

    Eskiden: cagiran taraf `_STATE.set_active(key, True)` diyor, sonra POST
    patliyor, hata yalnizca ekrana basiliyordu. Durum "aktif" kaldigi icin
    SONRAKI telemetri de tetiklemiyordu -> alarm satiri hic olusmuyor,
    marker yesil kaliyor, produces_fault alarmi olmadigi icin hat arizasi
    acilmiyor, bildirim gitmiyordu. `update.sh backend` sirasindaki 30-60
    saniyede olusan her alarm boyle kayboluyordu.
    """
    notifier = m._BackendNotifier()
    geri_alindi: list[bool] = []

    def patlayan_notify(payload, http=None):
        raise ConnectionError("backend yeniden basliyor")

    monkeypatch.setattr(m, "_notify_backend", patlayan_notify)
    monkeypatch.setattr(m, "RAISE_RETRY_BEKLEME_SN", 0.0)

    job = ("raise", {"device_code": "DEV-1", "signal_key": "s"}, 7,
           lambda: geri_alindi.append(True))
    notifier._isle(http=None, job=job)

    assert geri_alindi == [True], (
        "POST kalici basarisiz oldugunda durum geri alinmadi — alarm bir daha "
        "hic denenmez ve sessizce kaybolur."
    )
    assert notifier._dusen_raise == 1


def test_raise_basarisizsa_RABBITMQ_YAYINI_YAPILMAZ(monkeypatch):
    """alarm_id yoksa RabbitMQ'ya basmak, DB'de karsiligi olmayan bildirim uretir."""
    notifier = m._BackendNotifier()
    yayinlandi: list[dict] = []

    monkeypatch.setattr(
        m, "_notify_backend",
        lambda payload, http=None: (_ for _ in ()).throw(ConnectionError("yok")),
    )
    monkeypatch.setattr(m, "_publish_alarm_to_rabbitmq", lambda p: yayinlandi.append(p))
    monkeypatch.setattr(m, "RAISE_RETRY_BEKLEME_SN", 0.0)

    notifier._isle(http=None, job=("raise", {"device_code": "D"}, 1, None))

    assert yayinlandi == []


def test_raise_ilk_denemede_patlayip_ikincide_BASARILI_olabilir(monkeypatch):
    """Anlik ConnectionError'lar tek bir hizli tekrarla kurtarilir."""
    notifier = m._BackendNotifier()
    denemeler = {"n": 0}

    def bazen_patlar(payload, http=None):
        denemeler["n"] += 1
        if denemeler["n"] == 1:
            raise ConnectionError("anlik")
        return 4242

    monkeypatch.setattr(m, "_notify_backend", bazen_patlar)
    monkeypatch.setattr(m, "_publish_alarm_to_rabbitmq", lambda p: None)
    monkeypatch.setattr(m, "RAISE_RETRY_BEKLEME_SN", 0.0)

    payload = {"device_code": "D"}
    notifier._isle(http=None, job=("raise", payload, 1, None))

    assert denemeler["n"] == 2
    assert payload["alarm_id"] == 4242
    assert notifier._dusen_raise == 0


# --------------------------------------------------------------------------
# 2) comm_loss KAYDI YOKSA alarm yine de uretilmeli (kurulum eksigi)
# --------------------------------------------------------------------------
def _comm_alarmi_uretildi_mi(monkeypatch, *, kayit_var: bool, kural) -> bool:
    raised: list[dict] = []

    class SahteNotifier:
        def submit_raise(self, payload, *, rule_id, geri_al=None):
            raised.append(payload)

        def submit_clear(self, **alan):
            pass

    monkeypatch.setattr(m, "_NOTIFIER", SahteNotifier())
    monkeypatch.setattr(m._CACHE, "comm_rule", lambda: kural)
    monkeypatch.setattr(m._CACHE, "comm_kaydi_var", lambda: kayit_var)
    monkeypatch.setattr(m._CACHE, "is_ready", lambda: True)
    m._QUALITY_STATE._bad.clear()

    m._process_device_comm_alarm(
        {"device_code": "DEV-X", "quality": "comm_lost", "value": 0.0}
    )
    return bool(raised)


def _kural(*, aktif: bool) -> AlarmRule:
    return AlarmRule(
        id=1,
        signal_key="__comm_loss__",
        name="Haberleşme arızası",
        description="",
        level="critical",
        comparator="eq",
        threshold=0.0,
        threshold_high=None,
        hysteresis=0.0,
        debounce_sec=0,
        device_code_filter=None,
        device_model_filter=None,
        is_active=aktif,
        produces_fault=False,
        rule_kind="comm_loss",
        expression=None,
        composite_signal_keys=(),
    )


def test_comm_KAYDI_YOKSA_alarm_URETILIR(monkeypatch):
    """Kayit yoklugu operator karari degil KURULUM EKSIGIDIR.

    Temiz kurulum `create_all` + `stamp head` yapiyor, yani standart kurali
    tohumlayan migration 0058 HIC kosmuyor ve `alarm_rules` bos kaliyor.
    Eskiden alarm-service bunu "operator kapatmis" sanip TUM haberlesme
    alarmlarini kalici olarak susturuyordu: cihaz hattan dusse bile ne alarm
    aciliyor ne SMS/Telegram/e-posta gidiyordu.
    """
    assert _comm_alarmi_uretildi_mi(monkeypatch, kayit_var=False, kural=None) is True


def test_comm_kaydi_PASIFSE_alarm_URETILMEZ(monkeypatch):
    """Operator bilerek kapattiysa alarm uretilmemeli — bu ayrim korunmali."""
    assert (
        _comm_alarmi_uretildi_mi(monkeypatch, kayit_var=True, kural=_kural(aktif=False))
        is False
    )


def test_comm_kaydi_AKTIFSE_alarm_URETILIR(monkeypatch):
    assert (
        _comm_alarmi_uretildi_mi(monkeypatch, kayit_var=True, kural=_kural(aktif=True))
        is True
    )


# --------------------------------------------------------------------------
# 3) Composite kuralda raise ve clear AYNI signal_key'i tasimali
# --------------------------------------------------------------------------
def test_composite_raise_KURALIN_signal_keyini_kullanir():
    """Backend acik alarmi (rule_id, device_code, signal_key) ile buluyor.

    Raise tetikleyen telemetrinin signal_key'i ile, clear ise kuralin anchor
    signal_key'i ile gidiyordu. Composite kuralda tetikleyen terim anchor'dan
    FARKLI bir sinyal olabildigi icin ikisi ayrisiyor ve alarm ASLA
    kapanmiyordu — listede sonsuza kadar acik kaliyordu.
    """
    payload = {
        "device_code": "DEV-9",
        "signal_key": "master.actual_current",  # tetikleyen terim
        "device_id": 9,
    }
    alarm = m._build_alarm_from_rule(
        payload,
        rule_id=5,
        rule_name="Asiri yuk",
        rule_description="",
        level="critical",
        value=142.0,
        signal_key="master.actual_voltage",  # kuralin anchor'i
    )
    assert alarm["signal_key"] == "master.actual_voltage"


def test_signal_key_verilmezse_payloadtaki_kullanilir():
    """Geriye uyum: kural anchor'i verilmediginde eski davranis surer."""
    alarm = m._build_alarm_from_rule(
        {"device_code": "D", "signal_key": "master.temp"},
        rule_id=1,
        rule_name="x",
        rule_description="",
        level="warning",
        value=1.0,
    )
    assert alarm["signal_key"] == "master.temp"
