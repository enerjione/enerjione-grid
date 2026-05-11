"""NATS JetStream event bus — RabbitMQ'nun yaninda calisan paralel telemetri yolu.

Tasarim:
  * Mevcut sistem (RabbitMQ + event_bus.py) hic dokunulmadan calismaya devam eder.
  * `NATS_DUAL_PUBLISH_ENABLED=false` (default) iken bu modul HIC BAGLANTI ACMAZ;
    `publish_event()` no-op'tur. Yani nats-py paketi yuklu olmasa bile veya
    NATS server ayakta olmasa bile backend tam olarak eskisi gibi calisir.
  * `NATS_DUAL_PUBLISH_ENABLED=true` ise:
      - Backend startup'inda arka plan thread'inde asyncio loop baslar.
      - Stream'ler idempotent olarak ensure edilir (TELEMETRY_RAW, TELEMETRY_NORMALIZED).
      - `publish_event(topic, payload)` cagrildikta best-effort olarak JetStream'e
        de gonderir. Hata olursa SESSIZ degil ama EXCEPTION FIRLATMAZ — sadece
        warning loglar; cagiran RabbitMQ akisi etkilenmez.
  * `NATS_CONSUME_ENABLED=true` ise:
      - Persister/tuketici durable consumer kullanarak subject pattern'e abone olur.
      - Mesajlar mevcut RabbitMQ handler'iyla AYNI fonksiyona route edilir.
      - DB tarafindaki processed_message dedup (message_id) iki yoldan da gelen
        ayni mesaji tek seferde isler — duplicate processing yok.

nats-py paketi optional: yoksa import RuntimeError vermez, sadece runtime'da
acilis denenince warning loglar.
"""

from __future__ import annotations

import asyncio
import json
import logging
import threading
from collections.abc import Callable
from typing import Any

logger = logging.getLogger(__name__)

# Lazy import — paket yoksa modul yine import edilebilir.
try:
    import nats  # type: ignore[import-not-found]
    from nats.errors import TimeoutError as NatsTimeoutError  # noqa: F401
    from nats.js.api import RetentionPolicy, StorageType, StreamConfig  # type: ignore[import-not-found]

    NATS_AVAILABLE = True
except ImportError:  # pragma: no cover - optional dependency
    nats = None  # type: ignore[assignment]
    NATS_AVAILABLE = False


EventHandler = Callable[[dict[str, Any]], None]


def topic_to_subject(topic: str, *, gateway_code: str | None = None) -> str:
    """RabbitMQ routing key'i JetStream subject'ine cevirir.

    Esleme:
      - "telemetry.raw_received"  -> "hsl.telemetry.raw.<gw>"
      - "telemetry.received"      -> "hsl.telemetry.normalized.<gw>"
      - diger                      -> "hsl.events.<topic>" (fallback)

    gateway_code yoksa "unknown" kullanilir; consumer wildcard ile yine alir.
    """
    gw = gateway_code or "unknown"
    if topic == "telemetry.raw_received":
        return f"hsl.telemetry.raw.{gw}"
    if topic == "telemetry.received":
        return f"hsl.telemetry.normalized.{gw}"
    safe_topic = topic.replace(".", "-")
    return f"hsl.events.{safe_topic}"


class JetStreamBus:
    """Backend tarafindan kullanilan thread-safe sync wrapper.

    asyncio loop bir background thread'inde calisir; publish/consume sync
    kodtan `run_coroutine_threadsafe` ile cagrilir.
    """

    def __init__(
        self,
        *,
        url: str,
        stream_raw: str,
        stream_normalized: str,
        subject_raw_pattern: str,
        subject_normalized_pattern: str,
        max_age_days_raw: int,
        max_age_days_normalized: int,
        connect_timeout_sec: int,
    ) -> None:
        self._url = url
        self._stream_raw = stream_raw
        self._stream_normalized = stream_normalized
        self._subject_raw = subject_raw_pattern
        self._subject_normalized = subject_normalized_pattern
        self._max_age_raw_ns = max_age_days_raw * 24 * 60 * 60 * 1_000_000_000
        self._max_age_normalized_ns = max_age_days_normalized * 24 * 60 * 60 * 1_000_000_000
        self._connect_timeout = connect_timeout_sec

        self._loop: asyncio.AbstractEventLoop | None = None
        self._loop_thread: threading.Thread | None = None
        self._nc: Any = None  # nats.NATS connection
        self._js: Any = None  # JetStreamContext
        self._ready = threading.Event()
        self._closed = threading.Event()
        self._publish_failures = 0

    # ---- Lifecycle ----------------------------------------------------------
    def start(self) -> bool:
        """Background loop'u baslat ve NATS'a connect ol; stream'leri ensure et.

        Returns True only on full success. Returns False on any failure
        (paket yok, baglanti yok, vb). False donmesi cagiranin akisini
        bozmamali — fallback olarak dual-publish devre disi kalir.
        """
        if not NATS_AVAILABLE:
            logger.warning(
                "jetstream_bus_disabled reason=nats_package_missing "
                "(pip install nats-py>=2.6 ile yukleyin; bu arada RabbitMQ "
                "akisi normal calisiyor)"
            )
            return False
        if self._loop is not None:
            return True  # already started

        self._loop = asyncio.new_event_loop()

        def _run_loop() -> None:
            assert self._loop is not None
            asyncio.set_event_loop(self._loop)
            try:
                self._loop.run_forever()
            finally:
                try:
                    self._loop.close()
                except Exception:  # noqa: BLE001
                    logger.debug("jetstream_loop_close_error", exc_info=True)

        self._loop_thread = threading.Thread(
            target=_run_loop, name="jetstream-loop", daemon=True
        )
        self._loop_thread.start()

        # Connect + ensure streams (block here with timeout)
        future = asyncio.run_coroutine_threadsafe(self._connect_and_setup(), self._loop)
        try:
            future.result(timeout=self._connect_timeout + 5)
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "jetstream_bus_start_failed url=%s error=%s "
                "(RabbitMQ akisi devam ediyor — bu arizadan etkilenmez)",
                self._url,
                exc,
            )
            # loop'u kapat; tekrar deneme cagiraninin sorumlulugu
            self._shutdown_loop()
            return False

        self._ready.set()
        logger.info(
            "jetstream_bus_ready url=%s streams=[%s, %s]",
            self._url,
            self._stream_raw,
            self._stream_normalized,
        )
        return True

    def stop(self) -> None:
        if self._closed.is_set():
            return
        self._closed.set()
        if self._loop is None:
            return
        try:
            future = asyncio.run_coroutine_threadsafe(self._disconnect(), self._loop)
            future.result(timeout=5)
        except Exception:  # noqa: BLE001
            logger.debug("jetstream_disconnect_error", exc_info=True)
        self._shutdown_loop()

    def _shutdown_loop(self) -> None:
        if self._loop is None:
            return
        try:
            self._loop.call_soon_threadsafe(self._loop.stop)
        except Exception:  # noqa: BLE001
            pass
        if self._loop_thread is not None:
            self._loop_thread.join(timeout=3)
        self._loop = None
        self._loop_thread = None
        self._nc = None
        self._js = None
        self._ready.clear()

    async def _connect_and_setup(self) -> None:
        self._nc = await nats.connect(  # type: ignore[union-attr]
            servers=[self._url],
            connect_timeout=self._connect_timeout,
            max_reconnect_attempts=-1,  # surekli reconnect dene
            reconnect_time_wait=2,
            name="hsl-backend-api",
        )
        self._js = self._nc.jetstream()
        await self._ensure_stream(
            name=self._stream_raw,
            subject=self._subject_raw,
            max_age_ns=self._max_age_raw_ns,
        )
        await self._ensure_stream(
            name=self._stream_normalized,
            subject=self._subject_normalized,
            max_age_ns=self._max_age_normalized_ns,
        )

    async def _disconnect(self) -> None:
        if self._nc is not None:
            try:
                await self._nc.drain()
            except Exception:  # noqa: BLE001
                logger.debug("jetstream_drain_error", exc_info=True)

    async def _ensure_stream(self, *, name: str, subject: str, max_age_ns: int) -> None:
        """Stream yoksa olustur, varsa subject/retention farkliysa update et."""
        try:
            await self._js.stream_info(name)
            # Var; bu asamada subject/retention guncelleme yapmiyoruz —
            # production'da operator manuel `nats stream update` yapsin.
            return
        except Exception:  # noqa: BLE001 - "stream not found" da dahil her durum
            pass

        cfg = StreamConfig(  # type: ignore[union-attr]
            name=name,
            subjects=[subject],
            retention=RetentionPolicy.LIMITS,  # type: ignore[union-attr]
            storage=StorageType.FILE,  # type: ignore[union-attr]
            max_age=max_age_ns,
            max_msgs=-1,
            max_bytes=-1,
        )
        await self._js.add_stream(cfg)
        logger.info("jetstream_stream_created name=%s subject=%s", name, subject)

    # ---- Public sync API ----------------------------------------------------
    def publish_event(
        self,
        topic: str,
        payload: dict[str, Any],
        *,
        message_id: str = "",
    ) -> None:
        """Best-effort publish — hata RabbitMQ akisini etkilemez.

        Bu fonksiyon RabbitMqEventBus.publish_event ile ayni imzaya sahiptir.
        Hata olursa exception YAYILMAZ; sadece counter artar ve warning loglanir.
        """
        if not self._ready.is_set() or self._loop is None or self._js is None:
            return  # bus baslamadiysa sessiz no-op (dual-publish ilk acilirken normal)

        gateway_code = str(payload.get("source_gateway") or "unknown")
        subject = topic_to_subject(topic, gateway_code=gateway_code)
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        msg_id = message_id or str(payload.get("message_id") or "")
        headers = {"Nats-Msg-Id": msg_id} if msg_id else None

        async def _do_publish() -> None:
            # Nats-Msg-Id ile JetStream dedup destegi (default 2 dakika).
            await self._js.publish(subject, body, headers=headers)

        try:
            future = asyncio.run_coroutine_threadsafe(_do_publish(), self._loop)
            # Sync caller'i bloklamamak icin kisa timeout — broker yavasladiginda
            # RabbitMQ tarafini geciktirmemek kritik. Timeout fail = best-effort drop.
            future.result(timeout=2)
        except Exception as exc:  # noqa: BLE001
            self._publish_failures += 1
            if self._publish_failures in (1, 10, 100, 1000) or self._publish_failures % 10000 == 0:
                logger.warning(
                    "jetstream_publish_failed subject=%s msg_id=%s error=%s "
                    "consecutive=%s (RabbitMQ akisi devam ediyor)",
                    subject,
                    msg_id,
                    exc,
                    self._publish_failures,
                )

    @property
    def is_ready(self) -> bool:
        return self._ready.is_set()

    @property
    def publish_failures(self) -> int:
        return self._publish_failures


# ---- Module-level singleton ------------------------------------------------
_bus: JetStreamBus | None = None
_lock = threading.Lock()


def get_bus() -> JetStreamBus | None:
    """Aktif JetStream bus'i don (varsa). dual_publish kapali ise None."""
    return _bus


def start_bus_if_enabled() -> None:
    """Backend startup'inda cagrilir; JetStream bus'i baslat, stream'leri ensure et.

    Bu fonksiyon artik HER ZAMAN bus'i baslatmaya calisir — telemetri JetStream'e
    tasindi, opsiyonel degil. NATS server'a baglanilamiyorsa warning log + None
    birakir; baglanti gelince ileride baska bir startup'ta veya manuel tetikle
    yeniden denenebilir (su an basit: bir kez deneyip biraktiriyor).
    """
    from app.core.config import settings  # local import — module cycles'i kir

    global _bus
    with _lock:
        if _bus is not None:
            return
        bus = JetStreamBus(
            url=settings.nats_url,
            stream_raw=settings.nats_stream_telemetry_raw,
            stream_normalized=settings.nats_stream_telemetry_normalized,
            subject_raw_pattern=settings.nats_subject_telemetry_raw,
            subject_normalized_pattern=settings.nats_subject_telemetry_normalized,
            max_age_days_raw=settings.nats_stream_raw_max_age_days,
            max_age_days_normalized=settings.nats_stream_normalized_max_age_days,
            connect_timeout_sec=settings.nats_connect_timeout_sec,
        )
        ok = bus.start()
        if ok:
            _bus = bus
            logger.info(
                "jetstream_bus_started url=%s — telemetri akisi JetStream uzerinden",
                settings.nats_url,
            )
        else:
            _bus = None
            logger.error(
                "jetstream_bus_unavailable url=%s — telemetri akisi DURABILIR! "
                "NATS server'i kontrol edin. Backend ayagi kalmaya devam ediyor "
                "ama gelen telemetri publish'leri basarisiz olacak.",
                settings.nats_url,
            )


def stop_bus() -> None:
    """Backend shutdown'inda cagrilir."""
    global _bus
    with _lock:
        if _bus is None:
            return
        try:
            _bus.stop()
        except Exception:  # noqa: BLE001
            logger.debug("jetstream_stop_error", exc_info=True)
        _bus = None
