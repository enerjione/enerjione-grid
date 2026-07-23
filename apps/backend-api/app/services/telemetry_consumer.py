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
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError, SQLAlchemyError

from app.core.config import settings
from app.db.session import SessionLocal
from app.models.device import Device
from app.models.processed_message import ProcessedMessage
from app.schemas.telemetry import TelemetryIn
from app.services.ws_broadcaster import broadcaster as ws_broadcaster

logger = logging.getLogger(__name__)

CONSUMER_NAME = "backend-api.telemetry-persister"

_stop_event = threading.Event()
_thread: threading.Thread | None = None


def _persist_batch(msgs: list) -> tuple[list, list, list, list]:  # noqa: ANN001
    """Bir fetch batch'ini TEK session + TEK commit ile isler.

    Onceki tasarim her mesaji ayri SessionLocal + ayri commit ile isliyordu;
    200 cihaz yukunde DB round-trip gelis hizindan yavas kalip 1.4M mesajlik
    backlog + ack_pending tikanmasi (consumer DONMASI) yaratti. Burada 256-500
    mesaj tek transaction'da yazilir -> DB round-trip ~batch kadar azalir,
    throughput gelis hizini gecer, backlog erir.

    Idempotency: batch'teki message_id'ler TEK `IN` sorgusuyla kontrol edilir
    (256 ayri SELECT yerine). Device lookup batch-ici cache ile tekrar sorgu
    yapmaz. Historian ayni commit'e dahil (ayri session/commit kaldirildi).

    Donus: (ok_msgs, bad_msgs, ok_payloads, outbound_payloads)
      - ok_msgs: commit BASARILI olduktan sonra ack edilecek NATS mesajlari
        (yeni islenmis + zaten islenmis skip'ler).
      - bad_msgs: parse/validation hatasi olan mesajlar -> caller DLQ/nak yapar.
      - ok_payloads: commit sonrasi WS broadcast icin ham payload'lar.
      - outbound_payloads: commit sonrasi IEC104/REST/MQTT dispatch payload'lari.

    Kismi hata: bir mesajin process'i patlarsa TUM batch nak EDILMEZ; o mesaj
    bad_msgs'e ayrilir, kalan batch commit'e girer. Tek poison mesaj 255
    saglikli mesaji redeliver dongusune sokmaz.
    """
    from app.models.telemetry import Telemetry
    from app.models.telemetry_history import TelemetryHistory
    from app.services.tag_engine_service import (
        map_quality_to_status,
        normalize_quality,
        process_telemetry_reading,
    )
    from sqlalchemy.dialects.postgresql import insert as _pg_insert

    ok_msgs: list = []
    bad_msgs: list = []
    ok_payloads: list = []
    outbound_payloads: list = []  # commit sonrasi IEC104/REST/MQTT dispatch

    # 1) Parse + message_id'leri topla. Parse hatasi -> bad_msgs (DLQ/nak).
    parsed: list[tuple[Any, dict, str]] = []  # (msg, payload, message_id)
    for msg in msgs:
        try:
            payload = json.loads(msg.data.decode("utf-8"))
        except Exception:  # noqa: BLE001
            bad_msgs.append(msg)
            continue
        message_id = str(payload.get("message_id") or "")
        if not message_id:
            message_id = str(uuid4())
            payload["message_id"] = message_id
        parsed.append((msg, payload, message_id))

    if not parsed:
        return ok_msgs, bad_msgs, ok_payloads, outbound_payloads

    db = SessionLocal()
    try:
        # 2) Idempotency + device lookup: batch basina IKISER sorgu. Onceki
        # davranis mesaj basina device SELECT yapiyordu (500 mesaj -> yuzlerce
        # DB round-trip). Tum code'lari tek IN sorgusuyla cache'le.
        ids = [mid for (_m, _p, mid) in parsed]
        seen: set[str] = set(
            db.scalars(
                select(ProcessedMessage.message_id).where(
                    ProcessedMessage.consumer_name == CONSUMER_NAME,
                    ProcessedMessage.message_id.in_(ids),
                )
            ).all()
        )
        device_codes = {
            str(payload.get("device_code") or "") for (_m, payload, _mid) in parsed
        }
        devices = db.scalars(
            select(Device).where(Device.code.in_(device_codes))
        ).all()
        device_cache: dict[str, Device] = {device.code: device for device in devices}
        historian_rows: list[dict[str, Any]] = []

        for msg, payload, message_id in parsed:
            if message_id in seen:
                ok_msgs.append(msg)  # zaten islenmis -> sadece ack
                continue
            try:
                reading = TelemetryIn(**payload)
            except ValidationError as exc:
                logger.warning(
                    "telemetry-consumer-invalid-payload msg=%s error=%s", message_id, exc
                )
                bad_msgs.append(msg)
                continue

            dcode = reading.device_code
            device = device_cache.get(dcode)

            if device is None:
                logger.warning(
                    "telemetry-consumer-device-not-found msg=%s device_code=%s",
                    message_id, dcode,
                )
                # Bilinmeyen cihaz: idempotency isaretle, mesaji ack'le (sonsuz
                # redeliver olmasin). Cihaz sonradan eklenirse yeni mesajlar gelir.
                db.add(ProcessedMessage(
                    consumer_name=CONSUMER_NAME,
                    message_id=message_id,
                    processed_at=datetime.now(timezone.utc),
                ))
                seen.add(message_id)
                ok_msgs.append(msg)
                continue

            # process_telemetry_reading: Telemetry obj + device mutasyonu (ayni
            # db, commit yok). Patlarsa TUM batch'i rollback ETME -> o mesaji
            # bad_msgs'e ayir, minimal fallback ile devam.
            telemetry = None
            try:
                telemetry, _event = process_telemetry_reading(device, reading, db=db)
            except SQLAlchemyError:
                # DB/transaction hatasinda fallback ile devam edilmez: session
                # aborted olabilir. Batch exception ile cikar, hicbir msg ack
                # edilmez; JetStream redeliver eder (veri kaybi yok).
                raise
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "telemetry-consumer-process-error msg=%s error=%s", message_id, exc
                )
                # Saf is-mantigi hatasinda minimal telemetry + status fallback.
                nq = normalize_quality(reading.quality)
                device.communication_status = map_quality_to_status(nq)
                if device.communication_status.value == "online":
                    device.last_update_at = datetime.now(timezone.utc)
                telemetry = Telemetry(
                    device_id=device.id,
                    signal_key=reading.signal_key,
                    value=reading.value,
                    value_string=reading.value_string,
                    quality=nq,
                    source_timestamp=reading.source_timestamp,
                )

            db.add(telemetry)
            db.add(ProcessedMessage(
                consumer_name=CONSUMER_NAME,
                message_id=message_id,
                processed_at=datetime.now(timezone.utc),
            ))
            # Historian row'unu biriktir; dongu SONUNDA tum batch TEK
            # INSERT ... VALUES (...), (...) ile gider (mesaj basina execute yok).
            historian_rows.append({
                "device_id": device.id,
                "signal_key": reading.signal_key,
                "value": reading.value,
                "value_string": reading.value_string,
                "quality": normalize_quality(reading.quality),
                "source_timestamp": reading.source_timestamp,
            })
            seen.add(message_id)  # ayni batch'te duplicate message_id'ye karsi
            ok_msgs.append(msg)
            ok_payloads.append(payload)

            # Outbound dispatch payload'u — status commit ONCESI yakalanir
            # (commit sonrasi device expire olabilir). Dispatch commit SONRASI.
            status_val = (
                device.communication_status.value
                if hasattr(device.communication_status, "value")
                else str(device.communication_status)
            )
            sig_source = None
            if reading.signal_key and "." in reading.signal_key:
                sig_source = reading.signal_key.split(".", 1)[0].lower()
            outbound_payloads.append({
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
                "status": status_val,
                "source_timestamp": reading.source_timestamp.isoformat() if reading.source_timestamp else None,
                "processed_at": datetime.now(timezone.utc).isoformat(),
            })

        # Historian: tum batch TEK statement, idempotent ON CONFLICT.
        if historian_rows:
            db.execute(
                _pg_insert(TelemetryHistory)
                .values(historian_rows)
                .on_conflict_do_nothing(
                    index_elements=["device_id", "signal_key", "source_timestamp"]
                )
            )

        # 3) TEK commit. Basarisizsa (nadir: paralel consumer carpismasi) tum
        # batch redeliver edilir (ack YAPILMADI) -> at-least-once korunur.
        try:
            db.commit()
        except IntegrityError:
            db.rollback()
            logger.warning("telemetry-consumer-batch-commit-conflict — redeliver bekleniyor")
            # Commit patladi: hicbir sey ack'leme, hepsi redeliver olsun.
            # (IN pre-check duplicate'leri eledigi icin bu neredeyse hic tetiklenmez.)
            return [], bad_msgs, [], []
    finally:
        db.close()

    return ok_msgs, bad_msgs, ok_payloads, outbound_payloads


def _dispatch_outbound(outbound_payloads: list) -> None:  # noqa: ANN001
    """Commit sonrasi IEC104/REST/MQTT dispatch. Kendi kisa session'i ile;
    hata telemetri akisini bozmaz (mevcut _persist_message davranisi)."""
    if not outbound_payloads:
        return
    from app.models.outbound_target import OutboundTarget
    from app.services.outbound_dispatch_service import dispatch_event
    from app.services.outbound_telemetry_batcher import submit as rest_batch_submit
    from app.services.mqtt_publisher_service import submit_telemetry as mqtt_submit

    db = SessionLocal()
    try:
        targets = list(
            db.scalars(select(OutboundTarget).where(OutboundTarget.is_active.is_(True))).all()
        )
        for op in outbound_payloads:
            try:
                dispatch_event(
                    db, event_kind="telemetry", payload=op, targets=targets
                )  # IEC104 anlik
                rest_batch_submit(op)  # REST 5sn batch
                mqtt_submit(op)  # MQTT per-target
            except Exception:  # noqa: BLE001
                logger.debug("outbound_dispatch_failed_telemetry msg=%s",
                             op.get("message_id"), exc_info=True)
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
        from nats.errors import TimeoutError as _NatsTimeoutError  # type: ignore[import-not-found]
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

                async def _handle_bad(msg) -> None:  # noqa: ANN001
                    """Parse/validation hatasi olan TEK mesaj: max_deliver'a gore
                    DLQ'ya tasi + ack, degilse nak ile redeliver."""
                    num_delivered = int(
                        getattr(getattr(msg, "metadata", None), "num_delivered", 1) or 1
                    )
                    is_terminal = num_delivered >= settings.nats_worker_max_deliver
                    logger.warning(
                        "telemetry-consumer-bad-msg subject=%s delivery=%d/%d terminal=%s",
                        getattr(msg, "subject", "?"),
                        num_delivered,
                        settings.nats_worker_max_deliver,
                        is_terminal,
                    )
                    if is_terminal:
                        try:
                            orig_subject = getattr(msg, "subject", "unknown")
                            dlq_subject = f"e1.dlq.backend-api.{orig_subject}"
                            dlq_headers = {
                                "X-DLQ-Reason": "max_deliver_exceeded",
                                "X-DLQ-Service": "backend-api",
                                "X-DLQ-Original-Subject": orig_subject,
                                "X-DLQ-Delivery-Count": str(num_delivered),
                            }
                            await js.publish(dlq_subject, msg.data, headers=dlq_headers)
                            logger.error("telemetry-consumer-dlq subject=%s", dlq_subject)
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

                # Durable PULL consumer — subject pattern'i biz fetch ile cekeriz.
                #
                # NEDEN pull (push degil): push consumer'da NATS mesajlari sunucu
                # hizinda client socket'ine iter; backend her mesajda senkron DB
                # yazimi yaptigi icin socket write buffer'i dolar ve NATS
                # "Slow Consumer Detected" ile BAGLANTIYI DUSURUR. 200 cihaz
                # yukunde bu dongu cihaz durumunu (communication_status) kesik
                # kesik gunceller -> yeni cihazlar UNKNOWN kalir. Pull'da backend
                # kendi hizinda fetch(batch) yapar; slow-consumer imkansiz.
                #
                # Consumer parametreleri:
                #   * deliver_policy=NEW: durable ilk olusurken 7 gunluk history'yi
                #     replay etmez, yeni mesajdan baslar.
                #   * ack_wait=60s: persist + WS tipik <100ms; 60sn defansif cap.
                #   * max_ack_pending=10000: 600 cihaz x 10 msg/s ~6000 inflight.
                #   * max_deliver: poison message sonsuz redeliver edilmez -> DLQ.
                from nats.js.api import ConsumerConfig, DeliverPolicy
                consumer_cfg = ConsumerConfig(
                    durable_name=settings.nats_consumer_telemetry_persist,
                    deliver_policy=DeliverPolicy.NEW,
                    ack_wait=60,
                    # Batch-commit consumer: fetch inflight'i sinirlar; batch
                    # boyutunun kati olmali. Backlog'u TEK BASINA cozmez (asil
                    # cozum batch-commit throughput'u), sadece tikanma tavani.
                    max_ack_pending=settings.nats_pull_max_ack_pending,
                    max_deliver=settings.nats_worker_max_deliver,
                )
                psub = await js.pull_subscribe(
                    subject=settings.nats_subject_telemetry_raw,
                    durable=settings.nats_consumer_telemetry_persist,
                    config=consumer_cfg,
                )
                logger.info(
                    "telemetry_consumer_running mode=pull subject=%s durable=%s url=%s",
                    settings.nats_subject_telemetry_raw,
                    settings.nats_consumer_telemetry_persist,
                    settings.nats_url,
                )
                backoff = 2  # connect basarili — backoff sifirla
                # Fetch dongusu: backend kendi hizinda batch ceker, TEK
                # commit ile isler (_persist_batch), commit SONRASI topluca
                # ack'ler. fetch timeout'unda mesaj yoksa TimeoutError normal.
                # Batch-commit sayesinde throughput gelis hizini gecer ->
                # backlog erir, slow-consumer/ack_pending tikanmasi olmaz.
                while not _stop_event.is_set():
                    try:
                        msgs = await psub.fetch(
                            batch=settings.nats_pull_batch_size, timeout=5
                        )
                    except (asyncio.TimeoutError, _NatsTimeoutError):
                        continue  # bu pencerede yeni mesaj yok — normal
                    if not msgs:
                        continue
                    # DB isi ayri thread'de (senkron SQLAlchemy event loop'u
                    # bloke etmesin). _persist_batch TEK commit yapar.
                    ok_msgs, bad_msgs, ok_payloads, outbound_payloads = (
                        await asyncio.to_thread(_persist_batch, msgs)
                    )
                    # DB commit SONRASI ama NATS ack ONCESI yan etkiler. Process
                    # burada crash ederse mesaj redeliver olur; DB idempotency
                    # duplicate'i yutar ve dis akis tekrar denenir (veri kaybi yok).
                    for payload in ok_payloads:
                        try:
                            ws_broadcaster.broadcast(payload)
                        except Exception:  # noqa: BLE001
                            logger.debug("ws_broadcast_failed", exc_info=True)
                    if outbound_payloads:
                        await asyncio.to_thread(_dispatch_outbound, outbound_payloads)
                    # DB + yan etkiler tamam -> iyi/skip mesajlari ack.
                    for m in ok_msgs:
                        try:
                            await m.ack()
                        except Exception:  # noqa: BLE001
                            logger.debug("js_ack_failed", exc_info=True)
                    # Parse/validation hatali mesajlar: DLQ/nak.
                    for m in bad_msgs:
                        await _handle_bad(m)
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
