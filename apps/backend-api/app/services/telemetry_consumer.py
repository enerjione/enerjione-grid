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
import time as _time
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


# --------------------------------------------------------------------------
# ISLEM TELEMETRISI — "tuketici yetisiyor mu?"
#
# NEDEN GEREKLI: telemetri akisi NATS stream'inde tamponlanir ve stream
# `discard=old` ile calisir. Yani tuketici gelis hizinin GERISINE duserse
# tampon dolar ve EN ESKI mesajlar SESSIZCE dusurulur. Ekranda hata yok,
# alarm yok; sadece bazi okumalar hic gelmemis olur.
#
# Bu sessizlik bilincli bir takas (sistem durmasin diye) ama gorunurluk
# olmadan tehlikeli. Asagidaki sayaclar "yetisiyor muyuz" sorusunun tek
# cevabidir.
#
# BACKLOG BEDAVA OLCULUR: JetStream her mesajin metadata'sinda `num_pending`
# tasir — tuketicinin ONUNDE bekleyen mesaj sayisi. Ayrica bir consumer_info
# cagrisi yapmaya gerek yok.
# --------------------------------------------------------------------------

_stats_lock = threading.Lock()
_stats: dict[str, Any] = {
    "running": False,
    "connected": False,
    "last_fetch_at": None,        # ISO string
    "last_batch_size": 0,
    "last_batch_duration_sec": 0.0,
    "backlog": None,              # num_pending — tuketicinin onunde bekleyen
    "processed_total": 0,
    "bad_total": 0,
    "reconnects": 0,
    "last_error": None,
}
# Throughput icin kayan pencere: (zaman, islenen_adet) ciftleri.
_throughput_window: list[tuple[float, int]] = []
_THROUGHPUT_WINDOW_SEC = 60.0


def _stats_update(**kwargs: Any) -> None:
    with _stats_lock:
        _stats.update(kwargs)


def _stats_record_batch(*, size: int, duration: float, backlog: int | None, bad: int) -> None:
    now = _time.monotonic()
    with _stats_lock:
        _stats["last_fetch_at"] = datetime.now(timezone.utc).isoformat()
        _stats["last_batch_size"] = size
        _stats["last_batch_duration_sec"] = round(duration, 4)
        _stats["processed_total"] += size
        _stats["bad_total"] += bad
        if backlog is not None:
            _stats["backlog"] = backlog
        _throughput_window.append((now, size))
        cutoff = now - _THROUGHPUT_WINDOW_SEC
        while _throughput_window and _throughput_window[0][0] < cutoff:
            _throughput_window.pop(0)


# "Henuz hic uyarilmadi" = None. 0.0 DEGIL — bilincli.
#
# `time.monotonic()` sabit bir baslangic noktasi vermez; Linux'ta makine
# acilisindan beri gecen suredir. Baslangic degeri 0.0 olsaydi acilistan
# sonraki ILK `telemetry_backlog_warn_interval_sec` saniye boyunca
# `now - 0.0 < interval` cikar ve ILK uyari bastirilirdi — hem de tam
# backlog'un en yuksek oldugu an, yeniden baslatmanin hemen ardindan.
# (Windows'ta uptime buyuk oldugu icin bu davranis gorunmuyordu; hatayi
# Linux CI ortaya cikardi.)
_last_backlog_warn_at: float | None = None


def _warn_if_backlog_high(backlog: int | None) -> None:
    """Backlog esigi asilirsa loglar ve denetim kaydina yazar.

    Bu, `discard=old` tercihinin karsiligidir: tampon tasarsa mesajlar
    SESSIZCE dusuruluyor, dolayisiyla tasmaya YAKLASILDIGINI haber verecek
    bir sinyal sart. Esik, stream tavaninin cok altinda tutulur ki operatorun
    mudahale etmesi icin zaman kalsin.

    Olay kaydi rate-limit'lidir; backlog saatlerce yuksek kalsa bile
    `system_events` tablosunu doldurmaz.
    """
    global _last_backlog_warn_at
    if backlog is None or backlog < settings.telemetry_backlog_warn_threshold:
        return
    now = _time.monotonic()
    # ILK uyari her zaman gecer; rate-limit yalnizca IKINCIDEN itibaren isler.
    # Mutlak degeri karsilastirmak yerine "daha once uyardik mi" sorusunu
    # sormak, monotonic()'in baslangic noktasindan bagimsiz kilar.
    if (
        _last_backlog_warn_at is not None
        and now - _last_backlog_warn_at < settings.telemetry_backlog_warn_interval_sec
    ):
        return
    _last_backlog_warn_at = now
    snapshot = get_stats()
    logger.warning(
        "telemetry_backlog_high backlog=%d threshold=%d throughput=%.1f msg/s "
        "(tuketici gelis hizinin gerisinde — tampon tasarsa VERI KAYBI baslar)",
        backlog,
        settings.telemetry_backlog_warn_threshold,
        snapshot.get("throughput_msgs_per_sec", 0.0),
    )
    try:
        from app.services.event_service import record_event

        db = SessionLocal()
        try:
            record_event(
                db,
                category="telemetry",
                event_type="telemetry_backlog_high",
                severity="warning",
                message=(
                    f"Telemetri tuketicisi geride: {backlog} mesaj bekliyor "
                    f"({snapshot.get('throughput_msgs_per_sec', 0.0)} msg/sn isleniyor)"
                ),
                metadata={
                    "backlog": backlog,
                    "threshold": settings.telemetry_backlog_warn_threshold,
                    "throughput_msgs_per_sec": snapshot.get("throughput_msgs_per_sec"),
                    "last_batch_duration_sec": snapshot.get("last_batch_duration_sec"),
                },
            )
            db.commit()
        finally:
            db.close()
    except Exception:  # noqa: BLE001
        logger.debug("backlog_event_record_failed", exc_info=True)


def get_stats() -> dict[str, Any]:
    """Tuketicinin anlik durumu. Sistem Durumu sayfasi bunu okur.

    `throughput_msgs_per_sec` son 60 saniyelik kayan ortalamadir; anlik
    dalgalanmalari yumusatir. `backlog` en son fetch anindaki num_pending —
    surekli 0 civarinda olmasi beklenir; kalici olarak buyuyorsa tuketici
    gelis hizinin gerisindedir ve tampon tasarsa VERI KAYBI baslar.
    """
    with _stats_lock:
        snapshot = dict(_stats)
        if _throughput_window:
            span = max(1e-6, _throughput_window[-1][0] - _throughput_window[0][0])
            total = sum(n for _t, n in _throughput_window)
            # Tek ornek varsa span ~0 olur; bolmeyi anlamli tutmak icin
            # en az 1 saniye kabul ediyoruz.
            snapshot["throughput_msgs_per_sec"] = round(total / max(1.0, span), 2)
        else:
            snapshot["throughput_msgs_per_sec"] = 0.0
    return snapshot


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
    from app.services.device_clock_service import assess_device_timestamp
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
                _fb_at, _fb_quality = assess_device_timestamp(
                    getattr(reading, "device_event_at", None),
                    reported_quality=getattr(reading, "timestamp_quality", None),
                )
                telemetry = Telemetry(
                    device_id=device.id,
                    signal_key=reading.signal_key,
                    value=reading.value,
                    value_string=reading.value_string,
                    quality=nq,
                    source_timestamp=reading.source_timestamp,
                    # Fallback yolunda da damgalanir: aksi halde is-mantigi
                    # hatasi alan mesajlarda saat durumu SESSIZCE kaybolurdu.
                    device_event_at=_fb_at,
                    timestamp_quality=_fb_quality,
                )

            db.add(telemetry)
            db.add(ProcessedMessage(
                consumer_name=CONSUMER_NAME,
                message_id=message_id,
                processed_at=datetime.now(timezone.utc),
            ))
            # Historian row'unu biriktir; dongu SONUNDA tum batch TEK
            # INSERT ... VALUES (...), (...) ile gider (mesaj basina execute yok).
            # Cihazin kendi olay zamani (varsa) + makullugu. `source_timestamp`
            # AYNEN kaliyor (PK/partition kolonu); bu ikisi yalnizca analiz
            # icin ayri kolonlarda duruyor. Bkz. device_clock_service.
            #
            # Degerlendirme TEKRAR yapilmaz: canli satiri kuran
            # `process_telemetry_reading` zaten damgaladi. Ikinci kez cagirmak
            # 7 gunluk pencerenin tam sinirinda iki satirin FARKLI kalite
            # almasina yol acabilirdi (araya gecen mikrosaniyeler yuzunden).
            _dev_at = telemetry.device_event_at
            _ts_quality = telemetry.timestamp_quality
            historian_rows.append({
                "device_id": device.id,
                "signal_key": reading.signal_key,
                "value": reading.value,
                "value_string": reading.value_string,
                "quality": normalize_quality(reading.quality),
                "source_timestamp": reading.source_timestamp,
                "device_event_at": _dev_at,
                "timestamp_quality": _ts_quality,
            })
            seen.add(message_id)  # ayni batch'te duplicate message_id'ye karsi
            ok_msgs.append(msg)
            # WS yayini ham gateway payload'unu tasir; saat degerlendirmesini
            # UZERINE YAZIYORUZ. Gateway'in ham bildirimi degil BIZIM
            # degerlendirmemiz otoriter: gateway hic bir sey demese bile
            # 2000-01-01 damgasini "invalid" olarak isaretleyen biziz. Aksi
            # halde canli ekran (WS) ile yenilenmis snapshot (`/signals/live`)
            # ayni satir icin farkli sey gosterirdi.
            payload["device_event_at"] = _dev_at.isoformat() if _dev_at else None
            payload["timestamp_quality"] = _ts_quality
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
                _stats_update(connected=True, last_error=None)
                while not _stop_event.is_set():
                    try:
                        msgs = await psub.fetch(
                            batch=settings.nats_pull_batch_size, timeout=5
                        )
                    except (asyncio.TimeoutError, _NatsTimeoutError):
                        # Bu pencerede yeni mesaj yok — NORMAL ve ayni zamanda
                        # "backlog bos" demektir; olcume yansitiyoruz ki
                        # ekranda bayat bir backlog degeri asili kalmasin.
                        _stats_update(backlog=0)
                        continue
                    if not msgs:
                        _stats_update(backlog=0)
                        continue
                    _batch_started = _time.monotonic()
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

                    # --- Olcum: yetisiyor muyuz? --------------------------
                    # Backlog'u BEDAVA aliyoruz: JetStream her mesajin
                    # metadata'sinda `num_pending` tasir (bu mesajdan SONRA
                    # tuketicinin onunde bekleyen adet). Ayrica consumer_info
                    # cagrisi yapmaya gerek yok. Son mesaji baz aliyoruz;
                    # batch'in en guncel noktasi orasi.
                    backlog: int | None = None
                    try:
                        meta = getattr(msgs[-1], "metadata", None)
                        if meta is not None and getattr(meta, "num_pending", None) is not None:
                            backlog = int(meta.num_pending)
                    except Exception:  # noqa: BLE001
                        backlog = None
                    _stats_record_batch(
                        size=len(ok_msgs),
                        duration=_time.monotonic() - _batch_started,
                        backlog=backlog,
                        bad=len(bad_msgs),
                    )
                    _warn_if_backlog_high(backlog)
            except Exception as exc:  # noqa: BLE001
                if _stop_event.is_set():
                    break
                logger.warning(
                    "telemetry_consumer_reconnect error=%s backoff=%ds url=%s",
                    exc,
                    backoff,
                    settings.nats_url,
                )
                with _stats_lock:
                    _stats["connected"] = False
                    _stats["last_error"] = str(exc)[:300]
                    _stats["reconnects"] += 1
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
    _stats_update(running=True, connected=False, last_error=None)
    _thread = threading.Thread(
        target=_consume_loop, name="telemetry-consumer-jetstream", daemon=True
    )
    _thread.start()


def stop() -> None:
    _stop_event.set()
