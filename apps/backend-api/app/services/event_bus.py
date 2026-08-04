import json
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Protocol

from app.core.config import settings

try:
    import pika
except ImportError:  # pragma: no cover - optional dependency
    pika = None


EventHandler = Callable[[dict[str, Any]], None]


@dataclass
class ConsumerConfig:
    queue_name: str
    durable: bool = True
    prefetch_count: int = 20
    dead_letter_exchange: str = ""
    dead_letter_routing_key: str = ""


#: `publish_events` girdisi: (topic, payload, message_id)
BatchItem = tuple[str, dict[str, Any], str]


class EventBus(Protocol):
    def publish_event(self, topic: str, payload: dict[str, Any], *, message_id: str = "") -> None: ...

    def publish_events(self, items: list[BatchItem]) -> list[Exception | None]: ...

    def consume_event(self, topic: str, handler: EventHandler, *, config: ConsumerConfig | None = None) -> None: ...


class InProcessEventBus:
    def __init__(self) -> None:
        self._subscribers: dict[str, list[EventHandler]] = {}

    def publish_event(self, topic: str, payload: dict[str, Any], *, message_id: str = "") -> None:
        _ = message_id
        for handler in self._subscribers.get(topic, []):
            handler(payload)

    def publish_events(self, items: list[BatchItem]) -> list[Exception | None]:
        sonuc: list[Exception | None] = []
        for topic, payload, message_id in items:
            try:
                self.publish_event(topic, payload, message_id=message_id)
            except Exception as exc:  # noqa: BLE001
                sonuc.append(exc)
            else:
                sonuc.append(None)
        return sonuc

    def consume_event(self, topic: str, handler: EventHandler, *, config: ConsumerConfig | None = None) -> None:
        _ = config
        handlers = self._subscribers.setdefault(topic, [])
        handlers.append(handler)


class RabbitMqEventBus:
    def __init__(self, url: str, exchange: str) -> None:
        if pika is None:
            raise RuntimeError("RabbitMQ backend selected but 'pika' dependency is missing.")
        self._url = url
        self._exchange = exchange
        self._subscribers: dict[str, list[EventHandler]] = {}
        # Persistent connection — her event'te TCP handshake yapilirsa alarm
        # firtinasinda RabbitMQ TIME_WAIT'le doluyor (ip_local_port_range
        # tukenir). Tek connection + channel cache; thread-safe lock altinda.
        self._conn_lock = threading.Lock()
        self._connection: "pika.BlockingConnection | None" = None
        self._channel: "Any | None" = None

    def publish_event(self, topic: str, payload: dict[str, Any], *, message_id: str = "") -> None:
        # Topic-bazli routing:
        #   - telemetry.*  -> NATS JetStream (yuksek hacim, replay destekli)
        #   - diger (alarm.*, vs.) -> RabbitMQ (DLQ/retry semantik, dusuk hacim)
        # Boylece tek bir publish_event arayuzu uzerinden iki broker'a route ediliyor;
        # caller kod (alarm-service, outbox_service, vs.) topic-broker eslemesini
        # bilmek zorunda kalmaz.
        if topic.startswith("telemetry."):
            self._publish_to_jetstream(topic, payload, message_id=message_id)
        else:
            self._publish_to_rabbitmq(topic, payload, message_id=message_id)

        # In-process subscriber'lar (eski davranis korunur — InProcessEventBus
        # taklit modu icin gerekli).
        for handler in self._subscribers.get(topic, []):
            handler(payload)

    def publish_events(self, items: list[BatchItem]) -> list[Exception | None]:
        """Bir partiyi TEK cagriyla yayinlar; SONUC HER MESAJ ICIN AYRI doner.

        Donen liste girdiyle ayni sirada: basari icin None, aksi halde o
        mesajin hatasi. Boylece `flush_outbox` satir bazli hata yalitimini
        (dead-letter sayaci) toplu yayinda da surdurebiliyor.

        Yonlendirme `publish_event` ile AYNI: telemetry.* -> JetStream (toplu,
        paralel ack), digerleri -> RabbitMQ. Alarm vb. dusuk hacimli oldugu
        icin tek tek gonderilir; caller topic-broker eslemesini bilmez.
        """
        sonuc: list[Exception | None] = [None] * len(items)

        tel_idx = [i for i, (topic, _p, _m) in enumerate(items) if topic.startswith("telemetry.")]
        if tel_idx:
            from app.services.jetstream_bus import get_bus

            bus = get_bus()
            try:
                if bus is None:
                    raise RuntimeError("jetstream bus unavailable (toplu yayin)")
                hatalar = bus.publish_events([items[i] for i in tel_idx])
            except Exception as exc:  # noqa: BLE001
                # Bus yok / loop'a hic gecilemedi: ariza SISTEMIK, partinin
                # tamami basarisiz. flush_outbox bunu (hicbiri gecmedi)
                # gorup sayac artirmadan caller'a yayar.
                for i in tel_idx:
                    sonuc[i] = exc
            else:
                for i, hata in zip(tel_idx, hatalar, strict=True):
                    sonuc[i] = hata

        for i, (topic, payload, message_id) in enumerate(items):
            if topic.startswith("telemetry."):
                continue
            try:
                self._publish_to_rabbitmq(topic, payload, message_id=message_id)
            except Exception as exc:  # noqa: BLE001
                sonuc[i] = exc

        # In-process subscriber'lar — yalnizca gercekten yayinlanan mesajlar icin.
        for i, (topic, payload, _m) in enumerate(items):
            if sonuc[i] is None:
                for handler in self._subscribers.get(topic, []):
                    handler(payload)
        return sonuc

    def _ensure_channel(self) -> "Any":
        """Persistent RabbitMQ channel. Connection dustugunde otomatik reconnect.

        Caller `_conn_lock` altinda olmali. Returns channel.
        """
        # Eski connection broken ise sifirla
        if self._connection is not None:
            try:
                if self._connection.is_closed:
                    self._connection = None
                    self._channel = None
            except Exception:  # noqa: BLE001
                self._connection = None
                self._channel = None
        if self._connection is None:
            params = pika.URLParameters(self._url)
            # Heartbeat + connection timeout — uzun idle pause sonrasi
            # cevapsiz baglantilari otomatik kopart.
            params.heartbeat = 30
            params.blocked_connection_timeout = 10
            self._connection = pika.BlockingConnection(params)
            self._channel = None
        if self._channel is None or self._channel.is_closed:
            self._channel = self._connection.channel()
            self._channel.exchange_declare(
                exchange=self._exchange, exchange_type="topic", durable=True
            )
        return self._channel

    def _publish_to_rabbitmq(
        self, topic: str, payload: dict[str, Any], *, message_id: str
    ) -> None:
        """Alarm + diger event'ler icin RabbitMQ. Persistent connection."""
        properties = pika.BasicProperties(
            content_type="application/json",
            delivery_mode=2,
            message_id=message_id or payload.get("message_id", ""),
        )
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        with self._conn_lock:
            try:
                channel = self._ensure_channel()
                channel.basic_publish(
                    exchange=self._exchange,
                    routing_key=topic,
                    body=body,
                    properties=properties,
                )
            except Exception:
                # Connection drop — sifirla, bir sonraki call yeniden kursun.
                try:
                    if self._connection is not None:
                        self._connection.close()
                except Exception:  # noqa: BLE001
                    pass
                self._connection = None
                self._channel = None
                raise

    def _publish_to_jetstream(
        self, topic: str, payload: dict[str, Any], *, message_id: str
    ) -> None:
        """Telemetry akisi icin NATS JetStream. Yuksek throughput, replay'li.

        Hata MUTLAKA caller'a yayilir. `flush_outbox` ancak publish basariliysa
        row'u published=True yapar; exception olursa transaction rollback olur,
        published=False kalir ve worker tekrar dener. Onceki kod hatayi yutup
        satiri basarili isaretliyordu -> NATS kesintisinde sessiz telemetri
        kaybi + donmus device.last_update_at.
        """
        from app.services.jetstream_bus import get_bus

        bus = get_bus()
        if bus is None:
            raise RuntimeError(
                f"jetstream bus unavailable topic={topic} message_id={message_id}"
            )
        bus.publish_event(topic, payload, message_id=message_id)

    def consume_event(self, topic: str, handler: EventHandler, *, config: ConsumerConfig | None = None) -> None:
        handlers = self._subscribers.setdefault(topic, [])
        handlers.append(handler)
        consumer_cfg = config or ConsumerConfig(
            queue_name=f"{settings.service_name}.{topic}".replace(".", "_"),
            prefetch_count=settings.rabbitmq_prefetch_count,
            dead_letter_exchange=settings.rabbitmq_dlx_exchange,
            dead_letter_routing_key=f"{topic}.dead",
        )

        def _consume_loop() -> None:
            while True:
                try:
                    connection = pika.BlockingConnection(pika.URLParameters(self._url))
                    channel = connection.channel()
                    channel.exchange_declare(exchange=self._exchange, exchange_type="topic", durable=True)
                    if consumer_cfg.dead_letter_exchange:
                        channel.exchange_declare(
                            exchange=consumer_cfg.dead_letter_exchange,
                            exchange_type="topic",
                            durable=True,
                        )

                    queue_arguments = {}
                    if consumer_cfg.dead_letter_exchange:
                        queue_arguments["x-dead-letter-exchange"] = consumer_cfg.dead_letter_exchange
                    if consumer_cfg.dead_letter_routing_key:
                        queue_arguments["x-dead-letter-routing-key"] = consumer_cfg.dead_letter_routing_key

                    channel.queue_declare(
                        queue=consumer_cfg.queue_name,
                        durable=consumer_cfg.durable,
                        arguments=queue_arguments or None,
                    )
                    channel.queue_bind(exchange=self._exchange, queue=consumer_cfg.queue_name, routing_key=topic)
                    if consumer_cfg.dead_letter_exchange:
                        dlq_name = f"{consumer_cfg.queue_name}.dlq"
                        channel.queue_declare(queue=dlq_name, durable=True)
                        channel.queue_bind(
                            exchange=consumer_cfg.dead_letter_exchange,
                            queue=dlq_name,
                            routing_key=consumer_cfg.dead_letter_routing_key or f"{topic}.dead",
                        )

                    def _on_message(ch, method, properties, body) -> None:  # noqa: ANN001
                        _ = properties
                        try:
                            payload = json.loads(body.decode("utf-8"))
                            handler(payload)
                        except Exception:
                            ch.basic_nack(delivery_tag=method.delivery_tag, requeue=False)
                            return
                        ch.basic_ack(delivery_tag=method.delivery_tag)

                    channel.basic_qos(prefetch_count=max(1, consumer_cfg.prefetch_count))
                    channel.basic_consume(queue=consumer_cfg.queue_name, on_message_callback=_on_message)
                    channel.start_consuming()
                except Exception:
                    try:
                        connection.close()
                    except Exception:
                        pass
                    time.sleep(2)

        thread = threading.Thread(target=_consume_loop, daemon=True)
        thread.start()


def build_event_bus() -> EventBus:
    if settings.event_bus_backend.lower() == "rabbitmq":
        return RabbitMqEventBus(settings.rabbitmq_url, settings.rabbitmq_exchange)
    return InProcessEventBus()


event_bus = build_event_bus()
