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
    from nats.js.api import (  # type: ignore[import-not-found]
        DiscardPolicy,
        RetentionPolicy,
        StorageType,
        StreamConfig,
    )

    NATS_AVAILABLE = True
except ImportError:  # pragma: no cover - optional dependency
    nats = None  # type: ignore[assignment]
    NATS_AVAILABLE = False


EventHandler = Callable[[dict[str, Any]], None]


def topic_to_subject(topic: str, *, gateway_code: str | None = None) -> str:
    """RabbitMQ routing key'i JetStream subject'ine cevirir.

    Cati 0.x cutover sonrasi tum servisler `e1.*` prefix kullanir
    (eski `hsl.*` artik geriye uyumluluk icin de degil — gateway'ler
    `e1.telemetry.raw.<gw>` basar, tag-engine `e1.telemetry.normalized.<gw>`
    yayar). Prefix tutarsizligi production'da telemetri akisini koparir.

    Esleme:
      - "telemetry.raw_received"  -> "e1.telemetry.raw.<gw>"
      - "telemetry.received"      -> "e1.telemetry.normalized.<gw>"
      - diger                      -> "e1.events.<topic>" (fallback)

    gateway_code yoksa "unknown" kullanilir; consumer wildcard ile yine alir.
    """
    gw = gateway_code or "unknown"
    if topic == "telemetry.raw_received":
        return f"e1.telemetry.raw.{gw}"
    if topic == "telemetry.received":
        return f"e1.telemetry.normalized.{gw}"
    safe_topic = topic.replace(".", "-")
    return f"e1.events.{safe_topic}"


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
        stream_dlq: str,
        subject_raw_pattern: str,
        subject_normalized_pattern: str,
        subject_dlq_pattern: str,
        max_age_days_raw: int,
        max_age_days_normalized: int,
        max_age_days_dlq: int,
        connect_timeout_sec: int,
        # Stream basina DISK tavani. 0 = sinirsiz (eski davranis). Varsayilan
        # deger vermiyoruz ki cagiran taraf bilincli gecsin; bkz. config.py
        # nats_stream_*_max_bytes.
        max_bytes_raw: int = 0,
        max_bytes_normalized: int = 0,
        max_bytes_dlq: int = 0,
    ) -> None:
        self._url = url
        self._stream_raw = stream_raw
        self._stream_normalized = stream_normalized
        self._stream_dlq = stream_dlq
        self._subject_raw = subject_raw_pattern
        self._subject_normalized = subject_normalized_pattern
        self._subject_dlq = subject_dlq_pattern
        # NOT: nats-py StreamConfig `max_age` field'ini SANIYE olarak alir
        # (yorumda "in seconds"). `as_dict()` icinde otomatik `_to_nanoseconds`
        # ile NATS wire format'a cevirilir. Onceden burada manuel nanosaniyeye
        # cevriyorduk → as_dict bir kere daha cevirip int64 tasiriyor, NATS
        # server `BadRequestError code=400 err_code=10025 "invalid JSON"`
        # ile reddediyordu. Saniye olarak vermek dogru kullanim.
        self._max_age_raw_sec = max_age_days_raw * 24 * 60 * 60
        self._max_age_normalized_sec = max_age_days_normalized * 24 * 60 * 60
        self._max_age_dlq_sec = max_age_days_dlq * 24 * 60 * 60
        self._max_bytes_raw = max_bytes_raw
        self._max_bytes_normalized = max_bytes_normalized
        self._max_bytes_dlq = max_bytes_dlq
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
            "jetstream_bus_ready url=%s streams=[%s, %s, %s]",
            self._url,
            self._stream_raw,
            self._stream_normalized,
            self._stream_dlq,
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
            name="e1-backend-api",
        )
        self._js = self._nc.jetstream()
        await self._ensure_stream(
            name=self._stream_raw,
            subject=self._subject_raw,
            max_age_sec=self._max_age_raw_sec,
            max_bytes=self._max_bytes_raw,
        )
        await self._ensure_stream(
            name=self._stream_normalized,
            subject=self._subject_normalized,
            max_age_sec=self._max_age_normalized_sec,
            max_bytes=self._max_bytes_normalized,
        )
        # DLQ stream — workerlarin max_deliver'a takilan "poison" mesajlari
        # manual publish ile buraya tasinir. NATS default'unda max_deliver
        # asildiginda mesaj sessizce kaybolur; DLQ ile operator gorebilir,
        # root-cause sonra replay edebilir.
        await self._ensure_stream(
            name=self._stream_dlq,
            subject=self._subject_dlq,
            max_age_sec=self._max_age_dlq_sec,
            max_bytes=self._max_bytes_dlq,
        )

    async def _disconnect(self) -> None:
        if self._nc is not None:
            try:
                await self._nc.drain()
            except Exception:  # noqa: BLE001
                logger.debug("jetstream_drain_error", exc_info=True)

    async def _ensure_stream(
        self, *, name: str, subject: str, max_age_sec: int, max_bytes: int = 0
    ) -> None:
        """Stream yoksa olustur; varsa config drift'i varsa update_stream cagrir.

        Drift senaryosu: stream daha onceki cutover'da eski subject
        (`hsl.telemetry.*`) ile olusturuldu; cati `e1.*`'ye gecti. Eger
        sadece add_stream çağrılırsa "Stream already exists" hatası alınır
        ve eski subject kalir → yeni mesajlar stream'e DUSEMEZ.

        DRIFT KONTROLU NEYE BAKAR — ve neden onemli:
          Bu kontrol bir zamanlar YALNIZCA `subjects` + `max_age`
          karsilastiriyordu. Sonucu su oldu: stream'e yeni bir sinir
          (ornegin `max_bytes`) eklendiginde MEVCUT kurulumlarda hicbir sey
          degismiyordu — cunku subject ve max_age ayni kaldigi icin "drift
          yok" deyip erken donuyordu. Yani "disk tavani ekledik" denip
          release edilir, sahadaki diskler dolmaya devam ederdi. Yeni bir
          sinir alani eklerken BU LISTEYE DE EKLEYIN.
        """
        # NOT: NATS 2.10 server `-1`'i max_msgs / max_bytes icin REDDEDIYOR
        # (BadRequestError code=10025 err='invalid JSON'). Doğru konvansiyon
        # `0 = unlimited` (sunucu pozitif veya 0 bekler; field eksikse
        # default 0). nats-py StreamConfig dataclass `int | None` kabul
        # ediyor ama None gondersek field bos kalmiyor — bizim eski `-1`
        # sentinel'i seriyi koparmis. 0 ile geceriz, max_age ZATEN retention'i
        # saglar.
        # nats-py StreamConfig.max_age SANIYE bekler ("# in seconds" — api.py).
        # `as_dict()` cagrisi otomatik nanosaniyeye cevirir. Burada saniye
        # gondermeliyiz, aksi halde iki kere nano cevrilip int64 tasiyor
        # → server BadRequestError code=10025 "invalid JSON".
        cfg = StreamConfig(  # type: ignore[union-attr]
            name=name,
            subjects=[subject],
            retention=RetentionPolicy.LIMITS,  # type: ignore[union-attr]
            storage=StorageType.FILE,  # type: ignore[union-attr]
            max_age=max_age_sec,
            max_msgs=0,
            # DISK TAVANI. 0 = sinirsiz (eski davranis). max_age tek basina
            # bayt tavani VERMEZ: disk kullanimi hiz x zamandir.
            max_bytes=max_bytes,
            # Tavana carpinca EN ESKI mesajlari dus, publish'i REDDETME.
            # Bu, "izleme sistemi asla durmasin" tercihinin ta kendisi:
            # DiscardPolicy.NEW olsaydi tampon dolunca gateway telemetri
            # basamaz ve akis tamamen dururdu. Takas bilincli — uzun bir
            # kesintide en eski mesajlar sessizce kaybolur.
            discard=DiscardPolicy.OLD,  # type: ignore[union-attr]
        )
        try:
            info = await self._js.stream_info(name)
            # Stream var — config drift kontrolu. info.config.max_age da
            # `from_response`'da _convert_nanoseconds ile saniyeye cevrilmis;
            # ikisi de saniye, dogrudan karsilastirilir.
            existing_subjects = list(getattr(info.config, "subjects", []) or [])
            existing_max_age = int(getattr(info.config, "max_age", 0) or 0)
            existing_max_bytes = int(getattr(info.config, "max_bytes", 0) or 0)
            existing_discard = getattr(info.config, "discard", None)
            # DiscardPolicy bir str enum; hem enum hem ham string gelebilir.
            existing_discard_val = getattr(existing_discard, "value", existing_discard)
            if (
                sorted(existing_subjects) == sorted([subject])
                and existing_max_age == max_age_sec
                and existing_max_bytes == max_bytes
                and existing_discard_val == DiscardPolicy.OLD.value  # type: ignore[union-attr]
            ):
                # Drift yok — dokunma
                return
            logger.info(
                "jetstream_stream_drift name=%s max_bytes %s->%s discard %s->old",
                name,
                existing_max_bytes,
                max_bytes,
                existing_discard_val,
            )
            # Drift var — update_stream ile yenile
            await self._js.update_stream(cfg)
            logger.warning(
                "jetstream_stream_updated name=%s old_subjects=%s new_subject=%s "
                "(cutover drift duzeltildi)",
                name,
                existing_subjects,
                subject,
            )
            return
        except Exception as exc:  # noqa: BLE001
            # Stream yok veya stream_info hata verdi → add_stream dene
            err_msg = str(exc).lower()
            if "not found" not in err_msg and "no stream" not in err_msg:
                logger.debug("jetstream_stream_info_unexpected_error", exc_info=True)

        try:
            await self._js.add_stream(cfg)
            logger.info("jetstream_stream_created name=%s subject=%s", name, subject)
        except Exception as exc:  # noqa: BLE001
            # add_stream da hata verdi (stream var olabilir race kondisyonu)
            # → tekrar update dene; olmazsa kalk.
            err_msg = str(exc).lower()
            if "already" in err_msg or "exists" in err_msg:
                await self._js.update_stream(cfg)
                logger.warning(
                    "jetstream_stream_updated_after_race name=%s subject=%s",
                    name,
                    subject,
                )
                return
            raise

    # ---- Public sync API ----------------------------------------------------
    def publish_event(
        self,
        topic: str,
        payload: dict[str, Any],
        *,
        message_id: str = "",
    ) -> None:
        """JetStream'e publish et; basarisizlikta exception yay.

        Telemetri artik JetStream'e TEK yoldan gider. Best-effort/no-op veri
        kaybettirir: outbox caller'i basari sanip row'u published=True yapar.
        Exception sayesinde row published=False kalir ve worker retry eder.
        """
        if not self._ready.is_set() or self._loop is None or self._js is None:
            raise RuntimeError("jetstream bus not ready")

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
                    "consecutive=%s (outbox retry edecek)",
                    subject,
                    msg_id,
                    exc,
                    self._publish_failures,
                )
            raise

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
            stream_dlq=settings.nats_stream_telemetry_dlq,
            subject_raw_pattern=settings.nats_subject_telemetry_raw,
            subject_normalized_pattern=settings.nats_subject_telemetry_normalized,
            subject_dlq_pattern=settings.nats_subject_telemetry_dlq,
            max_age_days_raw=settings.nats_stream_raw_max_age_days,
            max_age_days_normalized=settings.nats_stream_normalized_max_age_days,
            max_age_days_dlq=settings.nats_stream_dlq_max_age_days,
            connect_timeout_sec=settings.nats_connect_timeout_sec,
            # Stream basina disk tavani — max_age tek basina bayt siniri
            # vermez. Tavana carpinca discard=OLD ile en eskiler duser,
            # publish REDDEDILMEZ (akis durmaz).
            max_bytes_raw=settings.nats_stream_raw_max_bytes,
            max_bytes_normalized=settings.nats_stream_normalized_max_bytes,
            max_bytes_dlq=settings.nats_stream_dlq_max_bytes,
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
