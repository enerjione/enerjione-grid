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
import json
import logging
import threading
from typing import Any

from app.core.config import settings

logger = logging.getLogger(__name__)

# nats-py opsiyonel: paket yoksa modul yine import edilebilmeli (testler ve
# NATS'siz dev kurulumlari icin). Kopru o durumda kapali kalir, yayin
# bellek-ici yola duser.
try:  # pragma: no cover - opsiyonel bagimlilik
    import nats as _nats  # type: ignore[import-not-found]

    _NATS_AVAILABLE = True
except ImportError:  # pragma: no cover
    _nats = None  # type: ignore[assignment]
    _NATS_AVAILABLE = False


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


class _WsNatsBridge:
    """Canli deger yayinini surecler arasi tasiyan NATS koprusu.

    NE ISE YARAR
      Bellek-ici yayin yalnizca TEK surecte calisir. Coklu surece gecince
      (API worker'lari + ayri tuketici container'i) tuketici baska bir
      surecte olur ve API surecindeki WS istemcilerine hicbir sey ulasmaz.
      Ariza SESSIZDIR: soket bagli gorunur, sadece deger akmaz. Bu kopru
      yayini NATS'a taşiyip HER surecin abone olmasini saglar.

    TASARIM KARARLARI
      * CORE NATS (JetStream degil): canli deger efemer; kalicilik/ack/disk
        maliyeti odemenin karsiligi yok.
      * QUEUE GROUP YOK: `subscribe(subject)` duz cagrilir. Queue group
        mesajlari abone surecler ARASINDA paylastirirdi ve her surec 1/N
        gorurdu — tam da kacinmak istedigimiz hata.
      * KENDI MESAJINI DA ALIR: yayinlayan surec de abone oldugu icin mesaj
        NATS uzerinden kendisine geri doner ve tek sefer dagitilir. Yerel
        kopya + uzak kopya ayrimi yapmadigimiz icin cift teslim riski yok.
      * Kendi asyncio loop'u ayri bir thread'de kosar (jetstream_bus ile ayni
        desen); publish herhangi bir thread'den cagrilabilir.
    """

    def __init__(self) -> None:
        self._loop: asyncio.AbstractEventLoop | None = None
        self._thread: threading.Thread | None = None
        self._nc: Any = None
        self._ready = threading.Event()
        self._stopping = threading.Event()
        self._on_message = None  # Callable[[dict], None]
        self.publish_failures = 0
        self.received_from_nats = 0
        # Ucusta olan publish task'lari. GUCLU REFERANS sart: aksi halde
        # asyncio task'i cop toplayiciya yem olabilir ve yayin sessizce
        # kaybolur (asyncio dokumantasyonunun acikca uyardigi tuzak).
        self._inflight: set[Any] = set()

    @property
    def is_ready(self) -> bool:
        """Kopru GERCEKTEN yayin yapabilir durumda mi?

        DIKKAT — burada `_ready` bayragina bakmak YETMEZ ve bir zamanlar
        oyleydi. `_ready` yalnizca ilk abonelikte set edilip `stop()`ta
        temizlenen TEK YONLU bir mandaldi; `_nc` de `max_reconnect_attempts=-1`
        yuzunden hicbir zaman None olmuyordu. Sonuc: NATS kopunca kopru
        "hazirim" demeye devam ediyor, `broadcast()` erken donuyor ve
        BELLEK-ICI YEDEK YOL HIC CALISMIYORDU — yani NATS'in kopmasi canli
        deger ekranini TEK SURECTE BILE karartiyordu. Bu, koprunun cozmek
        icin var oldugu sorundan daha kotu bir durumdu.

        Cozum: baglantinin GERCEK durumunu nats-py'nin kendisine soruyoruz.
        """
        nc = self._nc
        if not self._ready.is_set() or nc is None:
            return False
        # nats-py Client.is_connected — anlik baglanti durumu. Reconnect
        # sirasinda False doner, bagli degilken de False; ikisinde de yerel
        # yedek yola dusmemiz DOGRU davranistir.
        try:
            return bool(nc.is_connected)
        except Exception:  # noqa: BLE001
            return False

    def start(self, on_message) -> bool:  # noqa: ANN001
        """Koprüyü baslat. `on_message(payload)` NATS'tan gelen her mesaj icin
        cagirilir (kopru loop'unda). Basarisizsa False doner ve yayin
        bellek-ici yola duser."""
        if not settings.ws_fanout_nats_enabled:
            logger.info("ws_fanout_nats_disabled — yayin bellek-ici kalacak")
            return False
        if not _NATS_AVAILABLE:
            logger.warning(
                "ws_fanout_nats_unavailable reason=nats_py_missing — yayin "
                "bellek-ici kalacak. TEK surecte sorun degil; coklu surece "
                "gecilirse canli deger ekrani SESSIZCE bosalir."
            )
            return False
        if self._thread is not None and self._thread.is_alive():
            return self.is_ready

        self._on_message = on_message
        self._stopping.clear()
        self._ready.clear()
        self._thread = threading.Thread(
            target=self._run, name="ws-nats-bridge", daemon=True
        )
        self._thread.start()
        # Baglanti icin kisa bir pencere bekle; kurulamazsa yayin yine calisir
        # (bellek-ici), sadece surecler arasi dagitim olmaz.
        ok = self._ready.wait(timeout=float(settings.nats_connect_timeout_sec) + 2.0)
        if not ok:
            logger.warning(
                "ws_fanout_nats_connect_timeout — yayin simdilik bellek-ici; "
                "kopru arka planda yeniden baglanmayi surdurecek"
            )
        return ok

    def stop(self) -> None:
        self._stopping.set()
        loop = self._loop
        if loop is not None:
            try:
                loop.call_soon_threadsafe(loop.stop)
            except RuntimeError:
                pass
        if self._thread is not None:
            self._thread.join(timeout=3)
        self._thread = None
        self._loop = None
        self._nc = None
        self._ready.clear()

    def publish(self, payload: dict[str, Any]) -> bool:
        """Mesaji NATS'a birak. Basarili ise True.

        False donerse caller BELLEK-ICI dagitima duser — boylece NATS kopukken
        tek surecli kurulum calismaya devam eder.
        """
        if not self.is_ready or self._loop is None:
            return False
        try:
            data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        except (TypeError, ValueError):
            logger.debug("ws_fanout_serialize_failed", exc_info=True)
            return False
        try:
            # Fire-and-forget: cagiran thread (tuketici) NATS'i beklemesin.
            self._loop.call_soon_threadsafe(self._publish_soon, data)
            return True
        except RuntimeError:
            self.publish_failures += 1
            return False

    def _publish_soon(self, data: bytes) -> None:
        nc = self._nc
        if nc is None:
            return
        # publish() bir coroutine; loop icindeyiz, task olarak birakiyoruz.
        #
        # IKI TUZAK:
        #  1. Hata task'in ICINDE olusur ve asagidaki except'e DUSMEZ. Bu
        #     yuzden `publish_failures` sayaci bir zamanlar OLU idi (sonsuza
        #     kadar 0) ve "yayin calisiyor mu" sorusuna yalan soyluyordu.
        #     Cozum: done-callback ile sonucu okuyoruz.
        #  2. ensure_future'in donusune GUCLU REFERANS tutulmazsa task
        #     cop toplayiciya yem olabilir ve yayin sessizce kaybolur.
        try:
            task = asyncio.ensure_future(nc.publish(settings.ws_fanout_subject, data))
        except Exception:  # noqa: BLE001
            self.publish_failures += 1
            return
        self._inflight.add(task)
        task.add_done_callback(self._on_publish_done)

    def _on_publish_done(self, task: "asyncio.Task[Any]") -> None:
        self._inflight.discard(task)
        if task.cancelled():
            return
        exc = task.exception()
        if exc is not None:
            self.publish_failures += 1
            logger.debug("ws_fanout_publish_failed error=%s", exc)

    def _run(self) -> None:
        loop = asyncio.new_event_loop()
        self._loop = loop
        asyncio.set_event_loop(loop)
        try:
            loop.run_until_complete(self._connect_and_subscribe())
            if not self._stopping.is_set():
                loop.run_forever()
        except Exception:  # noqa: BLE001
            logger.exception("ws_fanout_bridge_crashed")
        finally:
            try:
                if self._nc is not None:
                    loop.run_until_complete(self._nc.drain())
            except Exception:  # noqa: BLE001
                logger.debug("ws_fanout_drain_error", exc_info=True)
            loop.close()

    async def _connect_and_subscribe(self) -> None:
        """NATS'a baglan ve abone ol; basarisizsa GERI CEKILEREK TEKRAR DENE.

        Eskiden ilk deneme basarisiz olunca kopru bir daha HIC denemiyordu
        (log metni bunu itiraf ediyor, ama `start()` icindeki mesaj tam
        tersini soyluyordu — celiskili ve yanlisti). Sonucu: backend NATS'tan
        once acilirsa kopru kalici olarak olu kalir; tek surecte yayin
        bellek-ici devam ettigi icin kimse fark etmez, ama coklu surece
        gecildiginde canli deger ekrani SESSIZCE bosalir.

        `nats.connect` bir kez KURULDUKTAN sonra kopmalari kendi icinde
        yonetir (max_reconnect_attempts=-1). Buradaki dongu yalnizca ILK
        baglantiyi kurmak icindir.
        """
        backoff = 2
        while not self._stopping.is_set():
            try:
                from app.core.nats_tls import nats_tls_context

                self._nc = await _nats.connect(  # type: ignore[union-attr]
                    servers=[settings.nats_url],
                    connect_timeout=settings.nats_connect_timeout_sec,
                    max_reconnect_attempts=-1,  # kurulduktan sonra surekli dene
                    reconnect_time_wait=2,
                    name="e1-backend-ws-fanout",
                    tls=nats_tls_context(),  # None = TLS kapali (varsayilan)
                )
                break
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "ws_fanout_nats_connect_failed error=%s backoff=%ds — yayin "
                    "simdilik bellek-ici; kopru denemeye DEVAM edecek",
                    exc,
                    backoff,
                )
                try:
                    await asyncio.sleep(backoff)
                except asyncio.CancelledError:
                    return
                backoff = min(backoff * 2, 60)
        if self._nc is None or self._stopping.is_set():
            return

        async def _handler(msg) -> None:  # noqa: ANN001
            self.received_from_nats += 1
            cb = self._on_message
            if cb is None:
                return
            try:
                payload = json.loads(msg.data.decode("utf-8"))
            except Exception:  # noqa: BLE001
                logger.debug("ws_fanout_decode_failed", exc_info=True)
                return
            try:
                cb(payload)
            except Exception:  # noqa: BLE001
                logger.debug("ws_fanout_dispatch_failed", exc_info=True)

        # DUZ SUBSCRIBE — queue group VERILMEZ. Verilseydi mesajlar abone
        # surecler arasinda paylastirilir ve her surec 1/N gorurdu.
        await self._nc.subscribe(settings.ws_fanout_subject, cb=_handler)
        # FLUSH SART — `subscribe()` donmesi aboneligin SUNUCUYA ULASTIGI
        # anlamina GELMEZ. nats-py SUB cercevesini tampona yazip hemen doner.
        # Flush olmadan, hazir isaretini verdikten hemen sonra yayinlanan
        # mesajlar kendi abonemize DUSMEDEN once sunucuya varir ve KAYBOLUR.
        #
        # Bu yalnizca teorik degil: koprunun kendi testi bu yuzden ~3 kosumda
        # 1 kirmizi oluyordu. Uretimde pencere dar ama gercek — surec acilir
        # acilmaz gelen ilk telemetri mesajlari sessizce dusebilirdi.
        try:
            await self._nc.flush(timeout=2)
        except Exception:  # noqa: BLE001
            # Flush basarisiz olsa bile abonelik muhtemelen kurulmustur;
            # hazir isaretini vermeyi engellemeyelim.
            logger.debug("ws_fanout_flush_failed", exc_info=True)
        self._ready.set()
        logger.info(
            "ws_fanout_bridge_ready subject=%s url=%s",
            settings.ws_fanout_subject,
            settings.nats_url,
        )


bridge = _WsNatsBridge()


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
        """Yayin girisi — telemetry_consumer buradan cagirir.

        AKIS:
          NATS koprusu hazirsa mesaj oraya birakilir ve HER backend sureci
          (bu surec dahil) abone oldugu icin mesaji alip kendi yerel WS
          istemcilerine dagitir. Yayinlayan surec mesaji NATS uzerinden geri
          aldigi icin BURADA yerel dagitim YAPILMAZ — aksi halde cift teslim
          olurdu.

          Kopru hazir DEGILSE (NATS kopuk, nats-py yok, ya da ayar kapali)
          dogrudan yerel dagitima duseriz. Boylece tek surecli kurulum
          NATS olmadan da calisir.
        """
        if bridge.publish(payload):
            return
        self._deliver_local(payload)

    def _deliver_local(self, payload: dict[str, Any]) -> None:
        """Bu SURECTEKI bagli WS subscriber'lara payload push eder.

        Iki yerden cagirilir: NATS koprusunden gelen mesajlar icin (normal
        yol) ve kopru yokken dogrudan `broadcast`ten (yedek yol).

        Thread-safe: cagiran thread ne olursa olsun her subscriber'in kendi
        async loop'una `call_soon_threadsafe` ile enqueue eder. Slow
        consumer'lar drop edilir (queue dolu).
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
            # Fan-out koprusu: coklu surecte canli deger akisinin saglik
            # gostergesi. `bridge_ready=False` iken birden fazla surec varsa
            # WS istemcileri veri ALAMAZ (sessiz ariza).
            "bridge_ready": bridge.is_ready,
            "bridge_publish_failures": bridge.publish_failures,
            "bridge_received_from_nats": bridge.received_from_nats,
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
