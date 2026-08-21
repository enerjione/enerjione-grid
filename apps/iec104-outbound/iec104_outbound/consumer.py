"""NATS JetStream tuketicisi — tag-engine'den gelen `telemetry.normalized.<gw>`
mesajlarini IEC 104 server manager'a koprü ile aktarir.

Tasarim: NATS asyncio loop kendi thread'inde calisir. Ana thread'de IEC 104
asyncio loop var; consumer her mesaj icin `manager.update_point_threadsafe`
cagirisi yaparak ana loop'a tasi. Boylece IEC 104 server async dunyasinda kalir,
mesaj IO ise ayri bir async dunyada (NATS) yapilir.

Reconnect: NATS dustugunde expo backoff (2s -> 60s cap). Durable consumer ile
process restart'ta kaldigi yerden devam eder.
"""

from __future__ import annotations

import asyncio
import json
import logging
from iec104_outbound.redaction import redact_url_credentials
import os
import ssl
from datetime import datetime
from threading import Event, Thread

import nats

from iec104_outbound.config import Settings
from iec104_outbound.server import IEC104ServerManager

logger = logging.getLogger(__name__)


_GOOD_QUALITIES = {"good", "ok", ""}


def _nats_tls_context():
    """NATS_CA_FILE ayarliysa dogrulayici SSL baglami, degilse None.

    NEDEN: NATS istemci portu tum arayuzlere acik ve baglantilar parolali;
    TLS'siz hem parola hem telemetri duz metin gider.

    `create_default_context(cafile=...)` YALNIZCA verilen CA'ya guvenir —
    isletim sisteminin guven deposu kullanilmaz. Bu bilincli: aksi halde
    herkese acik bir otoriteden alinmis sertifika da kabul edilirdi.

    Dosya okunamazsa HATA yukselir; TLS'siz devam etmek, operatorun "TLS
    acik" sandigi bir kurulumda parolayi acikta gondermek olurdu.
    """
    ca_file = (os.getenv("NATS_CA_FILE") or "").strip()
    if not ca_file:
        return None
    return ssl.create_default_context(cafile=ca_file)


def _is_good(quality: str | None) -> bool:
    return str(quality or "good").strip().lower() in _GOOD_QUALITIES


def _parse_iso_timestamp(raw: str | None) -> datetime | None:
    """ISO 8601 zaman damgasini datetime'e cevir; basarisizsa None.

    Tipik formatlar: "2026-05-06T12:34:56.123+00:00", "2026-05-06T12:34:56Z".
    Python 3.10 fromisoformat 'Z' suffix'ini desteklemez; manuel cevir.
    """
    if not raw or not isinstance(raw, str):
        return None
    s = raw.strip()
    if not s:
        return None
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    try:
        return datetime.fromisoformat(s)
    except ValueError:
        return None


class TelemetryConsumer:
    """JetStream consumer; kendi asyncio loop'unda calisir, daemon thread."""

    def __init__(self, *, settings: Settings, manager: IEC104ServerManager) -> None:
        self.settings = settings
        self.manager = manager
        self._stop = Event()
        self._thread: Thread | None = None
        self._messages_processed = 0
        self._last_error: str | None = None

    def start(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop.clear()
        thread = Thread(target=self._run, name="iec104-nats-consumer", daemon=True)
        thread.start()
        self._thread = thread

    def stop(self) -> None:
        self._stop.set()

    def is_alive(self) -> bool:
        """Tuketici IPLIGI ayakta mi (mesaj akiyor mu DEGIL).

        Bekci bunu kullanir: telemetri bu iplikten geliyor, iplik oldugunde
        asyncio dongusu neseyle donmeye devam eder ve disaridan her sey
        normal gorunur — ama SCADA'ya yayin tamamen durmustur. Trafikten
        BAGIMSIZ bir olcum oldugu icin sessiz gecede yanlis alarm uretmez.

        Durdurma istendiyse (kapanis) True doner: kapanirken bekcinin
        sureci oldurmesi anlamsiz olurdu.
        """
        if self._stop.is_set():
            return True
        thread = self._thread
        return thread is not None and thread.is_alive()

    @property
    def messages_processed(self) -> int:
        return self._messages_processed

    @property
    def last_error(self) -> str | None:
        return self._last_error

    def _run(self) -> None:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            loop.run_until_complete(self._consume())
        finally:
            loop.close()

    async def _consume(self) -> None:
        s = self.settings
        backoff = 2
        while not self._stop.is_set():
            nc = None
            try:
                nc = await nats.connect(
                    servers=[s.nats_url],
                    max_reconnect_attempts=-1,
                    reconnect_time_wait=2,
                    name="e1-iec104-outbound",
                    # None = TLS kapali (varsayilan). Bkz. _nats_tls_context.
                    tls=_nats_tls_context(),
                )
                js = nc.jetstream()

                async def _on_message(msg) -> None:  # noqa: ANN001
                    try:
                        payload = json.loads(msg.data.decode("utf-8"))
                        self._handle_payload(payload)
                        await msg.ack()
                        self._messages_processed += 1
                    except Exception as exc:  # noqa: BLE001
                        self._last_error = str(exc)
                        # Son denemede DLQ'ya tasi; aksi halde nak ile redeliver.
                        num_delivered = int(
                            getattr(getattr(msg, "metadata", None), "num_delivered", 1) or 1
                        )
                        is_terminal = num_delivered >= s.nats_worker_max_deliver
                        logger.warning(
                            "iec104_consumer_handler_error error=%s delivery=%d/%d terminal=%s",
                            exc,
                            num_delivered,
                            s.nats_worker_max_deliver,
                            is_terminal,
                        )
                        if is_terminal:
                            try:
                                orig_subject = getattr(msg, "subject", "unknown")
                                dlq_subject = (
                                    f"{s.nats_subject_dlq_prefix}.{orig_subject}"
                                )
                                dlq_headers = {
                                    "X-DLQ-Reason": "max_deliver_exceeded",
                                    "X-DLQ-Service": "iec104-outbound",
                                    "X-DLQ-Original-Subject": orig_subject,
                                    "X-DLQ-Error": str(exc)[:500],
                                    "X-DLQ-Delivery-Count": str(num_delivered),
                                }
                                await js.publish(
                                    dlq_subject, msg.data, headers=dlq_headers
                                )
                                logger.error(
                                    "iec104_consumer_dlq subject=%s error=%s",
                                    dlq_subject,
                                    exc,
                                )
                                await msg.ack()
                            except Exception:  # noqa: BLE001
                                logger.exception("iec104_consumer_dlq_publish_failed")
                                try:
                                    await msg.nak()
                                except Exception:  # noqa: BLE001
                                    pass
                        else:
                            try:
                                await msg.nak()
                            except Exception:  # noqa: BLE001
                                pass

                # Consumer parametreleri uretim icin sertlestirilmis (bkz.
                # diger NATS subscribe noktalari icin ayni mantik).
                from nats.js.api import ConsumerConfig, DeliverPolicy

                consumer_cfg = ConsumerConfig(
                    durable_name=s.nats_durable,
                    deliver_policy=DeliverPolicy.NEW,
                    ack_wait=60,
                    max_ack_pending=10000,
                    max_deliver=10,
                )
                sub = await js.subscribe(
                    subject=s.nats_subject,
                    durable=s.nats_durable,
                    cb=_on_message,
                    manual_ack=True,
                    config=consumer_cfg,
                )
                logger.info(
                    "iec104_consumer_running subject=%s durable=%s url=%s",
                    s.nats_subject,
                    s.nats_durable,
                    redact_url_credentials(s.nats_url),
                )
                backoff = 2
                while not self._stop.is_set():
                    await asyncio.sleep(1)
                await sub.unsubscribe()
            except Exception as exc:  # noqa: BLE001
                if self._stop.is_set():
                    break
                self._last_error = str(exc)
                logger.warning(
                    "iec104_consumer_reconnect error=%s backoff=%ds url=%s",
                    exc,
                    backoff,
                    redact_url_credentials(s.nats_url),
                )
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, 60)
            finally:
                if nc is not None:
                    try:
                        await nc.drain()
                    except Exception:  # noqa: BLE001
                        pass

    def _handle_payload(self, payload: dict) -> None:
        device_code = payload.get("device_code")
        signal_key = payload.get("signal_key")
        if not device_code or not signal_key:
            return
        value = payload.get("value")
        good = _is_good(payload.get("quality"))
        # source_timestamp ISO 8601 string olarak gelir (örn. "2026-05-06T...").
        # CP56Time2a frame'lerinde kullanilir; sinyal `with_timestamp=False`
        # ise yine de cache'lensin diye gonderiyoruz, encoder ihtiyac yoksa
        # ihmal eder.
        ts = _parse_iso_timestamp(payload.get("source_timestamp"))
        # Manager tum aktif server'lari dolasir; o target'ta bu point yoksa sessizce atlar.
        self.manager.update_point_threadsafe(
            device_code=str(device_code),
            signal_key=str(signal_key),
            value=value,
            good=good,
            timestamp=ts,
        )
