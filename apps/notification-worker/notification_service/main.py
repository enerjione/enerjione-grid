"""Notification worker — alarm.created event'lerini consume edip backend'in
dispatch endpoint'ini tetikler.

Mimari:
    alarm-service ----RabbitMQ----> hsl.events / alarm.created
                                          |
                                          v
                                 notification-worker (BU SERVIS)
                                          |
                                          | POST /internal/notifications/dispatch/{alarm_id}
                                          v
                                  backend-api dispatch_alarm_notifications
                                  (SMTP / Telegram / SMS / FCM)

Onceki "sadece print" davranisindan farkli:
  * Mesaji parse eder, `alarm_id` cikarir
  * Backend'in dispatch endpoint'ini X-Service-Token ile cagirir
  * Basarisizlikta exponential backoff + retry (3 deneme)
  * Tum denemeler basarisizsa DLX'e gonderir (forensic)
  * Backend down ise mesaj `requeue=True` ile geri konur (broker retry)
"""

from __future__ import annotations

import json
import logging
import os
import sys
import time
from http.server import BaseHTTPRequestHandler, HTTPServer
from threading import Thread
from typing import Any

import pika
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

# `.env` zorunlu — guest:guest default'u prod'da yanlislikla bag kurmasin.
RABBIT_URL = os.getenv("RABBITMQ_URL", "")
EXCHANGE = os.getenv("RABBITMQ_EXCHANGE", "e1.events")
INCOMING_TOPIC = os.getenv("NOTIFICATION_INCOMING_TOPIC", "alarm.created")
QUEUE_NAME = os.getenv("NOTIFICATION_QUEUE", "e1.notification_service.alarm")
DLX_EXCHANGE = os.getenv("RABBITMQ_DLX_EXCHANGE", "e1.events.dlx")
HEALTH_HOST = os.getenv("WORKER_HEALTH_HOST", "127.0.0.1")
HEALTH_PORT = int(os.getenv("WORKER_HEALTH_PORT", "8013"))
BACKEND_API_BASE = os.getenv("BACKEND_API_BASE", "http://127.0.0.1:8000/api/v1")
INTERNAL_SERVICE_TOKEN = os.getenv("INTERNAL_SERVICE_TOKEN", "change-me-internal-token")
# Backend down olursa mesaj requeue edilir; agressif retry interval (sn).
HTTP_TIMEOUT_SEC = float(os.getenv("NOTIFICATION_HTTP_TIMEOUT_SEC", "10"))
PREFETCH = int(os.getenv("NOTIFICATION_PREFETCH", "10"))
RECONNECT_BASE_SEC = float(os.getenv("NOTIFICATION_RECONNECT_BASE_SEC", "3"))
RECONNECT_MAX_SEC = float(os.getenv("NOTIFICATION_RECONNECT_MAX_SEC", "30"))

logger = logging.getLogger("notification-worker")

# Worker durumu — health endpoint icin
_state: dict[str, Any] = {
    "started_at": time.time(),
    "messages_received": 0,
    "messages_dispatched": 0,
    "messages_failed": 0,
    "last_error": None,
}


def _build_http_session() -> requests.Session:
    """Backend'e HTTP dispatch icin tek bir session — connection pool reuse +
    `requests.Session.adapters` ile transient (5xx, ConnectionError) retry.
    Mesaj-spesifik 4xx hatalari retry edilmez (poison message)."""
    s = requests.Session()
    retry = Retry(
        total=3,
        backoff_factor=0.5,  # 0.5, 1.0, 2.0 saniye
        status_forcelist=(500, 502, 503, 504),
        allowed_methods=frozenset(["POST"]),
        respect_retry_after_header=True,
    )
    adapter = HTTPAdapter(max_retries=retry, pool_connections=5, pool_maxsize=10)
    s.mount("http://", adapter)
    s.mount("https://", adapter)
    s.headers.update({ "X-Service-Token": INTERNAL_SERVICE_TOKEN, "X-Service-Name": "notification-worker" })
    return s


class _HealthHandler(BaseHTTPRequestHandler):
    def do_GET(self):  # noqa: N802
        if self.path == "/health":
            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.end_headers()
            body = {
                "status": "ok",
                "service": "notification-worker",
                **_state,
            }
            self.wfile.write(json.dumps(body).encode("utf-8"))
            return
        self.send_response(404)
        self.end_headers()

    def log_message(self, format, *args):  # noqa: A003
        _ = format, args
        return


def _start_health_server() -> None:
    server = HTTPServer((HEALTH_HOST, HEALTH_PORT), _HealthHandler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()


def _dispatch_alarm(http: requests.Session, alarm_id: int) -> tuple[bool, str | None]:
    """Backend'in dispatch endpoint'ini cagirir.

    Returns (success, error_message). Hata kategorisi:
      * 4xx (alarm yok, validation, auth) -> NON-RETRYABLE; mesaj DLX'e gider
      * 5xx / network -> RETRYABLE; pika nack(requeue=True) ile broker geri koyar
      * 2xx -> success, ack
    """
    url = f"{BACKEND_API_BASE.rstrip('/')}/internal/notifications/dispatch/{alarm_id}"
    try:
        resp = http.post(url, timeout=HTTP_TIMEOUT_SEC)
    except requests.RequestException as ex:
        return False, f"network_error: {ex}"
    if 200 <= resp.status_code < 300:
        return True, None
    return False, f"http_{resp.status_code}: {resp.text[:200]}"


def _is_retryable(error_msg: str | None) -> bool:
    """4xx hatasi NON-RETRYABLE (poison); 5xx + network RETRYABLE."""
    if not error_msg:
        return False
    if error_msg.startswith("network_error"):
        return True
    if error_msg.startswith("http_5"):
        return True
    return False


def _validate_required_secrets() -> None:
    """Worker baslamadan placeholder/bos secret tespit edip fail-fast yap.

    Operator env unutursa worker default 'change-me-internal-token' ile devam
    eder, backend 401 doner, retry loop sessizce dosya boy log uretir.
    """
    _PLACEHOLDER_PREFIXES = ("change-me", "please-change-me", "change-this", "your-secret")

    def _is_placeholder(value: str) -> bool:
        v = (value or "").strip().lower()
        return (not v) or v.startswith(_PLACEHOLDER_PREFIXES)

    errors: list[str] = []
    if _is_placeholder(INTERNAL_SERVICE_TOKEN):
        errors.append(
            "INTERNAL_SERVICE_TOKEN .env'de set edilmemis veya placeholder; "
            "backend 401 doner ve worker calismaz."
        )
    if not RABBIT_URL.strip():
        errors.append("RABBITMQ_URL .env'de set edilmemis; mesaj kuyruguna baglanilamaz.")
    if errors:
        msg = "\n  - ".join(errors)
        print(f"notification-worker ZORUNLU KONFIGURASYON EKSIK:\n  - {msg}", flush=True)
        raise SystemExit(2)


def main() -> None:
    logging.basicConfig(
        level=os.getenv("LOG_LEVEL", "INFO").upper(),
        format="%(asctime)s %(levelname)-5s %(name)s %(message)s",
    )
    _validate_required_secrets()
    _start_health_server()
    logger.info(
        "notification-worker-starting backend=%s prefetch=%d",
        BACKEND_API_BASE,
        PREFETCH,
    )
    http = _build_http_session()
    backoff = RECONNECT_BASE_SEC

    while True:
        connection = None
        try:
            connection = pika.BlockingConnection(pika.URLParameters(RABBIT_URL))
            channel = connection.channel()
            channel.exchange_declare(exchange=EXCHANGE, exchange_type="topic", durable=True)
            channel.exchange_declare(exchange=DLX_EXCHANGE, exchange_type="topic", durable=True)
            channel.queue_declare(
                queue=QUEUE_NAME,
                durable=True,
                arguments={
                    "x-dead-letter-exchange": DLX_EXCHANGE,
                    "x-dead-letter-routing-key": "alarm.created.dead",
                },
            )
            channel.queue_bind(exchange=EXCHANGE, queue=QUEUE_NAME, routing_key=INCOMING_TOPIC)
            channel.basic_qos(prefetch_count=PREFETCH)

            def _on_message(ch, method, properties, body):  # noqa: ANN001
                _ = properties
                _state["messages_received"] += 1
                try:
                    payload = json.loads(body.decode("utf-8"))
                except Exception as ex:
                    logger.warning("notification_payload_parse_failed error=%s", ex)
                    _state["messages_failed"] += 1
                    _state["last_error"] = f"parse_failed: {ex}"
                    # Bozuk payload — DLX'e gonder, sonsuza dek retry edip
                    # ana kuyrugu kilitleme.
                    ch.basic_nack(delivery_tag=method.delivery_tag, requeue=False)
                    return

                alarm_id = payload.get("alarm_id") or payload.get("id")
                if not isinstance(alarm_id, int):
                    logger.warning(
                        "notification_payload_missing_alarm_id keys=%s",
                        list(payload.keys()),
                    )
                    _state["messages_failed"] += 1
                    ch.basic_nack(delivery_tag=method.delivery_tag, requeue=False)
                    return

                ok, err = _dispatch_alarm(http, alarm_id)
                if ok:
                    _state["messages_dispatched"] += 1
                    logger.info(
                        "notification_dispatched alarm_id=%s device=%s",
                        alarm_id,
                        payload.get("device_code"),
                    )
                    ch.basic_ack(delivery_tag=method.delivery_tag)
                    return

                _state["messages_failed"] += 1
                _state["last_error"] = err
                if _is_retryable(err):
                    # Backend transient hatasi — broker mesaji geri koysun,
                    # bir sonraki consume'da yeniden dene.
                    logger.warning(
                        "notification_dispatch_retryable alarm_id=%s error=%s",
                        alarm_id,
                        err,
                    )
                    ch.basic_nack(delivery_tag=method.delivery_tag, requeue=True)
                else:
                    # 4xx — poison message; DLX'e gonder, ana kuyrugu temizle.
                    logger.error(
                        "notification_dispatch_poison alarm_id=%s error=%s — DLX'e gidiyor",
                        alarm_id,
                        err,
                    )
                    ch.basic_nack(delivery_tag=method.delivery_tag, requeue=False)

            channel.basic_consume(queue=QUEUE_NAME, on_message_callback=_on_message)
            logger.info("notification-worker-running queue=%s topic=%s", QUEUE_NAME, INCOMING_TOPIC)
            # Saglikli reconnect — basarili connect ile backoff sifirla
            backoff = RECONNECT_BASE_SEC
            channel.start_consuming()
        except KeyboardInterrupt:
            logger.info("notification-worker-stopping (keyboard interrupt)")
            break
        except Exception as ex:
            _state["last_error"] = f"connection_error: {ex}"
            logger.warning(
                "notification-worker-reconnect error=%s wait=%.1fs",
                ex,
                backoff,
            )
            time.sleep(backoff)
            backoff = min(RECONNECT_MAX_SEC, backoff * 1.5)
        finally:
            if connection is not None:
                try:
                    connection.close()
                except Exception:
                    pass


if __name__ == "__main__":
    main()
