import json
import time
import urllib.request

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.outbound_target import OutboundTarget
from app.services.event_service import record_event
from app.services.iec104.server import manager as iec104_manager

MAX_RETRY = 3
BASE_BACKOFF_SECONDS = 0.7

try:
    import paho.mqtt.publish as mqtt_publish
except ImportError:  # pragma: no cover
    mqtt_publish = None


def dispatch_event(db: Session, *, event_kind: str, payload: dict) -> None:
    stmt = select(OutboundTarget).where(OutboundTarget.is_active.is_(True))
    targets = list(db.scalars(stmt).all())
    for target in targets:
        if target.event_filter not in {"all", event_kind}:
            continue
        # IEC 104: retry/backoff yok; server in-memory register gunceller,
        # sonraki interrogation ya da spontaneous transmission otomatik devreye girer.
        if target.protocol == "iec104":
            _dispatch_iec104(db=db, target=target, event_kind=event_kind, payload=payload)
            continue
        _dispatch_with_retry(db=db, target=target, event_kind=event_kind, payload=payload)


def _dispatch_with_retry(db: Session, *, target: OutboundTarget, event_kind: str, payload: dict) -> None:
    last_error: Exception | None = None
    event_id = payload.get("message_id") or payload.get("event_id") or "unknown"
    correlation_id = payload.get("correlation_id") or event_id
    for attempt in range(1, MAX_RETRY + 1):
        try:
            if target.protocol == "rest":
                _send_rest(target, payload)
            elif target.protocol == "mqtt":
                _send_mqtt(target, payload)
            else:
                raise ValueError(f"Unsupported outbound protocol: {target.protocol}")
            record_event(
                db,
                category="outbound",
                event_type="outbound_delivered",
                severity="info",
                message=f"Event {event_kind} sent to target {target.name}",
                metadata={
                    "target": target.name,
                    "protocol": target.protocol,
                    "attempt": attempt,
                    "event_id": event_id,
                    "correlation_id": correlation_id,
                },
            )
            return
        except Exception as ex:
            last_error = ex
            if attempt < MAX_RETRY:
                wait_seconds = BASE_BACKOFF_SECONDS * (2 ** (attempt - 1))
                record_event(
                    db,
                    category="outbound",
                    event_type="outbound_retry_scheduled",
                    severity="warning",
                    message=f"Retry scheduled for {target.name} (attempt {attempt}/{MAX_RETRY})",
                    metadata={
                        "target": target.name,
                        "protocol": target.protocol,
                        "attempt": attempt,
                        "backoff_seconds": wait_seconds,
                        "error": str(ex),
                        "event_id": event_id,
                        "correlation_id": correlation_id,
                    },
                )
                time.sleep(wait_seconds)

    record_event(
        db,
        category="outbound",
        event_type="outbound_dead_letter",
        severity="error",
        message=f"Delivery to {target.name} moved to dead-letter queue",
        metadata={
            "target": target.name,
            "protocol": target.protocol,
            "max_retry": MAX_RETRY,
            "error": str(last_error) if last_error else "unknown",
            "event_id": event_id,
            "correlation_id": correlation_id,
            "payload": payload,
        },
    )


def _send_rest(target: OutboundTarget, payload: dict) -> None:
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    headers = {"Content-Type": "application/json"}
    if target.auth_header and target.auth_token:
        headers[target.auth_header] = target.auth_token
    req = urllib.request.Request(target.endpoint, data=data, headers=headers, method="POST")
    with urllib.request.urlopen(req, timeout=8):
        pass


def _send_mqtt(target: OutboundTarget, payload: dict) -> None:
    if mqtt_publish is None:
        raise RuntimeError("MQTT publish için paho-mqtt kurulu değil.")
    if not target.topic:
        raise ValueError("MQTT target için topic zorunludur.")
    mqtt_publish.single(
        target.topic,
        payload=json.dumps(payload, ensure_ascii=False),
        hostname=target.endpoint,
        qos=target.qos,
        retain=target.retain,
    )


def _dispatch_iec104(db: Session, *, target: OutboundTarget, event_kind: str, payload: dict) -> None:
    """IEC 104 server'in in-memory nokta tablosunu gunceller.

    Sadece `telemetry` kind'indaki event'ler gecerlidir (alarm'lar tag degeri
    degildir). Server FastAPI startup'ta deploy edilmis olmali; degilse sessizce
    dusturuluruz (degerli retry olmaz — bir sonraki interrogation'da SCADA zaten
    tekrar ister).
    """
    if event_kind != "telemetry":
        return
    device_code = payload.get("device_code")
    signal_key = payload.get("signal_key")
    if not device_code or not signal_key:
        return
    value = payload.get("value")
    quality = str(payload.get("quality", "good")).lower()
    good = quality in ("good", "ok", "")
    iec104_manager.update_point_threadsafe(
        device_code=str(device_code),
        signal_key=str(signal_key),
        value=value,
        good=good,
    )
    # Deliver olayini kuru kuruya loglamak yerine cok kisa bir bilgi birakiyoruz
    # ki olay akisi bos kalmasin.
    record_event(
        db,
        category="outbound",
        event_type="iec104_point_updated",
        severity="debug",
        message=f"{target.name} -> {device_code}.{signal_key}",
        metadata={
            "target": target.name,
            "protocol": "iec104",
            "device_code": device_code,
            "signal_key": signal_key,
            "quality": quality,
        },
    )
