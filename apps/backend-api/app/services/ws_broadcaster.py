"""WebSocket broadcaster: telemetri mesajlarini bagli WS client'lara push eder.

Mimari:
  RabbitMQ telemetry.raw_received
    -> telemetry_consumer._persist_message (DB'ye yaz)
       -> ws_broadcaster.broadcast(payload)        <-- buradan
          -> tum bagli /ws/live-values clients

Tasarim:
  * In-memory subscriber liste; her WS connection bir asyncio.Queue.
  * Pub/sub thread-safe (broadcast sync thread'den, consume async).
  * Queue dolma korumasi: max 200 pending; asilirsa client drop edilir
    (slow consumer cycle'i bloke etmesin). Frontend reconnect ederse yeni
    ackish state alir (`/signals/live` endpoint'inden tam snapshot).
  * Client filter: subscriber kendi `device_codes` filter ile sadece ilgili
    cihazlari alir. Default: tum cihazlar.

Frontend kullanim:
  ws://backend/api/v1/ws/live-values
  -> JSON event akisi: {device_code, signal_key, value, value_string,
                         quality, source_timestamp, ...}
"""
from __future__ import annotations

import asyncio
import logging
import threading
from typing import Any

logger = logging.getLogger(__name__)


# Bir client'in queue'sunda bekleyebilecek max mesaj. Asilirsa client drop
# edilir (broadcaster cycle'i blocklamasin). Frontend WS reconnect ile
# tam snapshot alir.
_MAX_PENDING_PER_CLIENT = 200


class _Subscriber:
    """Tek bir WS client için pub/sub kayit. Queue ile mesaj akisi."""

    def __init__(
        self,
        loop: asyncio.AbstractEventLoop,
        *,
        device_codes: set[str] | None = None,
    ) -> None:
        self.loop = loop
        # asyncio.Queue: thread-safe degil ama call_soon_threadsafe ile guvenle
        # erisilebilir
        self.queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue(maxsize=_MAX_PENDING_PER_CLIENT)
        # Filter: None -> tum cihazlar, set -> sadece bu kodlar
        self.device_codes: set[str] | None = device_codes
        self.dropped_messages: int = 0  # slow consumer kontrol
        self.alive: bool = True


class TelemetryWsBroadcaster:
    """Thread-safe broadcaster. publish_to_subscribers sync thread'den
    cagirilir; her subscriber kendi async queue'suna mesaj enqueue olur."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._subscribers: list[_Subscriber] = []

    def subscribe(
        self,
        loop: asyncio.AbstractEventLoop,
        *,
        device_codes: set[str] | None = None,
    ) -> _Subscriber:
        sub = _Subscriber(loop, device_codes=device_codes)
        with self._lock:
            self._subscribers.append(sub)
        logger.info(
            "ws_subscriber_added total=%d filter_devices=%s",
            len(self._subscribers),
            "all" if device_codes is None else len(device_codes),
        )
        return sub

    def unsubscribe(self, sub: _Subscriber) -> None:
        sub.alive = False
        with self._lock:
            try:
                self._subscribers.remove(sub)
            except ValueError:
                pass
        logger.info("ws_subscriber_removed remaining=%d", len(self._subscribers))

    def broadcast(self, payload: dict[str, Any]) -> None:
        """Bagli tum WS subscriber'lara payload push eder.

        Thread-safe: telemetry_consumer'in sync thread'inden cagirilir;
        her subscriber'in async loop'una `call_soon_threadsafe` ile enqueue
        eder. Slow consumer'lar drop edilir (queue dolu).
        """
        with self._lock:
            subscribers = list(self._subscribers)

        if not subscribers:
            return

        device_code = payload.get("device_code")
        for sub in subscribers:
            if not sub.alive:
                continue
            # Filter: sub kendi cihaz listesini istemisse onu uygula
            if sub.device_codes is not None and device_code not in sub.device_codes:
                continue
            try:
                sub.loop.call_soon_threadsafe(self._enqueue_safely, sub, payload)
            except RuntimeError:
                # Loop kapali — subscriber temizlenmeli
                sub.alive = False

    @staticmethod
    def _enqueue_safely(sub: _Subscriber, payload: dict[str, Any]) -> None:
        """Async loop'da queue'ya put. Queue dolu ise eski mesaji at."""
        if not sub.alive:
            return
        try:
            sub.queue.put_nowait(payload)
        except asyncio.QueueFull:
            # Slow consumer: en eski mesaji at, yenisini ekle. Frontend
            # reconnect/snapshot fetch ile telafi eder.
            sub.dropped_messages += 1
            try:
                _ = sub.queue.get_nowait()
            except asyncio.QueueEmpty:
                pass
            try:
                sub.queue.put_nowait(payload)
            except asyncio.QueueFull:
                pass

    def stats(self) -> dict[str, Any]:
        with self._lock:
            return {
                "active_subscribers": len(self._subscribers),
                "total_dropped_messages": sum(s.dropped_messages for s in self._subscribers),
            }


# Singleton — main.py startup'inda referans paylasilir
broadcaster = TelemetryWsBroadcaster()
