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

        # Outbound dispatch — iki ayri yol:
        #   1) IEC 104 hedefleri: dispatch_event ile ANLIK point registry guncellemesi
        #      (SCADA master surekli yeni degeri gormeli, batch kabul edilmez).
        #   2) REST / MQTT hedefleri: outbound_telemetry_batcher'a submit edilir;
        #      batcher 5sn pencerede DEGISIK readings'i biriktirir + tek POST
        #      olarak gonderir. Ayni device+signal icin ayni value tekrar gelirse
        #      dedup ile atlanir.
        #
        # Alarm event'leri (separate akis: /internal/alarms) batch'e GIRMEZ —
        # anlik gonderilir.
        try:
            # signal_source: signal_key prefix'inden turet (master.voltage_a -> master)
            sig_source = None
            if reading.signal_key and "." in reading.signal_key:
                sig_source = reading.signal_key.split(".", 1)[0].lower()
            outbound_payload = {
                "message_id": message_id,
                "correlation_id": reading.correlation_id or message_id,
                "event_kind": "telemetry",
                "device_code": reading.device_code,
                "signal_key": reading.signal_key,
                "signal_source": sig_source,
                "source_gateway": reading.source_gateway,
                "value": reading.value,
                "value_string": reading.value_string,
                "quality": reading.quality,
                "status": device.communication_status.value if hasattr(device.communication_status, "value") else str(device.communication_status),
                "source_timestamp": reading.source_timestamp.isoformat() if reading.source_timestamp else None,
                "processed_at": datetime.now(timezone.utc).isoformat(),
            }
            # 1) IEC 104 anlik — sadece IEC protocol'lu aktif target'lara push.
            from app.services.outbound_dispatch_service import dispatch_event
            dispatch_event(db, event_kind="telemetry", payload=outbound_payload)
            # NOT: dispatch_event REST/MQTT/IEC tum protocol'lere tek tek POST
            # atar. Ama biz REST/MQTT'i batch'e tasiyoruz; iki kez gondermesin
            # diye outbound_dispatch_service simdi sadece IEC104'u isleyecek
            # (telemetry icin REST/MQTT skipped). Detay: outbound_dispatch_service.py
            # _dispatch_with_retry icine `event_kind == 'telemetry' and protocol
            # in ('rest', 'mqtt')` filter eklendi.
            # 2) REST / MQTT batch — ayni payload, batcher dedup + buffer + flush.
            from app.services.outbound_telemetry_batcher import submit as batch_submit
            batch_submit(outbound_payload)
        except Exception:  # noqa: BLE001
            # Outbound hatasi telemetri akisini bozmasin — sadece log.
            logger.exception("outbound_dispatch_failed_telemetry msg=%s", message_id)
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
                    name="e1-backend-api-consumer",
                )
                js = nc.jetstream()

                async def _handle(msg) -> None:  # noqa: ANN001
                    try:
                        payload = json.loads(msg.data.decode("utf-8"))
                        # SIRA ONEMLI: persist ONCE, sonra WS broadcast.
                        # Eski sira (WS once) DB hatasi durumunda nak() ->
                        # JetStream redeliver -> WS ayni degeri ikinci kez
                        # yayinlar (duplicate frontend goruntusu). Persist
                        # succeed olmadan WS yapilmaz; idempotent davranis.
                        _persist_message(payload)
                        try:
                            ws_broadcaster.broadcast(payload)
                        except Exception:  # noqa: BLE001 — WS hatasi consume akisini bozmasin
                            logger.debug("ws_broadcast_failed", exc_info=True)
                        await msg.ack()
                    except Exception as exc:  # noqa: BLE001
                        # Mesajin kacinci delivery oldugunu kontrol et — son
                        # denemede DLQ'ya tasi, aksi halde nak ile redeliver.
                        # NATS max_deliver'a takilinca mesaji sessizce discard
                        # eder; DLQ ile operator gorebilir, root-cause sonra
                        # replay edebilir.
                        num_delivered = int(
                            getattr(getattr(msg, "metadata", None), "num_delivered", 1) or 1
                        )
                        is_terminal = num_delivered >= settings.nats_worker_max_deliver
                        logger.warning(
                            "telemetry-consumer-failed error=%s subject=%s "
                            "delivery=%d/%d terminal=%s",
                            exc,
                            getattr(msg, "subject", "?"),
                            num_delivered,
                            settings.nats_worker_max_deliver,
                            is_terminal,
                        )
                        if is_terminal:
                            # DLQ'ya manuel publish — orijinal mesaj + hata
                            # metadata'si. Sonra ack ki JetStream redeliver
                            # etmesin (max_deliver bekleme).
                            try:
                                orig_subject = getattr(msg, "subject", "unknown")
                                dlq_subject = f"e1.dlq.backend-api.{orig_subject}"
                                dlq_headers = {
                                    "X-DLQ-Reason": "max_deliver_exceeded",
                                    "X-DLQ-Service": "backend-api",
                                    "X-DLQ-Original-Subject": orig_subject,
                                    "X-DLQ-Error": str(exc)[:500],
                                    "X-DLQ-Delivery-Count": str(num_delivered),
                                }
                                await js.publish(
                                    dlq_subject, msg.data, headers=dlq_headers
                                )
                                logger.error(
                                    "telemetry-consumer-dlq subject=%s error=%s",
                                    dlq_subject,
                                    exc,
                                )
                                await msg.ack()
                            except Exception:  # noqa: BLE001
                                logger.exception("dlq_publish_failed")
                                try:
                                    await msg.nak()
                                except Exception:  # noqa: BLE001
                                    logger.debug("js_nak_failed", exc_info=True)
                        else:
                            try:
                                await msg.nak()
                            except Exception:  # noqa: BLE001
                                logger.debug("js_nak_failed", exc_info=True)

                # Durable push consumer — subject pattern'i dinliyoruz.
                # Backend startup'ta jetstream_bus.start_bus_if_enabled() stream'leri
                # ensure ediyor olmali; consumer subscribe ederken stream var olmali.
                #
                # Consumer parametreleri (nats-py default'lari uretim icin yetersiz):
                #   * max_ack_pending=10000: 600 cihaz x 10 msg/s = ~6000 inflight;
                #     default 1000 yetersiz, ack'lemeyi yetistiremeyince broker
                #     subscriber'i suspend eder.
                #   * max_deliver=10: poison message sonsuza dek redeliver edilmez;
                #     10 deneme sonrasi NACK -> DLQ benzeri davranis (max_deliver
                #     asimi mesaji discard eder; production'da DLQ stream'i ileride
                #     eklenebilir).
                #   * deliver_policy=NEW: durable consumer ilk olusurken sadece
                #     yeni mesajlardan baslar. Aksi halde 7 gunluk history'yi
                #     baslangicta replay eder ve persister gec kalir.
                #   * ack_wait=60s: persist + WS broadcast tipik <100ms; 60sn cap
                #     network gecikmesi icin defansif.
                from nats.js.api import ConsumerConfig, DeliverPolicy
                consumer_cfg = ConsumerConfig(
                    durable_name=settings.nats_consumer_telemetry_persist,
                    deliver_policy=DeliverPolicy.NEW,
                    ack_wait=60,
                    max_ack_pending=10000,
                    max_deliver=10,
                )
                sub = await js.subscribe(
                    subject=settings.nats_subject_telemetry_raw,
                    durable=settings.nats_consumer_telemetry_persist,
                    cb=_handle,
                    manual_ack=True,
                    config=consumer_cfg,
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
