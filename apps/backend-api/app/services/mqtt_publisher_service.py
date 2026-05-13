"""MQTT publisher servisi — per-target persistent paho client.

Tasarim:
  - Her aktif MQTT target icin TEK paho.mqtt.Client instance, baglanti
    surekli acik (publish her seferinde yeni TCP/TLS handshake'i yapmaz).
  - Auth (username/password), TLS (CA/cert/key), client_id, keepalive
    target ayarlarindan okunur.
  - Bir thread her N saniyede bir (target.mqtt_publish_interval_sec)
    flush yapar; o pencerede biriken telemetri snapshot'lari publish'lenir.
  - Topic resolver: signal -> mapping varsa mapping topic'i; yoksa
    target.mqtt_topic_template'den render edilir; o da bos ise default.
  - Hot reload: target update'lendiginde client kapanir + yeniden acilir.

Lifecycle:
  start(): FastAPI startup hook'tan cagrilir. DB'den aktif MQTT target'lari
    okur, her biri icin worker baslatir.
  stop(): shutdown'da, son flush + clean disconnect.
  refresh_target(target_id): operator UI'dan target update geldiginde
    cagirilabilir — ilgili worker'i restart eder.

Per-reading flow:
  telemetry_consumer.py her telemetry icin `submit(reading)` cagirir.
  Buffer: in-memory dict, key=(device_code, signal_key), value=reading.
  Ayni key icin yeni reading eskinin uzerine yazar (son snapshot).
  Flush time'da resolver tum readings'i topic'lere ayirir + grupli publish.

NOT: paho-mqtt thread-safe; ayni client'tan birden cok thread publish edebilir.
Yine de target lock ile race kacinilir.
"""
from __future__ import annotations

import json
import logging
import ssl
import threading
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import joinedload

from app.db.session import SessionLocal
from app.models.outbound_target import OutboundTarget, DEFAULT_MQTT_TOPIC_TEMPLATE

logger = logging.getLogger(__name__)

try:
    import paho.mqtt.client as mqtt  # type: ignore
    _PAHO_AVAILABLE = True
except ImportError:  # pragma: no cover
    mqtt = None  # type: ignore
    _PAHO_AVAILABLE = False


@dataclass
class _ReadingBuffer:
    """Per-target buffer: signal key -> en son reading dict."""
    readings: dict[tuple[str, str], dict[str, Any]] = field(default_factory=dict)
    last_sent: dict[tuple[str, str], Any] = field(default_factory=dict)
    lock: threading.Lock = field(default_factory=threading.Lock)


@dataclass
class _TargetWorker:
    """Bir MQTT target icin background publisher worker."""
    target_id: int
    target_snapshot: dict[str, Any]  # OutboundTarget alanlarinin dict'i (DB session bagimsiz)
    mappings: list[dict[str, Any]]   # OutboundTopicMapping listesi
    client: Any = None  # paho.mqtt.client.Client
    buffer: _ReadingBuffer = field(default_factory=_ReadingBuffer)
    thread: threading.Thread | None = None
    stop_event: threading.Event = field(default_factory=threading.Event)
    connected: bool = False


# Global registry — target_id -> _TargetWorker
_workers: dict[int, _TargetWorker] = {}
_workers_lock = threading.Lock()


def _to_snapshot(t: OutboundTarget) -> dict[str, Any]:
    """OutboundTarget row'unu thread-safe dict'e cevir (lazy load engellemek icin)."""
    return {
        "id": t.id,
        "name": t.name,
        "endpoint": t.endpoint,
        "event_filter": t.event_filter,
        "mqtt_port": t.mqtt_port,
        "mqtt_username": t.mqtt_username,
        "mqtt_password": t.mqtt_password,
        "mqtt_client_id": t.mqtt_client_id,
        "mqtt_tls_enabled": bool(t.mqtt_tls_enabled),
        "mqtt_tls_insecure": bool(t.mqtt_tls_insecure),
        "mqtt_tls_ca_path": t.mqtt_tls_ca_path,
        "mqtt_tls_cert_path": t.mqtt_tls_cert_path,
        "mqtt_tls_key_path": t.mqtt_tls_key_path,
        "mqtt_keepalive_sec": t.mqtt_keepalive_sec or 60,
        "mqtt_connect_timeout_sec": t.mqtt_connect_timeout_sec or 10,
        "mqtt_publish_interval_sec": t.mqtt_publish_interval_sec
        if t.mqtt_publish_interval_sec is not None
        else 10,
        "mqtt_topic_template": t.mqtt_topic_template or DEFAULT_MQTT_TOPIC_TEMPLATE,
        "mqtt_topic_prefix": t.mqtt_topic_prefix or "e1",
        "mqtt_customer_id": t.mqtt_customer_id or "default",
        "qos": t.qos or 0,
        "retain": bool(t.retain),
        # Legacy single-topic field — bos ise template kullanilir.
        "topic": t.topic or "",
    }


def _mapping_to_dict(m) -> dict[str, Any]:
    return {
        "id": m.id,
        "topic": m.topic,
        "device_codes": [c.strip() for c in (m.device_codes or "").split(",") if c.strip()],
        "signal_keys": [s.strip() for s in (m.signal_keys or "").split(",") if s.strip()],
        "qos": m.qos,
        "retain": m.retain,
        "is_active": bool(m.is_active),
    }


def _load_target_with_mappings(db, target_id: int) -> tuple[dict[str, Any] | None, list[dict[str, Any]]]:
    t = db.scalar(
        select(OutboundTarget)
        .options(joinedload(OutboundTarget.topic_mappings))
        .where(OutboundTarget.id == target_id)
    )
    if t is None:
        return None, []
    snap = _to_snapshot(t)
    maps = [_mapping_to_dict(m) for m in (t.topic_mappings or []) if m.is_active]
    return snap, maps


# ---------------------------------------------------------------------------
# Topic resolver
# ---------------------------------------------------------------------------

def _resolve_data_type(signal_key: str) -> str:
    """signal_key suffix'inden datatype kategorisi turet — basit heuristic.

    Gercek datatype SignalCatalog'da; ama burada DB sorgulamamak icin
    signal_key'den tahmin: voltage/current/power = analog, status/alarm = binary,
    counter/pulse = counter, vb. Tam dogruluk gerekirse SignalCatalog cache eklenebilir.
    """
    s = signal_key.lower()
    if any(t in s for t in ("voltage", "current", "power", "frequency", "temp", "_v_", "_a_", "_w_")):
        return "analog"
    if any(t in s for t in ("alarm", "status", "open", "close", "trip", "binary", "_b_")):
        return "binary"
    if "counter" in s or "pulse" in s or "count" in s:
        return "counter"
    if "string" in s or "text" in s:
        return "string"
    return "analog"  # default


def _render_topic_template(template: str, *, prefix: str, customer: str, device: str,
                            source: str, datatype: str) -> str:
    """{var} pattern'lerini value'larla degistir. Bilinmeyen var'lar literal kalir.

    Cihaz-bazli yayim: topic seviyesinde sinyal ayrimi YOK; ayni cihazin tum
    sinyalleri (event_filter'a uyan) ayni topic'e tek payload olarak gider.
    Eski sablonlardan kalan {signal} placeholder'i bos string'e indirgenir.
    """
    return (
        template
        .replace("{prefix}", prefix)
        .replace("{customer}", customer)
        .replace("{device}", device)
        .replace("{source}", source)
        .replace("{datatype}", datatype)
        .replace("/{signal}", "")
        .replace("{signal}", "")
    )


def _resolve_topics_for_reading(
    snapshot: dict[str, Any],
    mappings: list[dict[str, Any]],
    reading: dict[str, Any],
) -> list[tuple[str, int, bool]]:
    """Bir reading icin (topic, qos, retain) listesi don.

    1) Custom mapping match varsa onlar (birden fazla olabilir).
    2) Hicbir mapping match etmedi ise default template render.
    3) Legacy: snapshot['topic'] doluysa ve template bos ise tek topic.
    """
    device = str(reading.get("device_code", ""))
    signal = str(reading.get("signal_key", ""))
    source = (reading.get("signal_source") or (signal.split(".", 1)[0] if "." in signal else "")).lower()
    datatype = _resolve_data_type(signal)
    customer = snapshot["mqtt_customer_id"]
    prefix = snapshot["mqtt_topic_prefix"]

    out: list[tuple[str, int, bool]] = []
    matched_any = False
    for m in mappings:
        # device match: bos liste = tum cihazlar; dolu liste = sadece bunlar
        if m["device_codes"] and device not in m["device_codes"]:
            continue
        if m["signal_keys"] and signal not in m["signal_keys"]:
            continue
        topic = _render_topic_template(
            m["topic"], prefix=prefix, customer=customer, device=device,
            source=source, datatype=datatype,
        )
        qos = m["qos"] if m["qos"] is not None else snapshot["qos"]
        retain = m["retain"] if m["retain"] is not None else snapshot["retain"]
        out.append((topic, qos, retain))
        matched_any = True

    if matched_any:
        return out

    # Default template
    template = snapshot["mqtt_topic_template"] or DEFAULT_MQTT_TOPIC_TEMPLATE
    topic = _render_topic_template(
        template, prefix=prefix, customer=customer, device=device,
        source=source, datatype=datatype,
    )
    return [(topic, snapshot["qos"], snapshot["retain"])]


# ---------------------------------------------------------------------------
# MQTT client lifecycle
# ---------------------------------------------------------------------------

def _build_client(snapshot: dict[str, Any]) -> Any:
    """Yeni paho client + connect."""
    if not _PAHO_AVAILABLE:
        raise RuntimeError("paho-mqtt kurulu degil.")
    client_id = snapshot["mqtt_client_id"] or f"e1-{snapshot['name']}-{uuid.uuid4().hex[:8]}"
    client = mqtt.Client(
        callback_api_version=mqtt.CallbackAPIVersion.VERSION2,
        client_id=client_id,
        clean_session=True,
    )
    if snapshot["mqtt_username"]:
        client.username_pw_set(
            snapshot["mqtt_username"], snapshot["mqtt_password"] or None
        )
    if snapshot["mqtt_tls_enabled"]:
        ctx = ssl.create_default_context()
        if snapshot["mqtt_tls_ca_path"]:
            ctx.load_verify_locations(cafile=snapshot["mqtt_tls_ca_path"])
        if snapshot["mqtt_tls_cert_path"] and snapshot["mqtt_tls_key_path"]:
            ctx.load_cert_chain(
                certfile=snapshot["mqtt_tls_cert_path"],
                keyfile=snapshot["mqtt_tls_key_path"],
            )
        if snapshot["mqtt_tls_insecure"]:
            ctx.check_hostname = False
            ctx.verify_mode = ssl.CERT_NONE
        client.tls_set_context(ctx)
    host = snapshot["endpoint"].strip()
    if not host:
        raise ValueError(f"MQTT target endpoint bos: {snapshot['name']}")
    port = snapshot["mqtt_port"] or (8883 if snapshot["mqtt_tls_enabled"] else 1883)
    keepalive = snapshot["mqtt_keepalive_sec"]
    client.connect(host, port, keepalive)
    # paho'nun arka plan thread'i: ping/reconnect/queue isleme
    client.loop_start()
    return client


def _close_client(client) -> None:
    try:
        client.loop_stop()
    except Exception:  # noqa: BLE001
        pass
    try:
        client.disconnect()
    except Exception:  # noqa: BLE001
        pass


# ---------------------------------------------------------------------------
# Per-target worker thread
# ---------------------------------------------------------------------------

def _worker_loop(worker: _TargetWorker) -> None:
    """Periyodik flush thread'i."""
    snap = worker.target_snapshot
    interval = max(1, snap["mqtt_publish_interval_sec"] or 10)
    logger.info(
        "mqtt_publisher_worker_started target=%s endpoint=%s port=%d tls=%s interval=%ds",
        snap["name"], snap["endpoint"],
        snap["mqtt_port"] or (8883 if snap["mqtt_tls_enabled"] else 1883),
        snap["mqtt_tls_enabled"], interval,
    )
    # Initial connect (retry on failure)
    while not worker.stop_event.is_set():
        try:
            worker.client = _build_client(snap)
            worker.connected = True
            break
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "mqtt_publisher_connect_failed target=%s error=%s — 5s sonra tekrar",
                snap["name"], exc,
            )
            if worker.stop_event.wait(5):
                return

    while not worker.stop_event.wait(interval):
        try:
            _flush_once(worker)
        except Exception:  # noqa: BLE001
            logger.exception("mqtt_publisher_flush_error target=%s", snap["name"])

    # Shutdown: son flush + clean disconnect
    try:
        _flush_once(worker)
    except Exception:  # noqa: BLE001
        pass
    if worker.client:
        _close_client(worker.client)
    logger.info("mqtt_publisher_worker_stopped target=%s", snap["name"])


def _flush_once(worker: _TargetWorker) -> None:
    """Buffer'i drain et + dedup + publish."""
    snap = worker.target_snapshot
    if worker.client is None:
        return

    with worker.buffer.lock:
        if not worker.buffer.readings:
            return
        # Dedup: ayni (device, signal) icin son value gonderdiklerimizden farkliysa al
        to_send: list[dict[str, Any]] = []
        for key, reading in worker.buffer.readings.items():
            new_val = reading.get("value")
            if new_val is None:
                new_val = reading.get("value_string")
            last_val = worker.buffer.last_sent.get(key, _SENTINEL)
            if last_val is _SENTINEL or last_val != new_val:
                to_send.append(reading)
                worker.buffer.last_sent[key] = new_val
        worker.buffer.readings.clear()

    if not to_send:
        return

    # Topic'lere grupla: {topic_key: [readings]}
    # topic_key = (topic, qos, retain). Birden fazla reading ayni topic'e duserse
    # tek mesajda batch publish edilir.
    groups: dict[tuple[str, int, bool], list[dict[str, Any]]] = {}
    for r in to_send:
        for topic, qos, retain in _resolve_topics_for_reading(snap, worker.mappings, r):
            groups.setdefault((topic, qos, retain), []).append(r)

    sent = 0
    failed = 0
    for (topic, qos, retain), readings in groups.items():
        payload = {
            "event_kind": "telemetry_batch",
            "count": len(readings),
            "sent_at": datetime.now(timezone.utc).isoformat(),
            "readings": readings,
        }
        try:
            result = worker.client.publish(
                topic, payload=json.dumps(payload, ensure_ascii=False),
                qos=qos, retain=retain,
            )
            if result.rc != 0:
                failed += 1
                logger.warning(
                    "mqtt_publish_failed target=%s topic=%s rc=%d",
                    snap["name"], topic, result.rc,
                )
            else:
                sent += 1
        except Exception as exc:  # noqa: BLE001
            failed += 1
            logger.warning(
                "mqtt_publish_error target=%s topic=%s error=%s",
                snap["name"], topic, exc,
            )

    if sent or failed:
        logger.info(
            "mqtt_publisher_flush target=%s groups=%d sent=%d failed=%d",
            snap["name"], len(groups), sent, failed,
        )


# Module-level sentinel — None de gecerli value olabilir.
_SENTINEL = object()


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def submit_telemetry(reading: dict[str, Any]) -> None:
    """telemetry_consumer.py'in cagirdigi entry point.

    Aktif MQTT target'larin hepsine ayni reading konur (her birinin kendi
    buffer'i + kendi event_filter check). Filter telemetry/all degilse o
    worker'da hicbir sey olmaz (flush'ta zaten resolver butun reading'i ele alir).
    """
    if not _workers:
        return
    device = reading.get("device_code")
    signal = reading.get("signal_key")
    if not device or not signal:
        return
    key = (str(device), str(signal))
    with _workers_lock:
        target_ids = list(_workers.keys())
    for tid in target_ids:
        worker = _workers.get(tid)
        if worker is None:
            continue
        snap = worker.target_snapshot
        if snap["event_filter"] not in ("all", "telemetry"):
            continue
        with worker.buffer.lock:
            worker.buffer.readings[key] = reading


def start() -> None:
    """FastAPI startup hook'tan cagrilir."""
    if not _PAHO_AVAILABLE:
        logger.warning("mqtt_publisher_disabled reason=paho_mqtt_missing")
        return
    db = SessionLocal()
    try:
        targets = list(
            db.scalars(
                select(OutboundTarget)
                .options(joinedload(OutboundTarget.topic_mappings))
                .where(OutboundTarget.is_active.is_(True))
                .where(OutboundTarget.protocol == "mqtt")
            ).unique().all()
        )
    finally:
        db.close()

    for t in targets:
        _spawn_worker(t.id, _to_snapshot(t),
                      [_mapping_to_dict(m) for m in (t.topic_mappings or []) if m.is_active])

    logger.info("mqtt_publisher_started active_targets=%d", len(targets))


def stop() -> None:
    """FastAPI shutdown hook."""
    with _workers_lock:
        worker_list = list(_workers.values())
        _workers.clear()
    for worker in worker_list:
        worker.stop_event.set()
    for worker in worker_list:
        if worker.thread:
            worker.thread.join(timeout=10)


def _spawn_worker(target_id: int, snapshot: dict[str, Any],
                   mappings: list[dict[str, Any]]) -> None:
    worker = _TargetWorker(
        target_id=target_id,
        target_snapshot=snapshot,
        mappings=mappings,
    )
    thread = threading.Thread(
        target=_worker_loop, args=(worker,),
        name=f"mqtt-publisher-{target_id}", daemon=True,
    )
    worker.thread = thread
    with _workers_lock:
        _workers[target_id] = worker
    thread.start()


def refresh_target(target_id: int) -> None:
    """Operator UI'dan target update/create/delete sonrasi cagirilir.

    Mevcut worker varsa durdur + DB'den yeniden oku + yeni worker baslat.
    Target silindiyse veya is_active=False ise sadece durdur.
    """
    if not _PAHO_AVAILABLE:
        return
    # Eski worker'i durdur
    with _workers_lock:
        old = _workers.pop(target_id, None)
    if old:
        old.stop_event.set()
        if old.thread:
            # Detach: join'i baska bir thread'de yap, refresh caller'i bloklamasin
            def _wait_close(w):
                w.thread.join(timeout=10)
            threading.Thread(target=_wait_close, args=(old,), daemon=True).start()

    # Yeni hali oku + aktif MQTT mu kontrol et (tek sorguda)
    db = SessionLocal()
    try:
        t = db.scalar(
            select(OutboundTarget)
            .options(joinedload(OutboundTarget.topic_mappings))
            .where(OutboundTarget.id == target_id)
        )
        if t is None or t.protocol != "mqtt" or not t.is_active:
            return  # Silindi/devre disi/protokol degistirildi → sadece eski worker'i durdurmus olduk
        snap = _to_snapshot(t)
        maps = [_mapping_to_dict(m) for m in (t.topic_mappings or []) if m.is_active]
    finally:
        db.close()

    _spawn_worker(target_id, snap, maps)
    logger.info("mqtt_publisher_refreshed target_id=%d", target_id)
