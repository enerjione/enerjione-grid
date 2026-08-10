"""MQTT yayini: topic DOGRU yere gitmeli ve topic SESSIZLESMEMELI.

IKI AYRI ARIZA — ikisi de "broker'daki topic'e yayin yapilmiyor" diye
gorunuyordu:

1. SESSIZLESME. `_flush_once` dedup yapiyor: deger degismediyse gonderme.
   Ama saha sinyallerinin cogu SABIT (seri no, firmware, esik degerleri,
   normal durumdaki ariza bayraklari). Ilk turda bir kez yayinlaniyor,
   sonra topic sonsuza dek susuyor. `retain` de varsayilan kapali oldugu
   icin SONRADAN abone olan bir istemci hicbir sey gormuyor.

2. YANLIS TOPIC. `_resolve_topics_for_reading` docstring'i "legacy
   `topic` alani doluysa onu kullan" diyordu ama kod bunu HIC okumuyordu:
   `_to_snapshot` sablonu bos birakmadigi icin (bos ise default'u koyuyor)
   sablon dali her zaman kazaniyor, operatorun girdigi topic sessizce yok
   sayiliyordu.
"""

from __future__ import annotations

import time

import pytest

from app.services import mqtt_publisher_service as mps
from app.services.mqtt_publisher_service import (
    DEFAULT_MQTT_TOPIC_TEMPLATE,
    _ReadingBuffer,
    _resolve_topics_for_reading,
    _TargetWorker,
    _flush_once,
)


def _snapshot(**over) -> dict:
    base = {
        "id": 1,
        "name": "SCADA",
        "endpoint": "broker.local",
        "event_filter": "all",
        "qos": 0,
        "retain": False,
        "topic": "",
        "mqtt_topic_template": DEFAULT_MQTT_TOPIC_TEMPLATE,
        "mqtt_topic_template_explicit": "",
        "mqtt_topic_prefix": "e1",
        "mqtt_customer_id": "default",
        "mqtt_publish_interval_sec": 10,
    }
    base.update(over)
    return base


def _reading(value=1.0, signal="master.actual_current", device="DEV-001") -> dict:
    return {"device_code": device, "signal_key": signal, "value": value}


# ---- 2) Topic secimi ------------------------------------------------------

def test_operatorun_girdigi_topic_KULLANILIR():
    """Sablon elle girilmediyse legacy `topic` alani kazanmali."""
    snap = _snapshot(topic="saha/olaylar")
    topics = _resolve_topics_for_reading(snap, [], _reading())
    assert [t[0] for t in topics] == ["saha/olaylar"], (
        "operatorun girdigi topic yok sayildi — abone o topic'i dinlerken "
        "yayin baska yere gidiyor"
    )


def test_elle_girilen_sablon_legacy_topicten_ONCELIKLI():
    """Operator sablonu bilincli olarak yazdiysa o kazanir."""
    snap = _snapshot(
        topic="eski/topic",
        mqtt_topic_template="{prefix}/{device}/veri",
        mqtt_topic_template_explicit="{prefix}/{device}/veri",
    )
    topics = _resolve_topics_for_reading(snap, [], _reading())
    assert [t[0] for t in topics] == ["e1/DEV-001/veri"]


def test_ikisi_de_bossa_DEFAULT_sablon():
    topics = _resolve_topics_for_reading(_snapshot(), [], _reading())
    assert topics[0][0] == "e1/default/DEV-001/master/analog/telemetry"


def test_custom_mapping_her_seyi_EZER():
    mapping = {
        "id": 1, "topic": "ozel/{device}", "device_codes": [], "signal_keys": [],
        "qos": 1, "retain": True, "is_active": True,
    }
    topics = _resolve_topics_for_reading(_snapshot(topic="saha/olaylar"), [mapping], _reading())
    assert topics == [("ozel/DEV-001", 1, True)]


def test_onizleme_yayinla_AYNI_topici_gosterir():
    """Ekran bir topic gosterip yayin baskasina giderse teshis imkansizlasir."""
    snap = _snapshot(topic="saha/olaylar")
    worker = _TargetWorker(target_id=1, target_snapshot=snap, mappings=[])
    with mps._workers_lock:  # noqa: SLF001
        mps._workers[1] = worker  # noqa: SLF001
    try:
        onizleme = mps.auto_topics_for_target(1, [{"code": "DEV-001", "name": "F1"}])
        yayin = _resolve_topics_for_reading(snap, [], _reading())[0][0]
    finally:
        with mps._workers_lock:  # noqa: SLF001
            mps._workers.pop(1, None)  # noqa: SLF001
    assert [r["topic"] for r in onizleme if not r["is_custom"]] == [yayin]


# ---- 1) Sessizlesme -------------------------------------------------------

class _SahteSonuc:
    rc = 0


class _SahteClient:
    def __init__(self) -> None:
        self.yayinlar: list[tuple[str, str]] = []

    def publish(self, topic, payload, qos=0, retain=False):  # noqa: ANN001
        self.yayinlar.append((topic, payload))
        return _SahteSonuc()


def _worker_ile_client() -> tuple[_TargetWorker, _SahteClient]:
    client = _SahteClient()
    worker = _TargetWorker(target_id=1, target_snapshot=_snapshot(), mappings=[])
    worker.client = client
    worker.buffer = _ReadingBuffer()
    return worker, client


def test_degismeyen_deger_ayni_turda_TEKRAR_gonderilmez():
    """Dedup korunuyor — her flush'ta ayni degeri basmak gereksiz trafik."""
    worker, client = _worker_ile_client()
    for _ in range(3):
        worker.buffer.readings[("DEV-001", "master.actual_current")] = _reading(12.5)
        _flush_once(worker)
    assert len(client.yayinlar) == 1, client.yayinlar


def test_deger_degisince_HEMEN_gonderilir():
    worker, client = _worker_ile_client()
    for v in (12.5, 12.5, 13.0):
        worker.buffer.readings[("DEV-001", "master.actual_current")] = _reading(v)
        _flush_once(worker)
    assert len(client.yayinlar) == 2, client.yayinlar


def test_SABIT_sinyal_topici_sonsuza_dek_SUSTURMAZ(monkeypatch):
    """Asil ariza: sabit sinyaller ilk turdan sonra hic yayinlanmiyordu.

    `retain` kapali oldugu icin sonradan abone olan istemci bos topic
    goruyor ve "yayin yapilmiyor" diyor.
    """
    worker, client = _worker_ile_client()
    worker.buffer.readings[("DEV-001", "master.serial_number")] = _reading("SN2-1", "master.serial_number")
    _flush_once(worker)
    assert len(client.yayinlar) == 1

    # Deger hic degismiyor ama tazeleme suresi gecti.
    ileri = time.monotonic() + mps.FULL_SNAPSHOT_INTERVAL_SEC + 1
    monkeypatch.setattr(mps.time, "monotonic", lambda: ileri)
    worker.buffer.readings[("DEV-001", "master.serial_number")] = _reading("SN2-1", "master.serial_number")
    _flush_once(worker)

    assert len(client.yayinlar) == 2, (
        "sabit sinyal tazeleme suresi gectigi halde yeniden yayinlanmadi — "
        "topic sonsuza dek sessiz kalir"
    )


def test_tazeleme_suresi_MAKUL():
    """Cok kisa = gereksiz trafik, cok uzun = uzun sessizlik penceresi."""
    assert 60 <= mps.FULL_SNAPSHOT_INTERVAL_SEC <= 900


@pytest.mark.parametrize("interval", [0, None])
def test_flush_clientsiz_PATLAMAZ(interval):
    worker = _TargetWorker(
        target_id=1, target_snapshot=_snapshot(mqtt_publish_interval_sec=interval), mappings=[]
    )
    worker.buffer.readings[("DEV-001", "x")] = _reading()
    _flush_once(worker)  # client None — sessizce donmeli
