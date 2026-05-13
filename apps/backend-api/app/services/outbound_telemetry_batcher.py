"""Telemetry batch dispatcher.

Tasarim sebebi: telemetry her saniye 600 cihaz x N sinyal geliyor. Her reading
icin webhook'a tek POST atmak:
  - Aliciyi serserm yapar (rate limit, queue overflow)
  - Network gereksiz busy
  - "Onceden bu degerdeydi, yine ayni" mesajlari gereksiz

Cozum:
  1. Her telemetry consumer cagrisinda reading'i `submit()` ile buffer'a koy.
  2. Buffer key: (device_code, signal_key). Sadece ONCEKI gonderimden FARKLI
     value'lar gercekten buffer'a yazilir; ayni deger ise atlanir (dedup).
  3. Arka plan thread her FLUSH_WINDOW_SEC'de bir aktif `telemetry`/`all`
     outbound target'lara batch payload yollar:
       {
         "event_kind": "telemetry_batch",
         "count": 12,
         "window_seconds": 5,
         "sent_at": "2026-05-13T08:30:00Z",
         "readings": [{...}, {...}]
       }
  4. Alarm event'leri batch'e GIRMEZ — anlik gonderilir (outbound_dispatch_service
     dispatch_event() ile).

Thread safety: buffer + last_sent dict'leri Lock ile korunur. Flush worker
ayri thread'de, FastAPI lifespan'inde baslar.

IEC 104: bu modul ATLAR — IEC 104 zaten point registry'sini telemetry_consumer
icinde update ediyor (anlik, batch degil).
"""
from __future__ import annotations

import json
import logging
import threading
import time
import urllib.request
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select

from app.db.session import SessionLocal
from app.models.outbound_target import OutboundTarget
from app.services.event_service import record_event

logger = logging.getLogger(__name__)

# Pencere boyutu — bu sure boyunca biriken degisik telemetri batch'lenir.
# Operator tercihi: 5sn (kullanici "5 saniye" sectim dedi).
FLUSH_WINDOW_SEC = 5

# Buffer: key = (device_code, signal_key), value = en son reading dict.
# Ayni device_code+signal_key icin yeni reading eskinin uzerine yazar
# (window icindeki son degeri gonderir, ara degerler iceri girer ama
# flush sirasinda son degerle replace olur).
_buffer: dict[tuple[str, str], dict[str, Any]] = {}

# Son gonderilen degerleri (dedup icin). Yeni reading'in value'su
# burada kayitli son value ile ayni ise buffer'a EKLENMEZ.
_last_sent: dict[tuple[str, str], Any] = {}

_lock = threading.Lock()
_stop_event = threading.Event()
_flusher_thread: threading.Thread | None = None


def submit(reading: dict[str, Any]) -> None:
    """Telemetry consumer'in cagirdigi entry point.

    reading: {
        "message_id": ...,
        "device_code": ...,
        "signal_key": ...,
        "value": ...,
        "value_string": ...,
        "quality": ...,
        "source_timestamp": ...,
        ...
    }
    Sadece value (veya value_string) ONCEKI batch'ten farkliysa buffer'a girer.
    """
    device_code = reading.get("device_code")
    signal_key = reading.get("signal_key")
    if not device_code or not signal_key:
        return
    key = (str(device_code), str(signal_key))

    # Karsilastirilacak deger: numeric value oncelikli, yoksa value_string.
    new_val = reading.get("value")
    if new_val is None:
        new_val = reading.get("value_string")

    with _lock:
        last_val = _last_sent.get(key, _SENTINEL)
        # Ilk kez goruyorsak veya deger degisti ise buffer'a koy.
        if last_val is _SENTINEL or last_val != new_val:
            _buffer[key] = reading


# Module-level sentinel — None gecerli bir telemetry value olabilir.
_SENTINEL = object()


def _drain_buffer() -> list[dict[str, Any]]:
    """Buffer'i bosalt + son gonderim haritasini guncelle. Lock altinda."""
    with _lock:
        if not _buffer:
            return []
        readings = list(_buffer.values())
        # last_sent: gonderilen her reading icin yeni value'yu kaydet.
        for r in readings:
            dc = r.get("device_code")
            sk = r.get("signal_key")
            if not dc or not sk:
                continue
            v = r.get("value")
            if v is None:
                v = r.get("value_string")
            _last_sent[(str(dc), str(sk))] = v
        _buffer.clear()
        return readings


def _send_rest_batch(target: OutboundTarget, payload: dict) -> None:
    """REST POST — outbound_dispatch_service._send_rest ile ayni mantik."""
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    headers = {"Content-Type": "application/json"}
    if target.auth_header and target.auth_token:
        headers[target.auth_header] = target.auth_token
    req = urllib.request.Request(
        target.endpoint, data=data, headers=headers, method="POST"
    )
    with urllib.request.urlopen(req, timeout=15):
        pass


def _send_mqtt_batch(target: OutboundTarget, payload: dict) -> None:
    """MQTT publish — ayni topic, tek mesaj."""
    try:
        import paho.mqtt.publish as mqtt_publish
    except ImportError:  # pragma: no cover
        raise RuntimeError("MQTT publish icin paho-mqtt kurulu degil.") from None
    if not target.topic:
        raise ValueError("MQTT target icin topic zorunlu.")
    mqtt_publish.single(
        target.topic,
        payload=json.dumps(payload, ensure_ascii=False),
        hostname=target.endpoint,
        qos=target.qos,
        retain=target.retain,
    )


def _flush_once() -> None:
    """Buffer'i bosalt ve aktif target'lara gonder."""
    readings = _drain_buffer()
    if not readings:
        return

    # Aktif target'lari DB'den her flush'ta yeniden cek — operator UI'dan
    # target ekleyebilir/silebilir; arada cache invalidation yapmamak icin
    # her flush'ta fresh sorgu. 5sn'de bir tek SELECT — DB icin negligible.
    db = SessionLocal()
    try:
        targets = list(
            db.scalars(
                select(OutboundTarget).where(OutboundTarget.is_active.is_(True))
            ).all()
        )
        # event_filter'i telemetry veya all olanlar + IEC104 olmayanlar
        # (IEC104 zaten consumer'da anlik update yapiyor).
        eligible = [
            t for t in targets
            if t.protocol in ("rest", "mqtt")
            and t.event_filter in ("all", "telemetry")
        ]
        if not eligible:
            return

        payload = {
            "event_kind": "telemetry_batch",
            "count": len(readings),
            "window_seconds": FLUSH_WINDOW_SEC,
            "sent_at": datetime.now(timezone.utc).isoformat(),
            "readings": readings,
        }

        for target in eligible:
            try:
                if target.protocol == "rest":
                    _send_rest_batch(target, payload)
                elif target.protocol == "mqtt":
                    _send_mqtt_batch(target, payload)
                logger.info(
                    "outbound_batch_delivered target=%s protocol=%s count=%d",
                    target.name, target.protocol, len(readings),
                )
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "outbound_batch_failed target=%s protocol=%s count=%d error=%s",
                    target.name, target.protocol, len(readings), exc,
                )
                # DB event log — operator UI'da gorebilsin.
                try:
                    record_event(
                        db,
                        category="outbound",
                        event_type="outbound_batch_failed",
                        severity="warning",
                        message=f"Batch delivery to {target.name} failed: {exc}",
                        metadata={
                            "target": target.name,
                            "protocol": target.protocol,
                            "count": len(readings),
                            "error": str(exc),
                        },
                    )
                    db.commit()
                except Exception:  # noqa: BLE001
                    db.rollback()
    finally:
        db.close()


def _flusher_loop() -> None:
    """Periyodik flush thread'i. Stop event set olana kadar calisir."""
    logger.info(
        "outbound_telemetry_batcher_started window=%ds", FLUSH_WINDOW_SEC
    )
    while not _stop_event.wait(FLUSH_WINDOW_SEC):
        try:
            _flush_once()
        except Exception:  # noqa: BLE001
            logger.exception("outbound_telemetry_batcher_flush_error")
    logger.info("outbound_telemetry_batcher_stopped")


def start() -> None:
    """FastAPI startup hook'tan cagrilir."""
    global _flusher_thread
    if _flusher_thread is not None and _flusher_thread.is_alive():
        return
    _stop_event.clear()
    _flusher_thread = threading.Thread(
        target=_flusher_loop, name="outbound-telemetry-batcher", daemon=True
    )
    _flusher_thread.start()


def stop() -> None:
    """FastAPI shutdown hook'tan cagrilir. Son flush yapilir."""
    _stop_event.set()
    if _flusher_thread is not None:
        _flusher_thread.join(timeout=FLUSH_WINDOW_SEC + 2)
    # Shutdown sirasinda buffer'da kalan readings'i son kez gonder.
    try:
        _flush_once()
    except Exception:  # noqa: BLE001
        logger.exception("outbound_telemetry_batcher_final_flush_error")
