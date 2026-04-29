"""Backend-api icinde calisan RabbitMQ telemetri tuketicisi.

Gateway'den `hsl.events` exchange'ine `telemetry.raw_received` routing key'i ile
yayinlanan her sinyali DB'ye yazar; cihazin communication_status ve
last_update_at alanlarini guncel tutar. Bu islem yapilmadigi surece
"Canli Degerler" ekrani bos kalir ve cihazlar "Kesik / bekleniyor" gorunur.

Tag-engine'in cikis topic'i ('telemetry.received') yerine ham topic'i
dinliyoruz: tag-engine ayakta olmasa bile persist akisi calismaya devam eder
ve frontend cihaz durumunu kaybetmez. Quality normalizasyonu zaten
`process_telemetry_reading` icinde yapiliyor.

Tek thread halinde, daemon olarak FastAPI startup'inda baslatilir.
"""

from __future__ import annotations

import json
import logging
import threading
import time
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

import pika
from pika.exceptions import AMQPError
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.core.config import settings
from app.db.session import SessionLocal
from app.models.device import Device
from app.models.processed_message import ProcessedMessage
from app.schemas.telemetry import TelemetryIn
from app.services.tag_engine_service import process_telemetry_reading

logger = logging.getLogger(__name__)

CONSUMER_NAME = "backend-api.telemetry-persister"
INCOMING_TOPIC = "telemetry.raw_received"
QUEUE_NAME = "hsl.backend.telemetry_persist"
DLX_ROUTING_KEY = "telemetry.raw_received.persist.dead"
PREFETCH_COUNT = 20
RECONNECT_DELAY_SEC = 3

_stop_event = threading.Event()
_thread: threading.Thread | None = None


def _persist_message(payload: dict[str, Any]) -> None:
    message_id = str(payload.get("message_id") or "")
    if not message_id:
        message_id = str(uuid4())
        payload["message_id"] = message_id

    db = SessionLocal()
    try:
        already = db.scalar(
            select(ProcessedMessage).where(
                ProcessedMessage.consumer_name == CONSUMER_NAME,
                ProcessedMessage.message_id == message_id,
            )
        )
        if already is not None:
            return

        try:
            reading = TelemetryIn(**payload)
        except ValidationError as exc:
            logger.warning(
                "telemetry-consumer-invalid-payload msg=%s error=%s", message_id, exc
            )
            raise

        device = db.scalar(select(Device).where(Device.code == reading.device_code))
        if device is None:
            logger.warning(
                "telemetry-consumer-device-not-found msg=%s device_code=%s",
                message_id,
                reading.device_code,
            )
            db.add(
                ProcessedMessage(
                    consumer_name=CONSUMER_NAME,
                    message_id=message_id,
                    processed_at=datetime.now(timezone.utc),
                )
            )
            try:
                db.commit()
            except IntegrityError:
                db.rollback()
            return

        telemetry, _event = process_telemetry_reading(device, reading)
        db.add(telemetry)
        db.add(
            ProcessedMessage(
                consumer_name=CONSUMER_NAME,
                message_id=message_id,
                processed_at=datetime.now(timezone.utc),
            )
        )
        try:
            db.commit()
        except IntegrityError:
            # Paralel consumer veya retry'ta ayni mesaj iki kez gelirse
            # unique index hatasini bastiriyoruz — istenen davranis budur.
            db.rollback()
    finally:
        db.close()


def _consume_loop() -> None:
    while not _stop_event.is_set():
        connection: pika.BlockingConnection | None = None
        try:
            connection = pika.BlockingConnection(pika.URLParameters(settings.rabbitmq_url))
            channel = connection.channel()
            channel.exchange_declare(
                exchange=settings.rabbitmq_exchange, exchange_type="topic", durable=True
            )
            channel.exchange_declare(
                exchange=settings.rabbitmq_dlx_exchange, exchange_type="topic", durable=True
            )
            channel.queue_declare(
                queue=QUEUE_NAME,
                durable=True,
                arguments={
                    "x-dead-letter-exchange": settings.rabbitmq_dlx_exchange,
                    "x-dead-letter-routing-key": DLX_ROUTING_KEY,
                },
            )
            channel.queue_bind(
                exchange=settings.rabbitmq_exchange,
                queue=QUEUE_NAME,
                routing_key=INCOMING_TOPIC,
            )
            channel.basic_qos(prefetch_count=PREFETCH_COUNT)

            def _on_message(ch, method, properties, body):  # noqa: ANN001
                _ = properties
                try:
                    payload = json.loads(body.decode("utf-8"))
                    _persist_message(payload)
                    ch.basic_ack(delivery_tag=method.delivery_tag)
                except Exception as exc:  # noqa: BLE001
                    logger.warning("telemetry-consumer-failed error=%s", exc)
                    ch.basic_nack(delivery_tag=method.delivery_tag, requeue=False)

            channel.basic_consume(queue=QUEUE_NAME, on_message_callback=_on_message)
            logger.info("telemetry-consumer-running queue=%s", QUEUE_NAME)
            channel.start_consuming()
        except (AMQPError, OSError) as exc:
            if not _stop_event.is_set():
                logger.warning("telemetry-consumer-reconnect error=%s", exc)
                time.sleep(RECONNECT_DELAY_SEC)
        except Exception as exc:  # noqa: BLE001
            logger.exception("telemetry-consumer-crashed error=%s", exc)
            time.sleep(RECONNECT_DELAY_SEC)
        finally:
            if connection is not None and not connection.is_closed:
                try:
                    connection.close()
                except Exception:  # noqa: BLE001
                    pass


def start() -> None:
    global _thread
    if _thread is not None and _thread.is_alive():
        return
    _stop_event.clear()
    _thread = threading.Thread(
        target=_consume_loop, name="telemetry-consumer", daemon=True
    )
    _thread.start()


def stop() -> None:
    _stop_event.set()
