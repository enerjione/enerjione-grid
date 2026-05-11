import json
import os
import time
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, HTTPServer
from threading import Event, Lock, Thread
from uuid import uuid4

import pika
import requests

from alarm_service.rules import AlarmRuleCache, evaluate_composite, evaluate_rule

RABBIT_URL = os.getenv("RABBITMQ_URL", "amqp://guest:guest@localhost:5672/")
EXCHANGE = os.getenv("RABBITMQ_EXCHANGE", "hsl.events")
INCOMING_TOPIC = os.getenv("ALARM_INCOMING_TOPIC", "telemetry.received")
OUTGOING_TOPIC = os.getenv("ALARM_OUTGOING_TOPIC", "alarm.created")
QUEUE_NAME = os.getenv("ALARM_QUEUE", "hsl.alarm_service.telemetry")
DLX_EXCHANGE = os.getenv("RABBITMQ_DLX_EXCHANGE", "hsl.events.dlx")
HEALTH_HOST = os.getenv("WORKER_HEALTH_HOST", "127.0.0.1")
HEALTH_PORT = int(os.getenv("WORKER_HEALTH_PORT", "8012"))
BACKEND_INTERNAL_URL = os.getenv("BACKEND_INTERNAL_ALARM_URL", "http://127.0.0.1:8000/api/v1/internal/alarms")
BACKEND_INTERNAL_CLEAR_URL = os.getenv(
    "BACKEND_INTERNAL_ALARM_CLEAR_URL",
    "http://127.0.0.1:8000/api/v1/internal/alarms/clear",
)
BACKEND_API_BASE = os.getenv("BACKEND_API_BASE", "http://127.0.0.1:8000/api/v1")
INTERNAL_SERVICE_TOKEN = os.getenv("INTERNAL_SERVICE_TOKEN", "change-me-internal-token")
RULES_REFRESH_SEC = int(os.getenv("ALARM_RULES_REFRESH_SEC", "10"))


# Kural (rule_id, device_code) bazli durum takibi: aktiflik + debounce buffer + ilk gorulen zaman
class _RuleState:
    def __init__(self) -> None:
        self._lock = Lock()
        self._active: dict[tuple[int, str, str], bool] = {}
        self._pending_since: dict[tuple[int, str, str], float] = {}
        # Son backend clear cagrisinin zaman damgasi (rate-limit icin):
        # in-memory state drift senaryosunda her tick'te clear cagrisi
        # atmamak icin (rule, device, signal) key'i basina min aralik tutariz.
        self._last_clear_sent: dict[tuple[int, str, str], float] = {}

    def get_active(self, key: tuple[int, str, str]) -> bool:
        with self._lock:
            return self._active.get(key, False)

    def set_active(self, key: tuple[int, str, str], active: bool) -> None:
        with self._lock:
            self._active[key] = active
            if not active:
                self._pending_since.pop(key, None)

    def pending_since(self, key: tuple[int, str, str]) -> float | None:
        with self._lock:
            return self._pending_since.get(key)

    def mark_pending(self, key: tuple[int, str, str], now: float) -> None:
        with self._lock:
            if key not in self._pending_since:
                self._pending_since[key] = now

    def clear_pending(self, key: tuple[int, str, str]) -> None:
        with self._lock:
            self._pending_since.pop(key, None)

    def should_send_clear(self, key: tuple[int, str, str], now: float, min_interval_sec: float) -> bool:
        """Drift-proof clear gonderme rate-limit'i.

        prev_active=False olsa bile periyodik olarak backend'e clear gondeririz
        (DB'de hala acik alarm kalmis olabilir — alarm-service restart edildi
        veya state drift oldu). Min aralik kullaniyoruz ki her tick'te
        flood etmesin."""
        with self._lock:
            last = self._last_clear_sent.get(key)
            return last is None or (now - last) >= min_interval_sec

    def mark_clear_sent(self, key: tuple[int, str, str], now: float) -> None:
        with self._lock:
            self._last_clear_sent[key] = now


_STATE = _RuleState()


# Kalite-bazli alarmlar (haberlesme / offline / invalid) icin device-bazli state.
# Aktifken yeni kalite alarmi uretilmez (dedup); good'a donunce backend'e clear gider.
class _QualityState:
    def __init__(self) -> None:
        self._lock = Lock()
        self._bad: dict[str, bool] = {}

    def is_bad(self, device_code: str) -> bool:
        with self._lock:
            return self._bad.get(device_code, False)

    def mark_bad(self, device_code: str) -> None:
        with self._lock:
            self._bad[device_code] = True

    def mark_good(self, device_code: str) -> bool:
        """True doner ise daha once 'bad' idi - clear gonderilmesi gerekir."""
        with self._lock:
            was_bad = self._bad.get(device_code, False)
            if was_bad:
                self._bad[device_code] = False
            return was_bad


_QUALITY_STATE = _QualityState()


# Composite kurallar icin son sinyal degerlerinin in-memory cache'i. Bir
# (signal_key, device_code) pair'i her gelen telemetride guncellenir; composite
# evaluator diger terimlerin son degerlerini buradan okur. Yalnizca alarm-service
# process'inde tutulur — restart sonrasi tum hucreler bos baslar, yeni telemetri
# geldikce dolar. Yarim degerlendirmeyi onlemek icin evaluator None'da kurali atlar.
class _LiveValueCache:
    def __init__(self) -> None:
        self._lock = Lock()
        # (signal_key, device_code) -> (value, monotonic_ts)
        self._values: dict[tuple[str, str], tuple[float, float]] = {}

    def put(self, signal_key: str, device_code: str, value: float, now: float) -> None:
        with self._lock:
            self._values[(signal_key, device_code)] = (value, now)

    def get(self, signal_key: str, device_code: str, *, max_age_sec: float = 600.0, now: float | None = None) -> float | None:
        """Belirtilen sinyalin/cihazin son degerini doner. max_age_sec'ten eski
        degerler 'taze degil' kabul edilip None doner — kural eski veriyle
        yanlislikla tetiklenmesin."""
        if now is None:
            now = time.monotonic()
        with self._lock:
            entry = self._values.get((signal_key, device_code))
        if entry is None:
            return None
        value, ts = entry
        if (now - ts) > max_age_sec:
            return None
        return value


_LIVE = _LiveValueCache()

_CACHE = AlarmRuleCache(
    base_url=BACKEND_API_BASE,
    service_token=INTERNAL_SERVICE_TOKEN,
    refresh_sec=RULES_REFRESH_SEC,
)


class _HealthHandler(BaseHTTPRequestHandler):
    def do_GET(self):  # noqa: N802
        if self.path == "/health":
            body = {
                "status": "ok" if _CACHE.is_ready() else "starting",
                "service": "alarm-service",
                "rules_ready": _CACHE.is_ready(),
            }
            payload = json.dumps(body, ensure_ascii=False).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)
            return
        self.send_response(404)
        self.end_headers()

    def log_message(self, format, *args):  # noqa: A003
        _ = format, args
        return


def _start_health_server() -> None:
    server = HTTPServer((HEALTH_HOST, HEALTH_PORT), _HealthHandler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()


def _rules_refresh_loop(stop_event: Event) -> None:
    while not stop_event.is_set():
        _CACHE.refresh()
        stop_event.wait(timeout=max(5, RULES_REFRESH_SEC))


def _quality_is_bad(payload: dict) -> bool:
    quality = str(payload.get("quality", "good")).lower()
    return quality in {"bad", "offline", "invalid"}


_SOURCE_LABEL_TR = {
    "master": "Master",
    "sat01": "Satellite 01",
    "sat02": "Satellite 02",
}


def _signal_source_label(signal_key: str | None) -> str:
    """Sinyal anahtarinin prefix'inden kullanici dostu kaynak etiketi turet.

    Ornek: "master.overcurrent_tripped" -> "Master".
    Bilinmeyen prefix'ler icin prefix oldugu gibi (orn. "Sat03") doner;
    prefix yoksa bos string."""
    if not signal_key:
        return ""
    prefix = signal_key.split(".", 1)[0].lower()
    return _SOURCE_LABEL_TR.get(prefix, prefix.capitalize())


def _build_alarm_from_rule(
    payload: dict,
    rule_id: int,
    rule_name: str,
    rule_description: str,
    level: str,
    value: float,
    *,
    threshold: float | None = None,
    operator: str | None = None,
) -> dict:
    # Title kuralın adını yansıtır; kaynak (master/sat01/sat02) bilgisi
    # frontend'de signal_key prefix'inden turetilir, boylece backend dedup
    # eski/yeni formatlar arasinda calismaya devam eder.
    return {
        "message_id": str(uuid4()),
        "correlation_id": payload.get("correlation_id") or payload.get("message_id") or str(uuid4()),
        "device_id": payload.get("device_id"),
        "device_code": payload.get("device_code"),
        "source_gateway": payload.get("source_gateway"),
        "signal_key": payload.get("signal_key"),
        "title": rule_name,
        "description": rule_description or rule_name,
        "level": level,
        "source_timestamp": payload.get("source_timestamp") or datetime.now(timezone.utc).isoformat(),
        "rule_id": rule_id,
        # Bildirim panelinde "Akim 142 A (>100 A esigi)" gibi gostermek icin
        # tetikleyen olcum + esik + karsilastirma operatoru.
        "value": value,
        "value_string": payload.get("value_string"),
        "threshold": threshold,
        "operator": operator,
    }


def _build_quality_alarm(payload: dict) -> dict:
    quality = str(payload.get("quality", "")).lower()
    if quality == "offline":
        description = "Cihaz çevrimdışı, veri okunamıyor."
    elif quality == "invalid":
        description = "Cihazdan geçersiz veri alınıyor."
    else:
        description = "Cihaz haberleşmesinde sorun var."
    # Title backend dedup/clear icin device_code'a bagli kalir; UI'da
    # cihaz hucresi ayri gosterildigi icin baslikta sadece "Haberlesme arizasi"
    # gorunecek sekilde kisa anahtar kullaniyoruz.
    title = "Haberleşme arızası"
    return {
        "message_id": str(uuid4()),
        "correlation_id": payload.get("correlation_id") or payload.get("message_id") or str(uuid4()),
        "device_id": payload.get("device_id"),
        "device_code": payload.get("device_code"),
        "source_gateway": payload.get("source_gateway"),
        "title": title,
        "description": description,
        "level": "critical",
        "source_timestamp": payload.get("source_timestamp") or datetime.now(timezone.utc).isoformat(),
        "rule_id": None,
    }


def _notify_backend(payload: dict) -> None:
    headers = {"X-Service-Token": INTERNAL_SERVICE_TOKEN}
    body = {k: v for k, v in payload.items() if k != "rule_id"}
    response = requests.post(BACKEND_INTERNAL_URL, json=body, headers=headers, timeout=8)
    response.raise_for_status()


def _notify_backend_clear(
    rule_id: int,
    rule_title: str,
    device_code: str | None,
    source_gateway: str | None,
    signal_key: str | None = None,
) -> None:
    """Alarm sahada normale dondu - backend'deki acik kaydi reset=True yap.

    Backend `/internal/alarms/clear` endpoint'ine POST atar; mevcut acik alarm
    'Normale Donen - Onay Bekliyor' kismina geciyor.

    `signal_key` gondermek kritik: ayni cihazda farkli kaynak/sinyaller (orn.
    sat01.x_alarm, sat02.x_alarm) ayni baslikta acik alarma sahip oldugunda,
    sadece dogru sinyalin kaydi reset edilir; aksi halde yanlis sinyal
    'normale dondu' olarak isaretlenir."""
    headers = {"X-Service-Token": INTERNAL_SERVICE_TOKEN}
    body = {
        "rule_id": rule_id,
        "title": rule_title,
        "device_code": device_code,
        "source_gateway": source_gateway,
        "signal_key": signal_key,
        "source_timestamp": datetime.now(timezone.utc).isoformat(),
    }
    response = requests.post(BACKEND_INTERNAL_CLEAR_URL, json=body, headers=headers, timeout=8)
    response.raise_for_status()


def _publish_alarm(channel, alarm_payload: dict) -> None:
    channel.basic_publish(
        exchange=EXCHANGE,
        routing_key=OUTGOING_TOPIC,
        body=json.dumps(alarm_payload, ensure_ascii=False).encode("utf-8"),
        properties=pika.BasicProperties(
            content_type="application/json",
            delivery_mode=2,
            message_id=alarm_payload["message_id"],
            correlation_id=alarm_payload["correlation_id"],
            headers={
                "source_gateway": alarm_payload.get("source_gateway") or "",
                "device_code": alarm_payload.get("device_code") or "",
                "rule_id": alarm_payload.get("rule_id") if alarm_payload.get("rule_id") is not None else 0,
            },
        ),
    )


def _process_rules_for_payload(channel, payload: dict) -> None:
    signal_key = payload.get("signal_key")
    device_code = payload.get("device_code")
    value_raw = payload.get("value")
    if signal_key is None or value_raw is None:
        return
    if not _CACHE.is_alarmable(str(signal_key)):
        return
    try:
        value = float(value_raw)
    except (TypeError, ValueError):
        return

    now = time.monotonic()
    # Live-value cache'i her telemetri ile guncelle; composite kurallar diger
    # terimlerin son degerlerini buradan okuyacak.
    _LIVE.put(str(signal_key), str(device_code or ""), value, now)

    for rule in _CACHE.rules_for(str(signal_key), str(device_code) if device_code else None):
        # Composite kural ise anchor cihazi tetikleyen telemetri'nin
        # device_code'u olur; tum terimler bu cihaz uzerinden ("*") veya
        # explicit cihaz koduyla cozulur.
        key = (rule.id, str(device_code or ""), rule.signal_key)
        prev_active = _STATE.get_active(key)
        if rule.rule_kind == "composite" and rule.expression is not None:
            decision = evaluate_composite(
                rule,
                anchor_device_code=str(device_code or ""),
                live_value=lambda sk, dc, _now=now: _LIVE.get(sk, dc, now=_now),
            )
            if decision is None:
                # Henuz tum terimler icin veri yok — bu turda kuralı atla.
                continue
            should_be_active = decision
        else:
            should_be_active = evaluate_rule(rule, value, prev_active=prev_active)

        if should_be_active and not prev_active:
            if rule.debounce_sec > 0:
                _STATE.mark_pending(key, now)
                pending = _STATE.pending_since(key)
                if pending is not None and (now - pending) < rule.debounce_sec:
                    continue
            _STATE.set_active(key, True)
            alarm_payload = _build_alarm_from_rule(
                payload,
                rule_id=rule.id,
                rule_name=rule.name,
                rule_description=rule.description,
                level=rule.level,
                value=value,
                threshold=rule.threshold,
                operator=rule.comparator,
            )
            _publish_alarm(channel, alarm_payload)
            try:
                _notify_backend(alarm_payload)
            except Exception as exc:  # noqa: BLE001
                print(f"alarm-service-backend-error rule_id={rule.id} error={exc}")
            print(
                "alarm-service-raised "
                f"rule_id={rule.id} signal={rule.signal_key} dev={device_code} value={value}"
            )
        elif not should_be_active:
            # prev_active=True ise (transition kosulu) clear hemen gonderilir.
            # prev_active=False ama should_be_active=False senaryosunda da
            # periyodik clear gondeririz: alarm-service restart veya state
            # drift'inde DB'de hala acik alarm kalmis olabilir; min 60sn
            # araliklarla idempotent clear cagrisi atariz (backend zaten
            # eslesen acik alarm yoksa no_match doner).
            was_active = prev_active
            if was_active:
                _STATE.set_active(key, False)
            _STATE.clear_pending(key)
            # Drift recovery: prev_active False olsa bile 60sn'de bir
            # clear deneriz; transition durumda hemen, drift durumunda
            # rate-limited.
            should_send = was_active or _STATE.should_send_clear(key, now, 60.0)
            if should_send:
                _STATE.mark_clear_sent(key, now)
                try:
                    _notify_backend_clear(
                        rule_id=rule.id,
                        rule_title=rule.name,
                        device_code=str(device_code) if device_code else None,
                        source_gateway=payload.get("source_gateway"),
                        signal_key=rule.signal_key,
                    )
                    if was_active:
                        print(f"alarm-service-cleared rule_id={rule.id} signal={rule.signal_key} dev={device_code}")
                except Exception as exc:  # noqa: BLE001
                    if was_active:
                        print(f"alarm-service-clear-error rule_id={rule.id} error={exc}")


def main() -> None:
    _start_health_server()
    stop_event = Event()
    refresh_thread = Thread(target=_rules_refresh_loop, args=(stop_event,), daemon=True)
    refresh_thread.start()

    # Ilk kural cekimini blokla (en fazla 15 sn)
    for _ in range(30):
        if _CACHE.is_ready():
            break
        time.sleep(0.5)

    print(f"alarm-service-starting rules_ready={_CACHE.is_ready()}")
    while True:
        connection = None
        try:
            connection = pika.BlockingConnection(pika.URLParameters(RABBIT_URL))
            channel = connection.channel()
            channel.exchange_declare(exchange=EXCHANGE, exchange_type="topic", durable=True)
            channel.exchange_declare(exchange=DLX_EXCHANGE, exchange_type="topic", durable=True)
            channel.queue_declare(
                queue=QUEUE_NAME,
                durable=True,
                arguments={
                    "x-dead-letter-exchange": DLX_EXCHANGE,
                    "x-dead-letter-routing-key": "telemetry.received.dead",
                },
            )
            channel.queue_bind(exchange=EXCHANGE, queue=QUEUE_NAME, routing_key=INCOMING_TOPIC)
            channel.basic_qos(prefetch_count=20)

            def _on_message(ch, method, properties, body):  # noqa: ANN001
                _ = properties
                try:
                    payload = json.loads(body.decode("utf-8"))
                    # NOT: Otomatik "Haberleşme arızası" alarmı uretmiyoruz —
                    # kullanici kendi kurallari uzerinden bu durumu tanimliyor.
                    # Sadece tanimli alarm kurallarini degerlendir.
                    _process_rules_for_payload(channel, payload)
                    ch.basic_ack(delivery_tag=method.delivery_tag)
                except Exception as ex:  # noqa: BLE001
                    print(f"alarm-service-failed error={ex}")
                    ch.basic_nack(delivery_tag=method.delivery_tag, requeue=False)

            channel.basic_consume(queue=QUEUE_NAME, on_message_callback=_on_message)
            print("alarm-service-running")
            channel.start_consuming()
        except Exception as ex:  # noqa: BLE001
            print(f"alarm-service-reconnect error={ex}")
            time.sleep(3)
        finally:
            if connection is not None:
                try:
                    connection.close()
                except Exception:
                    pass


if __name__ == "__main__":
    main()
