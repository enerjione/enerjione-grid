"""Backend-api telemetri tuketicisi — NATS JetStream uzerinden.

Gateway'ler artik telemetriyi `hsl.telemetry.raw.<gateway_code>` subject'ine
JetStream'e basar. Bu modul TELEMETRY_RAW stream'inden okuyup her sinyali
DB'ye yazar; cihazin communication_status ve last_update_at alanlarini
guncel tutar. Bu islem yapilmadigi surece "Canli Degerler" ekrani bos kalir
ve cihazlar "Kesik / bekleniyor" gorunur.

Tag-engine cikis topic'i ('telemetry.normalized.*') yerine ham topic'i
dinliyoruz: tag-engine ayakta olmasa bile persist akisi calismaya devam eder
ve frontend cihaz durumunu kaybetmez. Quality normalizasyonu zaten
`process_telemetry_reading` icinde yapiliyor.

Asyncio loop ayri bir thread'de calisir; NATS reconnect ve durable consumer
sayesinde process restart'inda kaldigi yerden devam eder.
"""

from __future__ import annotations

import asyncio
import json
import logging
import threading
import time
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.core.config import settings
from app.db.session import SessionLocal
from app.models.device import Device
from app.models.processed_message import ProcessedMessage
from app.schemas.telemetry import TelemetryIn
from app.services.tag_engine_service import process_telemetry_reading
from app.services.ws_broadcaster import broadcaster as ws_broadcaster

logger = logging.getLogger(__name__)

CONSUMER_NAME = "backend-api.telemetry-persister"
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

        try:
            telemetry, _event = process_telemetry_reading(device, reading, db=db)
        except Exception as exc:  # noqa: BLE001
            # process_telemetry_reading icindeki yardimci sorgulardan biri
            # (örn. _auto_clear_quality_alarms veya batarya hesabi) hata
            # firlatirsa telemetri persist'i blocklanmasin — sadece minimal
            # device alanlarini guncelleyip telemetri kaydini at.
            logger.warning("telemetry-consumer-process-error msg=%s error=%s", message_id, exc)
            db.rollback()
            telemetry = None  # type: ignore[assignment]

        if telemetry is None:
            # Fallback: en azindan ham telemetri kaydini ve communication_status'u guncelle.
            from app.models.telemetry import Telemetry
            from app.services.tag_engine_service import map_quality_to_status, normalize_quality

            normalized_quality = normalize_quality(reading.quality)
            device.communication_status = map_quality_to_status(normalized_quality)
            if device.communication_status.value == "online":
                device.last_update_at = datetime.now(timezone.utc)
            telemetry = Telemetry(
                device_id=device.id,
                signal_key=reading.signal_key,
                value=reading.value,
                value_string=reading.value_string,
                quality=normalized_quality,
                source_timestamp=reading.source_timestamp,
            )

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
    """JetStream'den `hsl.telemetry.raw.>` subject'ini dinler.

    Durable consumer ile process restart'inda kaldigi yerden devam eder.
    NATS gelmediyse veya bagklanti koparsa kendi icinde exponential backoff
    ile yeniden baglanir.
    """
    try:
        import nats as _nats  # type: ignore[import-not-found]
    except ImportError:
        logger.error(
            "telemetry_consumer_jetstream_disabled reason=nats_py_missing "
            "(pip install nats-py>=2.6). Telemetri DB'ye YAZILMIYOR!"
        )
        return

    async def _run() -> None:
        backoff = 2
        while not _stop_event.is_set():
            nc = None
            try:
                nc = await _nats.connect(
                    servers=[settings.nats_url],
                    connect_timeout=settings.nats_connect_timeout_sec,
                    max_reconnect_attempts=-1,
                    reconnect_time_wait=2,
                    name="hsl-backend-api-consumer",
                )
                js = nc.jetstream()

                async def _handle(msg) -> None:  # noqa: ANN001
                    try:
                        payload = json.loads(msg.data.decode("utf-8"))
                        # WS broadcast ONCE: frontend cihaz degerini anlik gorur.
                        # DB persist sonrasinda yapilirsa, DB lock/yavaslama frontend'i
                        # de yavaslatir. WS sadece in-memory queue'ya put_nowait
                        # (~mikro saniyeler), persist baska is parcaciginda gibi
                        # davranir.
                        try:
                            ws_broadcaster.broadcast(payload)
                        except Exception:  # noqa: BLE001 — WS hatasi consume akisini bozmasin
                            logger.debug("ws_broadcast_failed", exc_info=True)
                        _persist_message(payload)
                        await msg.ack()
                    except Exception as exc:  # noqa: BLE001
                        logger.warning(
                            "telemetry-consumer-failed error=%s subject=%s",
                            exc,
                            getattr(msg, "subject", "?"),
                        )
                        # nak: JetStream redeliver edecek (backoff'la). Poison
                        # mesajlar max_deliver'a takilinca dead-letter'a duser.
                        try:
                            await msg.nak()
                        except Exception:  # noqa: BLE001
                            logger.debug("js_nak_failed", exc_info=True)

                # Durable push consumer — subject pattern'i dinliyoruz.
                # Backend startup'ta jetstream_bus.start_bus_if_enabled() stream'leri
                # ensure ediyor olmali; consumer subscribe ederken stream var olmali.
                sub = await js.subscribe(
                    subject=settings.nats_subject_telemetry_raw,
                    durable=settings.nats_consumer_telemetry_persist,
                    cb=_handle,
                    manual_ack=True,
                )
                logger.info(
                    "telemetry_consumer_running subject=%s durable=%s url=%s",
                    settings.nats_subject_telemetry_raw,
                    settings.nats_consumer_telemetry_persist,
                    settings.nats_url,
                )
                backoff = 2  # connect basarili — backoff sifirla
                while not _stop_event.is_set():
                    await asyncio.sleep(1)
                await sub.unsubscribe()
            except Exception as exc:  # noqa: BLE001
                if _stop_event.is_set():
                    break
                logger.warning(
                    "telemetry_consumer_reconnect error=%s backoff=%ds url=%s",
                    exc,
                    backoff,
                    settings.nats_url,
                )
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, 60)
            finally:
                if nc is not None:
                    try:
                        await nc.drain()
                    except Exception:  # noqa: BLE001
                        logger.debug("js_drain_error", exc_info=True)

    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        loop.run_until_complete(_run())
    finally:
        loop.close()


def start() -> None:
    global _thread
    if _thread is not None and _thread.is_alive():
        return
    _stop_event.clear()
    _thread = threading.Thread(
        target=_consume_loop, name="telemetry-consumer-jetstream", daemon=True
    )
    _thread.start()


def stop() -> None:
    _stop_event.set()
