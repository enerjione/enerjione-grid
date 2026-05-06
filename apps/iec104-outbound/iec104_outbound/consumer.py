"""RabbitMQ tuketicisi - tag-engine'den gelen `telemetry.received` mesajlarini
IEC 104 server manager'a koprü ile aktarir.

Tasarim: pika BlockingConnection kendi thread'inde calisir. asyncio loop
ana thread'de calisir; consumer her mesaj icin `manager.update_point_threadsafe`
cagirisi yaparak loop'a tasi. Boylece IEC 104 server async dunyasinda kalir,
mesaj IO ise senkron pika ile yapilir (kucuk ve kararli desen).

Reconnect: baglanti koparsa exponential backoff yok; sabit 3sn ile yeniden
dener. Tag engine'inkiyle ayni desen.
"""

from __future__ import annotations

import json
import logging
import time
from datetime import datetime
from threading import Event, Thread

import pika

from iec104_outbound.config import Settings
from iec104_outbound.server import IEC104ServerManager

logger = logging.getLogger(__name__)


_GOOD_QUALITIES = {"good", "ok", ""}


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
    """Tek thread, BlockingConnection. `start()` daemon thread'i baslatir."""

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
        thread = Thread(target=self._run, name="iec104-rabbit-consumer", daemon=True)
        thread.start()
        self._thread = thread

    def stop(self) -> None:
        self._stop.set()

    @property
    def messages_processed(self) -> int:
        return self._messages_processed

    @property
    def last_error(self) -> str | None:
        return self._last_error

    def _run(self) -> None:
        s = self.settings
        while not self._stop.is_set():
            connection = None
            try:
                connection = pika.BlockingConnection(pika.URLParameters(s.rabbit_url))
                channel = connection.channel()
                channel.exchange_declare(
                    exchange=s.exchange, exchange_type="topic", durable=True,
                )
                channel.exchange_declare(
                    exchange=s.dlx_exchange, exchange_type="topic", durable=True,
                )
                channel.queue_declare(
                    queue=s.queue_name,
                    durable=True,
                    arguments={
                        "x-dead-letter-exchange": s.dlx_exchange,
                        "x-dead-letter-routing-key": f"{s.incoming_topic}.dead",
                    },
                )
                channel.queue_bind(
                    exchange=s.exchange, queue=s.queue_name,
                    routing_key=s.incoming_topic,
                )
                channel.basic_qos(prefetch_count=s.prefetch_count)
                channel.basic_consume(
                    queue=s.queue_name, on_message_callback=self._on_message,
                )
                logger.info(
                    "iec104_consumer_running queue=%s topic=%s prefetch=%d",
                    s.queue_name, s.incoming_topic, s.prefetch_count,
                )
                # start_consuming bloklar; stop edilince exception ile cikariz.
                while not self._stop.is_set():
                    connection.process_data_events(time_limit=1.0)
            except Exception as exc:
                self._last_error = str(exc)
                logger.warning("iec104_consumer_reconnect error=%s", exc)
                time.sleep(3)
            finally:
                if connection is not None:
                    try:
                        connection.close()
                    except Exception:
                        pass

    def _on_message(self, ch, method, properties, body) -> None:  # noqa: ANN001
        _ = properties
        try:
            payload = json.loads(body.decode("utf-8"))
        except Exception:
            ch.basic_nack(delivery_tag=method.delivery_tag, requeue=False)
            return
        try:
            self._handle_payload(payload)
            ch.basic_ack(delivery_tag=method.delivery_tag)
            self._messages_processed += 1
        except Exception as exc:
            self._last_error = str(exc)
            logger.exception("iec104_consumer_handler_error")
            ch.basic_nack(delivery_tag=method.delivery_tag, requeue=False)

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
