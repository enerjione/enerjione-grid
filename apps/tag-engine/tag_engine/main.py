import json
import os
import time
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, HTTPServer
from threading import Thread
from uuid import uuid4

import pika


RABBIT_URL = os.getenv("RABBITMQ_URL", "amqp://guest:guest@localhost:5672/")
EXCHANGE = os.getenv("RABBITMQ_EXCHANGE", "hsl.events")
INCOMING_TOPIC = os.getenv("TAG_ENGINE_INCOMING_TOPIC", "telemetry.raw_received")
OUTGOING_TOPIC = os.getenv("TAG_ENGINE_OUTGOING_TOPIC", "telemetry.received")
QUEUE_NAME = os.getenv("TAG_ENGINE_QUEUE", "hsl.tag_engine.raw")
DLX_EXCHANGE = os.getenv("RABBITMQ_DLX_EXCHANGE", "hsl.events.dlx")
HEALTH_HOST = os.getenv("WORKER_HEALTH_HOST", "127.0.0.1")
HEALTH_PORT = int(os.getenv("WORKER_HEALTH_PORT", "8011"))


class _HealthHandler(BaseHTTPRequestHandler):
    def do_GET(self):  # noqa: N802
        if self.path == "/health":
            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.end_headers()
            self.wfile.write(b'{"status":"ok","service":"tag-engine"}')
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


def _normalize_quality(quality: str) -> str:
    return (quality or "good").strip().lower()


def _build_processed_payload(payload: dict) -> dict:
    quality = _normalize_quality(str(payload.get("quality", "good")))
    now_iso = datetime.now(timezone.utc).isoformat()
    return {
        "message_id": payload.get("message_id") or str(uuid4()),
        "correlation_id": payload.get("correlation_id") or payload.get("message_id") or str(uuid4()),
        "source_gateway": payload.get("source_gateway") or "unknown",
        "device_code": payload.get("device_code"),
        "signal_key": payload.get("signal_key"),
        "value": payload.get("value"),
        "quality": quality,
        # Gateway dnp3 adapter'leri "comm_lost" (TCP/link kopuk) ve "restart"
        # (cihaz reboot etti, baseline bekleniyor) kalitelerini de yayinlar.
        # Frontend'de cihazin "offline" gozukmesi icin bu kaliteler de
        # offline olarak isaretlenmelidir; aksi halde gateway'in son iyi
        # degeri tekrar yayinlanmadigi durumda cihaz hala "online" gorunur.
        "status": (
            "offline"
            if quality in {"bad", "offline", "invalid", "comm_lost", "restart"}
            else "online"
        ),
        "source_timestamp": payload.get("source_timestamp") or now_iso,
        "processed_at": now_iso,
    }


def main() -> None:
    _start_health_server()
    print("tag-engine-starting")
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
                    "x-dead-letter-routing-key": "telemetry.raw_received.dead",
                },
            )
            channel.queue_bind(exchange=EXCHANGE, queue=QUEUE_NAME, routing_key=INCOMING_TOPIC)
            channel.basic_qos(prefetch_count=20)

            def _on_message(ch, method, properties, body):  # noqa: ANN001
                _ = properties
                try:
                    payload = json.loads(body.decode("utf-8"))
                    processed = _build_processed_payload(payload)
                    if processed["source_gateway"] == "unknown":
                        print(
                            "tag-engine-warning missing source_gateway "
                            f"msg={processed['message_id']} dev={processed['device_code']}"
                        )
                    channel.basic_publish(
                        exchange=EXCHANGE,
                        routing_key=OUTGOING_TOPIC,
                        body=json.dumps(processed, ensure_ascii=False).encode("utf-8"),
                        properties=pika.BasicProperties(
                            content_type="application/json",
                            delivery_mode=2,
                            message_id=processed["message_id"],
                            correlation_id=processed["correlation_id"],
                            headers={
                                "source_gateway": processed["source_gateway"],
                                "device_code": processed.get("device_code") or "",
                                "signal_key": processed.get("signal_key") or "",
                            },
                        ),
                    )
                    print(
                        "tag-engine-forwarded "
                        f"msg={processed['message_id']} corr={processed['correlation_id']} "
                        f"gw={processed['source_gateway']} dev={processed['device_code']}"
                    )
                    ch.basic_ack(delivery_tag=method.delivery_tag)
                except Exception as ex:
                    print(f"tag-engine-failed error={ex}")
                    ch.basic_nack(delivery_tag=method.delivery_tag, requeue=False)

            channel.basic_consume(queue=QUEUE_NAME, on_message_callback=_on_message)
            print("tag-engine-running")
            channel.start_consuming()
        except Exception as ex:
            print(f"tag-engine-reconnect error={ex}")
            time.sleep(3)
        finally:
            if connection is not None:
                try:
                    connection.close()
                except Exception:
                    pass


if __name__ == "__main__":
    main()
