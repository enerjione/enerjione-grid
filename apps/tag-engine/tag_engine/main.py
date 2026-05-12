"""Tag-engine — NATS JetStream uzerinde ham telemetri akisini normalize eder.

Akis:
  hsl.telemetry.raw.<gw>          (gateway'lerin yaylinladigi ham)
    -> tag-engine consume
    -> normalize (quality, status, processed_at)
    -> publish: hsl.telemetry.normalized.<gw>
       (alarm-service ve iec104-outbound buradan tuketir)

RabbitMQ'dan tamamen kaldirildi. Telemetri akisi tamamen JetStream uzerinden
ilerler; alarm.created akisi backend tarafinda RabbitMQ'da kalir (tag-engine
onunla ilgilenmez).
"""

import asyncio
import json
import os
import signal
import threading
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, HTTPServer
from threading import Thread
from uuid import uuid4

import nats

NATS_URL = os.getenv("NATS_URL", "nats://localhost:4222")
# Stream isimleri — backend'in olusturdugu stream'lerle ayni olmali.
STREAM_NORMALIZED = os.getenv("NATS_STREAM_TELEMETRY_NORMALIZED", "TELEMETRY_NORMALIZED")
# Consumer subject pattern (incoming) — backend'in TELEMETRY_RAW stream'ine bind.
SUBJECT_RAW = os.getenv("NATS_SUBJECT_TELEMETRY_RAW", "hsl.telemetry.raw.>")
# Outgoing subject prefix — konkre subject: hsl.telemetry.normalized.<gw>
SUBJECT_NORMALIZED_PREFIX = os.getenv(
    "NATS_SUBJECT_NORMALIZED_PREFIX", "hsl.telemetry.normalized"
)
DURABLE_NAME = os.getenv("NATS_TAG_ENGINE_DURABLE", "tag-engine-normalize")
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
        "signal_data_type": payload.get("signal_data_type"),
        "value": payload.get("value"),
        # DNP3 Group 110 (Octet String) sinyallerinde gateway numeric value
        # alanini None yollar; gercek metin value_string'tedir. Backend'in
        # bu alani persist edebilmesi icin field'i drop etmeden geciriyoruz.
        "value_string": payload.get("value_string"),
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


_stop_event = threading.Event()


def _install_signal_handlers() -> None:
    def _handler(signum, _frame):  # noqa: ANN001
        print(f"tag-engine-shutdown signal={signum}")
        _stop_event.set()

    signal.signal(signal.SIGINT, _handler)
    signal.signal(signal.SIGTERM, _handler)


async def _run() -> None:
    backoff = 2
    while not _stop_event.is_set():
        nc = None
        try:
            nc = await nats.connect(
                servers=[NATS_URL],
                max_reconnect_attempts=-1,
                reconnect_time_wait=2,
                name="e1-tag-engine",
            )
            js = nc.jetstream()

            async def _on_message(msg) -> None:  # noqa: ANN001
                try:
                    payload = json.loads(msg.data.decode("utf-8"))
                    processed = _build_processed_payload(payload)
                    if processed["source_gateway"] == "unknown":
                        # Sadece anomali (eksik gateway) durumunda log; her mesajda
                        # print yapmak Docker stdout fsync ile per-message gecikme
                        # uretiyordu (saniyede 200+ mesajda toplam yavaslama).
                        print(
                            "tag-engine-warning missing source_gateway "
                            f"msg={processed['message_id']} dev={processed['device_code']}"
                        )
                    out_subject = (
                        f"{SUBJECT_NORMALIZED_PREFIX}.{processed['source_gateway']}"
                    )
                    # Nats-Msg-Id: stream-side dedup (2dk pencerede ayni id'yi tek alir).
                    headers = {
                        "Nats-Msg-Id": processed["message_id"],
                        "X-Correlation-Id": str(processed.get("correlation_id") or ""),
                        "source_gateway": str(processed["source_gateway"]),
                        "device_code": str(processed.get("device_code") or ""),
                        "signal_key": str(processed.get("signal_key") or ""),
                    }
                    await js.publish(
                        out_subject,
                        json.dumps(processed, ensure_ascii=False).encode("utf-8"),
                        headers=headers,
                    )
                    await msg.ack()
                except Exception as ex:  # noqa: BLE001
                    print(f"tag-engine-failed error={ex}")
                    try:
                        await msg.nak()
                    except Exception:  # noqa: BLE001
                        pass

            sub = await js.subscribe(
                subject=SUBJECT_RAW,
                durable=DURABLE_NAME,
                cb=_on_message,
                manual_ack=True,
            )
            print(
                f"tag-engine-running url={NATS_URL} in={SUBJECT_RAW} "
                f"out={SUBJECT_NORMALIZED_PREFIX}.<gw> durable={DURABLE_NAME}"
            )
            backoff = 2  # connect basarili
            while not _stop_event.is_set():
                await asyncio.sleep(1)
            await sub.unsubscribe()
        except Exception as ex:  # noqa: BLE001
            if _stop_event.is_set():
                break
            print(f"tag-engine-reconnect error={ex} backoff={backoff}s url={NATS_URL}")
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, 60)
        finally:
            if nc is not None:
                try:
                    await nc.drain()
                except Exception:  # noqa: BLE001
                    pass


def main() -> None:
    _start_health_server()
    _install_signal_handlers()
    print("tag-engine-starting")
    asyncio.run(_run())
    print("tag-engine-stopped")


if __name__ == "__main__":
    main()
