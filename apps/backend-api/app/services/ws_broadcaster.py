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
        # Toplam alinan mesaj (dropped dahil) — drop oranini hesaplamak icin.
        self.received_messages: int = 0
        # Son uyari log epoch — saniyede bir uyari (logging flood koruma).
        self.last_warn_at: float = 0.0
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
        """Async loop'da queue'ya put. Queue dolu ise eski mesaji at.

        Slow-consumer telemetrisi:
          - dropped_messages: client basina toplam drop sayisi (monotonik).
          - received_messages: toplam denenen put (drop dahil).
          - Drop orani %5'i asarsa WARNING log (1sn rate-limit) — operator
            dashboard / log aggregator alarm kurabilir.
        """
        if not sub.alive:
            return
        sub.received_messages += 1
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
            # Drop oranini takip et — esik asilirsa uyari (rate-limited).
            import time as _t
            now = _t.monotonic()
            if (
                sub.received_messages >= 200
                and sub.dropped_messages * 20 >= sub.received_messages  # >%5
                and (now - sub.last_warn_at) >= 1.0
            ):
                sub.last_warn_at = now
                logger.warning(
                    "ws_subscriber_slow_consumer dropped=%d received=%d ratio=%.1f%% "
                    "filter_devices=%s — client process'i CPU/IO bottleneck'te "
                    "veya internet zayif; frontend bunu UI'da gosterip "
                    "reconnect'te /signals/live ile telafi etmeli.",
                    sub.dropped_messages,
                    sub.received_messages,
                    (sub.dropped_messages / max(1, sub.received_messages)) * 100.0,
                    "all" if sub.device_codes is None else len(sub.device_codes),
                )

    def stats(self) -> dict[str, Any]:
        """Toplam ve client basina detayli istatistik. Health endpoint icin."""
        with self._lock:
            subs = list(self._subscribers)
        total_received = sum(s.received_messages for s in subs)
        total_dropped = sum(s.dropped_messages for s in subs)
        return {
            "active_subscribers": len(subs),
            "total_received_messages": total_received,
            "total_dropped_messages": total_dropped,
            "drop_ratio_percent": round(
                (total_dropped / total_received) * 100.0 if total_received else 0.0,
                2,
            ),
            # Per-subscriber summary (top 5 by drop count) — operator hangi
            # client'in slow oldugunu görsün.
            "top_droppers": [
                {
                    "dropped": s.dropped_messages,
                    "received": s.received_messages,
                    "filter_devices": (
                        "all" if s.device_codes is None else len(s.device_codes)
                    ),
                }
                for s in sorted(subs, key=lambda x: x.dropped_messages, reverse=True)[:5]
                if s.dropped_messages > 0
            ],
        }


# Singleton — main.py startup'inda referans paylasilir
broadcaster = TelemetryWsBroadcaster()
