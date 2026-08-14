import asyncio
import json
import os
import queue
import re
import ssl
import threading
import time
from collections.abc import Callable
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, HTTPServer
from threading import Event, Lock, Thread
from uuid import uuid4

import nats
import pika
import requests

from alarm_service import watchdog
from alarm_service.rules import AlarmRuleCache, evaluate_composite, evaluate_rule

# ----- Telemetri kaynagi: NATS JetStream (gateway -> tag-engine -> JetStream) -
NATS_URL = os.getenv("NATS_URL", "nats://localhost:4222")
NATS_SUBJECT_NORMALIZED = os.getenv(
    "NATS_SUBJECT_TELEMETRY_NORMALIZED", "e1.telemetry.normalized.>"
)
NATS_DURABLE = os.getenv("NATS_ALARM_DURABLE", "alarm-service-evaluator")
# ----- IKI HAT: oncelikli (dijital/durum) ve toplu (analog) -------------------
# tag-engine konuya bir sinif token'i ekler
# (`e1.telemetry.normalized.<gw>.<prio|bulk>`). Alarm URETEN yol oncelikli
# hatta baglanir; ayrinti icin bkz. _hatlari_kur.
NATS_STREAM_NORMALIZED = os.getenv(
    "NATS_STREAM_TELEMETRY_NORMALIZED", "TELEMETRY_NORMALIZED"
)
NATS_SUBJECT_PRIO = os.getenv(
    "NATS_SUBJECT_TELEMETRY_NORMALIZED_PRIO", "e1.telemetry.normalized.*.prio"
)
NATS_SUBJECT_BULK = os.getenv(
    "NATS_SUBJECT_TELEMETRY_NORMALIZED_BULK", "e1.telemetry.normalized.*.bulk"
)
# Sinifsiz (4 token'li) eski konu — guncelleme penceresinde henuz
# yenilenmemis tag-engine buraya basar. Abone olunmazsa alarm
# degerlendirmesi SESSIZCE dururdu.
NATS_SUBJECT_LEGACY = os.getenv(
    "NATS_SUBJECT_TELEMETRY_NORMALIZED_LEGACY", "e1.telemetry.normalized.*"
)
NATS_DURABLE_PRIO = os.getenv("NATS_ALARM_DURABLE_PRIO", "alarm-service-evaluator-prio")
NATS_DURABLE_BULK = os.getenv("NATS_ALARM_DURABLE_BULK", "alarm-service-evaluator-bulk")
NATS_DURABLE_LEGACY = os.getenv(
    "NATS_ALARM_DURABLE_LEGACY", "alarm-service-evaluator-legacy"
)
# `max_ack_pending` = sunucunun teslim edip ack bekledigi tavan, yani
# tuketicinin ONUNDEKI kuyrugun uzunlugu. Oncelikli hatta KUCUK tutulur:
# buyuk bir tavan, ayirdigimiz hattin icine yeniden kuyruk kurmak olurdu.
ACK_PENDING_PRIO = int(os.getenv("NATS_ALARM_ACK_PENDING_PRIO", "500"))
ACK_PENDING_BULK = int(os.getenv("NATS_ALARM_ACK_PENDING_BULK", "10000"))
# DLQ subject prefix — max_deliver'a takilan mesajlar buraya gider.
SUBJECT_DLQ_PREFIX = os.getenv("NATS_SUBJECT_DLQ_PREFIX", "e1.dlq.alarm-service")
MAX_DELIVER = int(os.getenv("NATS_WORKER_MAX_DELIVER", "10"))

# ----- Alarm yayini: RabbitMQ (notification-worker, internal alarm endpoint) --
# RABBITMQ_URL default'u BOS — production'da `.env`'de zorunlu set edilmesi
# gerekir. Eski "amqp://guest:guest@localhost:5672/" default'u dev'de bag
# kuruyordu ama production'da yanlislikla aktif olursa guest credential ile
# uretim broker'ina baglanma riski. Compose env zorunlu `${RABBITMQ_URL}`.
RABBIT_URL = os.getenv("RABBITMQ_URL", "")
EXCHANGE = os.getenv("RABBITMQ_EXCHANGE", "e1.events")
OUTGOING_TOPIC = os.getenv("ALARM_OUTGOING_TOPIC", "alarm.created")
DLX_EXCHANGE = os.getenv("RABBITMQ_DLX_EXCHANGE", "e1.events.dlx")
HEALTH_HOST = os.getenv("WORKER_HEALTH_HOST", "127.0.0.1")
HEALTH_PORT = int(os.getenv("WORKER_HEALTH_PORT", "8012"))
BACKEND_INTERNAL_URL = os.getenv("BACKEND_INTERNAL_ALARM_URL", "http://127.0.0.1:8000/api/v1/internal/alarms")
BACKEND_INTERNAL_CLEAR_URL = os.getenv(
    "BACKEND_INTERNAL_ALARM_CLEAR_URL",
    "http://127.0.0.1:8000/api/v1/internal/alarms/clear",
)
BACKEND_API_BASE = os.getenv("BACKEND_API_BASE", "http://127.0.0.1:8000/api/v1")
INTERNAL_SERVICE_TOKEN = os.getenv("INTERNAL_SERVICE_TOKEN", "change-me-internal-token")

# NOT: "Drift clear" (aktif olmayan her (kural x cihaz) anahtari icin
# periyodik idempotent clear POST'u) KALDIRILDI. Uretim hizi O(kural x
# cihaz) oldugu icin 401 cihazda HTTP gonderim hizini asiyor, kuyruk
# sinirsiz buyuyordu. Restart/state-drift telafisi backend'de:
# `alarm_reconciliation.py` 30 sn'de bir acik alarmlari son telemetriye
# karsi degerlendirip kendisi kapatir. Clear artik yalnizca gercek
# gecislerde (aktif -> normal) gonderilir.
# Backend gonderim kuyrugu tavani — bellek guvenligi icin sinirli (bkz.
# _SamplesCache OOM gecmisi). Dolarsa clear isleri dusurulur ve sayilir.
NOTIFY_QUEUE_MAX = int(os.getenv("ALARM_NOTIFY_QUEUE_MAX", "10000"))
RULES_REFRESH_SEC = int(os.getenv("ALARM_RULES_REFRESH_SEC", "10"))
# Raise POST'u basarisiz olursa iki deneme arasindaki bekleme. KISA tutulur:
# kuyruk tek thread'de islendigi icin her bekleme sirayi bloklar. Asil telafi
# yolu bekleme degil, kalici basarisizlikta durumu geri alip bir sonraki
# telemetriye yeniden denetmektir.
RAISE_RETRY_BEKLEME_SN = float(os.getenv("ALARM_RAISE_RETRY_SEC", "0.5"))


# Kural (rule_id, device_code) bazli durum takibi: aktiflik + debounce buffer + ilk gorulen zaman
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


class _RuleState:
    def __init__(self) -> None:
        self._lock = Lock()
        self._active: dict[tuple[int, str, str], bool] = {}
        self._pending_since: dict[tuple[int, str, str], float] = {}

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


# Composite agg terimleri (Faz 2): son N saniyenin ortalamasi/min/max/sum/
# count_above/count_below hesaplari icin (signal_key, device_code) basina
# zaman damgali ornekler tutuyoruz. Bellek koruma: pencere max 24 saat, ama
# samples ust limit 5000/sinyal (yuksek frekanslı sinyaller icin).
from collections import deque
from typing import Deque


class _SamplesCache:
    MAX_PER_KEY = 5000
    MAX_RETAIN_SEC = 86400  # 24 saat
    # Key TTL: 24 saatte hic ornek gelmemis keys cache'ten silinir. Cihaz/sinyal
    # backend'de silindiginde _buf dict'i sonsuza buyumesin diye periyodik
    # cleanup. Worst case: 600 cihaz x 175 sinyal x 5000 sample = 525M tuple,
    # ~8 GB. TTL ile silinen cihazlar disari atilir.
    _CLEANUP_INTERVAL_SEC = 600  # 10 dakikada bir

    # Kural yoksa/pencere okunamazsa kullanilacak taban saklama suresi.
    # `put` zaten yalnizca agg kurali olan sinyaller icin cagrildigi icin bu
    # deger pratikte nadiren devreye girer.
    MIN_RETAIN_SEC = 300

    def __init__(self) -> None:
        self._lock = Lock()
        # (signal_key, device_code) -> deque[(monotonic_ts, value)]
        self._buf: dict[tuple[str, str], Deque[tuple[float, float]]] = {}
        # Son cleanup zamani (periyodik prune icin)
        self._last_cleanup: float = 0.0

    def _retain_sec(self) -> float:
        """Kurallarin ihtiyac duydugu saklama suresi (+%20 emniyet payi).

        Kural onbellegi henuz hazir degilse taban degere duser; boylece
        acilista gelen ilk ornekler kaybolmaz.
        """
        try:
            pencere = _CACHE.max_agg_window_sec()
        except Exception:  # noqa: BLE001
            pencere = 0
        if pencere <= 0:
            return float(self.MIN_RETAIN_SEC)
        return float(min(self.MAX_RETAIN_SEC, max(self.MIN_RETAIN_SEC, pencere * 1.2)))

    def put(self, signal_key: str, device_code: str, value: float, now: float) -> None:
        with self._lock:
            key = (signal_key, device_code)
            dq = self._buf.get(key)
            if dq is None:
                dq = deque(maxlen=self.MAX_PER_KEY)
                self._buf[key] = dq
            dq.append((now, value))
            # Geriye dogru KURALLARIN BAKMADIGI ornekleri sil.
            #
            # Sabit 24 saat tutmak, hicbir kuralin okumayacagi veriyi saklamak
            # demekti. Pencere artik en uzun `agg_window_sec`'e gore belirlenir
            # (uzerine bir emniyet payi). Kural yoksa `retain_sec` taban degere
            # duser ve zaten `put` hic cagrilmaz.
            cutoff = now - self._retain_sec()
            while dq and dq[0][0] < cutoff:
                dq.popleft()
            # Periyodik global cleanup: silinmis cihaz/sinyal'in key'lerini
            # at. 10 dakika icinde guncellenmeyen tum key'leri kaldir.
            if now - self._last_cleanup > self._CLEANUP_INTERVAL_SEC:
                stale_keys = [
                    k for k, q in self._buf.items()
                    if not q or q[-1][0] < cutoff
                ]
                for k in stale_keys:
                    self._buf.pop(k, None)
                self._last_cleanup = now

    def lookup(
        self,
        signal_key: str,
        device_code: str,
        agg_fn: str,
        window_sec: int,
        agg_arg: float,
        *,
        now: float | None = None,
    ) -> float | None:
        if now is None:
            now = time.monotonic()
        cutoff = now - max(1, int(window_sec))
        with self._lock:
            dq = self._buf.get((signal_key, device_code))
            if not dq:
                return None
            # Pencere disinda kalanlari atla (deque sirali, sol bastan).
            samples = [v for ts, v in dq if ts >= cutoff]
        if not samples:
            return None
        if agg_fn == "avg":
            return sum(samples) / len(samples)
        if agg_fn == "min":
            return min(samples)
        if agg_fn == "max":
            return max(samples)
        if agg_fn == "sum":
            return sum(samples)
        if agg_fn == "count_above":
            return float(sum(1 for v in samples if v > agg_arg))
        if agg_fn == "count_below":
            return float(sum(1 for v in samples if v < agg_arg))
        return None


_SAMPLES = _SamplesCache()

_CACHE = AlarmRuleCache(
    base_url=BACKEND_API_BASE,
    service_token=INTERNAL_SERVICE_TOKEN,
    refresh_sec=RULES_REFRESH_SEC,
)


class _HealthHandler(BaseHTTPRequestHandler):
    def do_GET(self):  # noqa: N802
        if self.path == "/health":
            # Kural onbellegi hazir OLSA BILE ana dongu kilitlenmis olabilir;
            # onceden bu uc o durumda da 200 donuyordu ve healthcheck arizayi
            # hic gormuyordu. Artik kalp atisi da sart.
            canli = watchdog.saglikli()
            body = {
                "status": ("ok" if _CACHE.is_ready() else "starting") if canli else "stalled",
                "service": "alarm-service",
                "rules_ready": _CACHE.is_ready(),
                "atissiz_sn": round(watchdog.gecen_sn(), 1),
                # Backend gonderim kuyrugu derinligi — surekli buyuyorsa
                # backend yanit vermiyor demektir (mesaj dongusu etkilenmez).
                "notify_bekleyen": _NOTIFIER.bekleyen(),
            }
            payload = json.dumps(body, ensure_ascii=False).encode("utf-8")
            self.send_response(200 if canli else 503)
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


# Alarm degerlendirmesini ENGELLEYEN kaliteler.
#
# Backend'deki `tag_engine_service._OFFLINE_QUALITIES` ile AYNI olmali —
# iki servis ayri paketler oldugu icin kod paylasilamiyor, bu yuzden burada
# tekrarlaniyor. Birini degistiren digerini de degistirmeli.
#
# `comm_lost` ve `restart` BURADA OLMAK ZORUNDA: gateway'in haberlesmesi
# kopan cihaz icin bastigi kalite tam olarak `comm_lost`. Eski kume bu ikisini
# icermiyordu ve fonksiyon zaten HIC CAGRILMIYORDU.
_ALARM_BLOCKING_QUALITIES = frozenset(
    {"bad", "offline", "invalid", "comm_lost", "restart", "forced"}
)


def _quality_is_bad(payload: dict) -> bool:
    """Bu okuma alarm mantiginda KULLANILMAMALI mi?

    True ise okuma ne yeni alarm acar ne de acik alarmi kapatir; alarm
    durumu DONAR.

    NEDEN KAPATMAMAK ASIL MESELE: haberlesmesi kopan cihaz icin gateway
    `comm_lost` kalitesiyle 0.0 basar. Kalite kapisi olmadan bu 0.0 "esik
    artik saglanmiyor" diye yorumlanip ACIK ARIZA ALARMINI KAPATIR ve
    harita yesile doner — yani baglanti koptugu anda sistem "ariza gecti"
    der. SCADA doktrini bunun tersidir: veri kaybinda SON BILINEN DURUM
    korunur.

    KARA LISTE (beyaz liste DEGIL): taninmayan bir kalite token'i
    degerlendirmeyi ENGELLEMEZ. Beyaz liste olsaydi gateway ileride yeni bir
    kalite adi yayinladiginda tum alarm motoru sessizce durabilirdi.
    """
    quality = str(payload.get("quality", "good")).strip().lower()
    return quality in _ALARM_BLOCKING_QUALITIES


_SOURCE_LABEL_TR = {
    "master": "Master",
}

#: `sat07` gibi uydu kaynaklarini yakalar.
#:
#: NEDEN SABIT SOZLUK DEGIL: Pole Master Kit 9 uydu tasiyor (sat01..sat09).
#: Elle yazilan bir sozluk once sat01/sat02'de kalir, kalan yedi uydu
#: "Sat07" gibi yari-cevrilmis bir etiketle gorunurdu — hata da vermezdi.
_SAT_SOURCE_RE = re.compile(r"^sat(\d{1,2})$")


def _signal_source_label(signal_key: str | None) -> str:
    """Sinyal anahtarinin prefix'inden kullanici dostu kaynak etiketi turet.

    Ornek: "master.overcurrent_tripped" -> "Master",
           "sat07.fault_current"        -> "Satellite 07".
    Bilinmeyen prefix'ler icin prefix oldugu gibi doner; prefix yoksa bos
    string."""
    if not signal_key:
        return ""
    prefix = signal_key.split(".", 1)[0].lower()
    if prefix in _SOURCE_LABEL_TR:
        return _SOURCE_LABEL_TR[prefix]
    eslesme = _SAT_SOURCE_RE.match(prefix)
    if eslesme:
        return f"Satellite {int(eslesme.group(1)):02d}"
    return prefix.capitalize()


def _build_alarm_from_rule(
    payload: dict,
    rule_id: int,
    rule_name: str,
    rule_description: str,
    level: str,
    value: float,
    *,
    signal_key: str | None = None,
    threshold: float | None = None,
    operator: str | None = None,
    produces_fault: bool = True,
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
        # Kural verdiyse KURALIN anchor signal_key'i kullanilir; clear tarafi da
        # ayni degeri gonderdigi icin backend acik alarmi bulabilir. Bkz.
        # cagirandaki not (composite kuralda alarm asla kapanmiyordu).
        "signal_key": signal_key or payload.get("signal_key"),
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
        # Kuraldan "dondurulan" produces_fault — backend bu alani AlarmEvent'e
        # yazar; haritada kirmizi gosterim + Hat Arizasi yalniz True ise olusur.
        "produces_fault": produces_fault,
    }


#: Standart kural bulunamadigi durumda kullanilan varsayilanlar. Kural
#: SILINEMEZ (backend 409 doner) ama onbellek daha hic dolmamis olabilir;
#: o pencerede haberlesme alarmini susturmak, cihaz kopmasini gizlemek olurdu.
COMM_VARSAYILAN_BASLIK = "Haberleşme arızası"
COMM_VARSAYILAN_SEVIYE = "critical"


def _build_quality_alarm(payload: dict, rule=None) -> dict:  # noqa: ANN001
    quality = str(payload.get("quality", "")).lower()
    if quality == "offline":
        description = "Cihaz çevrimdışı, veri okunamıyor."
    elif quality == "invalid":
        description = "Cihazdan geçersiz veri alınıyor."
    else:
        description = "Cihaz haberleşmesinde sorun var."
    # BASLIK KURALIN ADIDIR. Iki sebeple:
    #   1. Bildirim kanali secimi alarmin BASLIGI ile kuralin ADINI
    #      eslestirerek calisiyor (notification_dispatch_service.
    #      _resolve_active_rule); baslik sabit kalsaydi kural adi degistigi
    #      an haberlesme alarmi "kuralsiz" sayilip tum kanallara duserdi.
    #   2. Kullanici kurali yeniden adlandirabilsin diye — ekranda gordugu ad
    #      ile alarm listesinde gordugu baslik ayni olmali.
    title = (getattr(rule, "name", None) or COMM_VARSAYILAN_BASLIK).strip()
    level = getattr(rule, "level", None) or COMM_VARSAYILAN_SEVIYE
    return {
        "message_id": str(uuid4()),
        "correlation_id": payload.get("correlation_id") or payload.get("message_id") or str(uuid4()),
        "device_id": payload.get("device_id"),
        "device_code": payload.get("device_code"),
        "source_gateway": payload.get("source_gateway"),
        "title": title,
        "description": description,
        "level": level,
        "source_timestamp": payload.get("source_timestamp") or datetime.now(timezone.utc).isoformat(),
        "rule_id": None,
        # HAT ARIZASI URETIR MI — STANDART KURALDAN.
        #
        # Bu alan eskiden hic gonderilmiyordu ve backend semasi True
        # varsayiyordu. Sonucu: haberlesmesi kopan cihaz
        # `fault_recompute_service` icin "arizayi GORDUM" diyen bir cihaz
        # sayiliyor, kendisi ile sonraki cihaz arasindaki aralikta HAT
        # ARIZASI aciliyordu — haritada kirmizi kesim, operatore bildirim,
        # ekibe bosuna saha cikisi. Sessiz kalan cihaz ariza akimi GORMUS
        # DEGILDIR; sadece bilmiyoruzdur.
        #
        # Artik karar operatorun: Alarm Kurallari > "Haberleşme arızası" >
        # Hat Arızası. Kural okunamadiysa VARSAYILAN FALSE — emin olmadigimiz
        # yerde ariza uydurmayiz.
        "produces_fault": bool(getattr(rule, "produces_fault", False)),
        # Analiz katmani "hangi cihazin haberlesmesi sik kopuyor" sorusunu
        # bu alandan cevapliyor. Backend gonderilmeyince "rule" yaziyordu ve
        # haberlesme alarmlari kural alarmi gibi sayiliyordu.
        "kind": "comm_loss",
    }


def _notify_backend(payload: dict, http=None) -> int | None:
    """Backend `/internal/alarms` POST eder; alarm row olusur veya
    deduplicate edilir. Donus: backend'in atadigi `alarm_id` (notification-
    worker bunu kullanarak dispatch tetikler) veya None (dedup/error).

    `http`: baglanti havuzlu `requests.Session` (gonderim thread'i verir);
    None ise duz `requests` kullanilir (testler icin).
    """
    headers = { "X-Service-Token": INTERNAL_SERVICE_TOKEN, "X-Service-Name": "alarm-service" }
    body = {k: v for k, v in payload.items() if k != "rule_id"}
    response = (http or requests).post(BACKEND_INTERNAL_URL, json=body, headers=headers, timeout=8)
    response.raise_for_status()
    try:
        data = response.json()
        if isinstance(data, dict):
            alarm_id = data.get("alarm_id")
            if isinstance(alarm_id, int):
                return alarm_id
    except (ValueError, TypeError):
        pass
    return None


def _notify_backend_clear(
    rule_id: int,
    rule_title: str,
    device_code: str | None,
    source_gateway: str | None,
    signal_key: str | None = None,
    http=None,
) -> None:
    """Alarm sahada normale dondu - backend'deki acik kaydi reset=True yap.

    Backend `/internal/alarms/clear` endpoint'ine POST atar; mevcut acik alarm
    'Normale Donen - Onay Bekliyor' kismina geciyor.

    `signal_key` gondermek kritik: ayni cihazda farkli kaynak/sinyaller (orn.
    sat01.x_alarm, sat02.x_alarm) ayni baslikta acik alarma sahip oldugunda,
    sadece dogru sinyalin kaydi reset edilir; aksi halde yanlis sinyal
    'normale dondu' olarak isaretlenir."""
    headers = { "X-Service-Token": INTERNAL_SERVICE_TOKEN, "X-Service-Name": "alarm-service" }
    body = {
        "rule_id": rule_id,
        "title": rule_title,
        "device_code": device_code,
        "source_gateway": source_gateway,
        "signal_key": signal_key,
        "source_timestamp": datetime.now(timezone.utc).isoformat(),
    }
    response = (http or requests).post(BACKEND_INTERNAL_CLEAR_URL, json=body, headers=headers, timeout=8)
    response.raise_for_status()


class _BackendNotifier:
    """Backend HTTP + RabbitMQ yayinini event loop DISINA tasiyan gonderim hatti.

    YASANAN SORUN: `_notify_backend*` cagrilari senkron `requests.post`
    (timeout 8 sn) ile dogrudan asyncio mesaj callback'inin icinde
    kosuyordu. 401 cihazlik yuk testinde drift-recovery clear'lari
    ((kural x cihaz) basina 60 sn'de bir) saniyede onlarca BLOKLU HTTP'ye
    donustu; her POST backend'de bir DB sorgusu demek. Sonuc: alarm-service
    CPU %25'te bosta beklerken prio kuyrugu ~1.166 mesaj/sn birikiyordu —
    tag-engine'in seri PubAck darbogaziyla ayni hastalik sinifi.

    COZUM: tek daemon thread + sinirli kuyruk + baglanti havuzlu Session.
    Mesaj dongusu artik yalnizca bellek ici is yapar (cache/state); HTTP ve
    RabbitMQ yayini burada kosar. Siralama korunur: raise isinde ONCE
    backend POST (alarm_id doner) SONRA RabbitMQ publish — notification-
    worker'in alarm_id'ye ihtiyaci var (bkz. eski "SIRA ONEMLI" notu).

    TESLIM GARANTISI DEGISMEDI: eski kod da HTTP hatasini yutup ack'liyordu
    (print + devam). Kuyruk dolarsa clear isleri dusurulur — idempotent ve
    periyodik olduklari icin bir sonraki aralik telafi eder; raise dususu
    ise yuksek sesle loglanir (10k'lik kuyrugun dolmasi backend'in uzun
    suredir yanitsiz oldugu anlamina gelir).
    """

    def __init__(self, maxsize: int = NOTIFY_QUEUE_MAX) -> None:
        self._q: queue.Queue = queue.Queue(maxsize=maxsize)
        self._thread: Thread | None = None
        self._lock = Lock()
        self._dusen_clear = 0
        # Backend'e YAZILAMAMIS raise sayisi. /health govdesinde gorunur:
        # sifirdan buyuk olmasi "alarm uretildi ama kaydedilemedi" demektir
        # ve operatorun bunu gormesi gerekir.
        self._dusen_raise = 0

    def start(self) -> None:
        with self._lock:
            if self._thread is not None and self._thread.is_alive():
                return
            self._thread = Thread(target=self._run, name="alarm-notify", daemon=True)
            self._thread.start()

    def stop(self) -> None:
        try:
            self._q.put_nowait(None)
        except queue.Full:
            pass
        t = self._thread
        if t is not None:
            t.join(timeout=5)

    def bekleyen(self) -> int:
        return self._q.qsize()

    def submit_raise(
        self,
        alarm_payload: dict,
        *,
        rule_id: int | None,
        geri_al: "Callable[[], None] | None" = None,
    ) -> None:
        """Raise isini kuyruga alir.

        `geri_al`: backend POST'u KALICI basarisiz olursa cagrilir. Cagiran
        taraf alarmi gondermeden ONCE kendi bellek durumunu "aktif"
        isaretledigi icin, gonderim basarisiz olunca o isaret GERI ALINMALI —
        aksi halde alarm ne DB'ye yazilir ne de bir daha denenir.
        """
        self.start()
        try:
            self._q.put_nowait(("raise", alarm_payload, rule_id, geri_al))
        except queue.Full:
            print(f"alarm-service-notify-kuyruk-dolu RAISE dustu rule_id={rule_id}")
            # Kuyruk dolulugu da kalici basarisizliktir: durumu geri al ki
            # bir sonraki telemetri yeniden denesin.
            if geri_al is not None:
                try:
                    geri_al()
                except Exception as exc:  # noqa: BLE001
                    print(f"alarm-service-geri-al-hata rule_id={rule_id} error={exc}")

    def submit_clear(
        self,
        *,
        rule_id: int,
        rule_title: str,
        device_code: str | None,
        source_gateway: str | None,
        signal_key: str | None,
        was_active: bool,
    ) -> None:
        self.start()
        alan = {
            "rule_id": rule_id,
            "rule_title": rule_title,
            "device_code": device_code,
            "source_gateway": source_gateway,
            "signal_key": signal_key,
        }
        try:
            self._q.put_nowait(("clear", alan, was_active))
        except queue.Full:
            self._dusen_clear += 1
            if self._dusen_clear % 100 == 1:
                print(
                    "alarm-service-notify-kuyruk-dolu clear dustu "
                    f"toplam={self._dusen_clear} (idempotent, sonraki periyot telafi eder)"
                )

    def _run(self) -> None:
        http = requests.Session()
        while True:
            job = self._q.get()
            if job is None:
                return
            try:
                self._isle(http, job)
            except Exception as exc:  # noqa: BLE001
                print(f"alarm-service-notify-hata is={job[0]} error={exc}")

    def _isle(self, http, job) -> None:
        tur = job[0]
        if tur == "raise":
            _, alarm_payload, rule_id, geri_al = job
            alarm_id: int | None = None
            # BIR KEZ HIZLI YENIDEN DENEME. Kaybin en sik sebebi backend'in
            # guncelleme sirasinda 30-60 sn yeniden baslamasi degil, tek bir
            # anlik ConnectionError; kisa bir tekrar bunlarin cogunu kurtarir.
            # Uzun/agresif retry BILEREK yok: kuyruk tek thread'de islendigi
            # icin her deneme sirayi bloklar. Kalici basarisizlikta durum geri
            # alinir ve bir sonraki telemetri dogal olarak yeniden tetikler.
            son_hata: Exception | None = None
            for deneme in range(2):
                try:
                    alarm_id = _notify_backend(alarm_payload, http=http)
                    son_hata = None
                    break
                except Exception as exc:  # noqa: BLE001
                    son_hata = exc
                    if deneme == 0:
                        time.sleep(RAISE_RETRY_BEKLEME_SN)
            if son_hata is not None:
                print(
                    f"alarm-service-backend-error rule_id={rule_id} error={son_hata}"
                )
            if alarm_id is not None:
                alarm_payload["alarm_id"] = alarm_id
                try:
                    _publish_alarm_to_rabbitmq(alarm_payload)
                except Exception as exc:  # noqa: BLE001
                    print(f"alarm-service-publish-error rule_id={rule_id} error={exc}")
            else:
                # ALARM DB'YE YAZILAMADI.
                #
                # Eskiden burada yalnizca bir print vardi ve is biterdi: cagiran
                # taraf durumu ZATEN "aktif" isaretledigi icin sonraki telemetri
                # de tetiklemiyordu. Sonuc, alarmin SESSIZCE ve KALICI kaybi —
                # alarm satiri hic olusmuyor, marker yesil kaliyor, produces_fault
                # alarmi olmadigi icin hat arizasi acilmiyor, bildirim gitmiyor.
                #
                # RabbitMQ'ya da BASILMAZ: notification-worker alarm_id bekliyor;
                # alarm_id'siz mesaj DB'de karsiligi olmayan bir bildirim uretirdi.
                self._dusen_raise += 1
                print(
                    "alarm-service-RAISE-KAYIP "
                    f"rule_id={rule_id} dev={alarm_payload.get('device_code')} "
                    f"signal={alarm_payload.get('signal_key')} "
                    f"toplam={self._dusen_raise} — durum geri alindi, "
                    "sonraki telemetri yeniden deneyecek"
                )
                if geri_al is not None:
                    try:
                        geri_al()
                    except Exception as exc:  # noqa: BLE001
                        print(f"alarm-service-geri-al-hata rule_id={rule_id} error={exc}")
        elif tur == "clear":
            _, alan, was_active = job
            try:
                _notify_backend_clear(http=http, **alan)
                if was_active:
                    print(
                        "alarm-service-cleared "
                        f"rule_id={alan['rule_id']} signal={alan.get('signal_key')} "
                        f"dev={alan.get('device_code')}"
                    )
            except Exception as exc:  # noqa: BLE001
                if was_active:
                    print(f"alarm-service-clear-error rule_id={alan['rule_id']} error={exc}")


_NOTIFIER = _BackendNotifier()


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


def _process_device_comm_alarm(payload: dict) -> None:
    """Cihaz bazli 'Haberlesme arizasi' alarmi — sinyal/kuraldan bagimsiz.

    2026-08-07 OLAYI: `_QualityState`/`_build_quality_alarm` onceden de
    TANIMLIYDI ama hicbir yerden CAGRILMIYORDU — sahada bir cihaz saatlerce
    haberlesmeden dustu ve operatore HICBIR bildirim (SMS/Telegram/e-posta)
    gitmedi; sorun ancak elle fark edildi. Bu fonksiyon o bosluğu kapatir:
    ilk `comm_lost`/`offline`/`invalid` kaliteli okumada CRITICAL alarm
    ACAR (backend'in normal /internal/alarms hatti uzerinden — bildirim
    dispatch'i otomatik calisir), kalite `good`'a donunce CLEAR gonderir.
    `_QUALITY_STATE` cihaz basina dedup sagladigi icin ayni cihazdan gelen
    binlerce ardisik comm_lost okumasi TEK alarm uretir.

    Kalite tespiti gateway TARAFINDA zaten debounce'lu (yadnp3 adaptoru
    ancak recovery-timeout grace suresinden SONRA comm_lost basar) — burada
    ekstra bekleme eklemek operatoru bilgilendirmeyi gereksiz gecikirdi.

    STANDART KURAL (2026-08-12): alarmin seviyesi, basligi, kapsami, bildirim
    kanallari ve hat arizasi uretip uretmeyecegi artik kodda GOMULU DEGIL;
    Alarm Kurallari'ndaki `rule_kind='comm_loss'` kaydindan okunur. Kural
    pasiflestirilirse haberlesme alarmi hic uretilmez — operatorun kapatma
    yolu vardi ve tek yol kod degisikligiydi.
    """
    device_code = payload.get("device_code")
    dc = str(device_code or "")
    if not dc:
        return
    kural = _CACHE.comm_rule()
    if kural is not None and not kural.is_active:
        # KAYIT VAR ama PASIF -> operator bilerek kapatmis, alarm uretilmez.
        return
    if kural is None and _CACHE.is_ready() and _CACHE.comm_kaydi_var():
        # Kayit var, aktif/pasif ayrimi yukarida yapildi; buraya dusmesi
        # beklenmez. Yine de kapali kabul et.
        return
    # KAYIT HIC YOKSA (comm_kaydi_var False) VARSAYILANLARLA DEVAM EDILIR.
    #
    # Eskiden burada "kural yok VE onbellek dolu -> operator kapatmis" deniyor
    # ve TUM haberlesme alarmlari susturuluyordu. Ama kayit yoklugu operator
    # karari degil KURULUM EKSIGIDIR: standart kurali migration 0058
    # tohumluyor, temiz kurulum ise `create_all` + `stamp head` yaptigi icin
    # o migration HIC kosmuyor -> `alarm_rules` bos -> sifirdan kurulan her
    # sahada haberlesme alarmi KALICI olarak susuyordu. Cihaz hattan dusse
    # bile ne alarm aciliyor ne bildirim gidiyordu.
    #
    # Backend'in kendi yolu (`alarm_engine_service.comm_loss_rule`) bu ayrimi
    # zaten dogru yapiyordu; burasi ona hizalandi. `/internal/alarm-rules`
    # artik pasif comm_loss kaydini da donduruyor ki ikisi ayirt edilebilsin.
    if kural is not None and not _CACHE.rule_covers_device(
        kural, dc, _CACHE.device_model(dc)
    ):
        return
    if _quality_is_bad(payload):
        if not _QUALITY_STATE.is_bad(dc):
            _QUALITY_STATE.mark_bad(dc)
            # geri_al: backend POST'u kalici basarisiz olursa "bu cihaz bozuk"
            # isareti geri alinir, boylece sonraki kalite okumasi yeniden dener.
            _NOTIFIER.submit_raise(
                _build_quality_alarm(payload, kural),
                rule_id=None,
                geri_al=lambda _d=dc: _QUALITY_STATE.mark_good(_d),
            )
    elif _QUALITY_STATE.mark_good(dc):
        _NOTIFIER.submit_clear(
            rule_id=None,
            # CLEAR BASLIGI RAISE ILE AYNI OLMALI — backend acik alarmi
            # baslikla buluyor. Kural adi degistiginde ikisi ayrisirsa alarm
            # normale donse bile listede acik kalirdi.
            rule_title=(getattr(kural, "name", None) or COMM_VARSAYILAN_BASLIK).strip(),
            device_code=device_code,
            source_gateway=payload.get("source_gateway"),
            signal_key=None,
            was_active=True,
        )


def _process_rules_for_payload(channel, payload: dict) -> None:
    signal_key = payload.get("signal_key")
    device_code = payload.get("device_code")
    value_raw = payload.get("value")
    _process_device_comm_alarm(payload)
    if signal_key is None or value_raw is None:
        return
    # KALITE KAPISI — bu satir olmadan sistem, cihazla baglanti koptugu anda
    # "ariza gecti" diyordu. Erken donuyoruz: ne kural degerlendirilir, ne
    # canli deger/ornek onbellegi guncellenir, ne de clear cagrisi atilir.
    if _quality_is_bad(payload):
        return
    if not _CACHE.is_alarmable(str(signal_key)):
        return
    try:
        value = float(value_raw)
    except (TypeError, ValueError):
        return

    now = time.monotonic()
    # Live-value + samples cache'i her telemetri ile guncelle; composite
    # kurallar (Faz 1 compare + Faz 2 agg) bu cache'lerden okur.
    _LIVE.put(str(signal_key), str(device_code or ""), value, now)
    # Gecmis ornekler YALNIZCA ihtiyaci olan sinyaller icin biriktirilir.
    #
    # YASANAN SORUN: her telemetri okumasi, composite/agg kurali olsun olmasin,
    # (sinyal, cihaz) basina 5000 orneklik bir deque'e yaziliyordu. Ornekler
    # ise yalnizca `kind == "agg"` terimlerinde okunuyor — katalogdaki 175
    # sinyalin cogunun boyle bir kurali yok.
    #
    # 200 cihaz x 20 aktif sinyal = 4000 anahtar; her biri ~14 saatte
    # maxlen=5000'e doyar -> ~20M tuple, kabaca 1.5-2 GB. Container tavani
    # 512M oldugu icin alarm-service birkac saatte OOM-kill yiyordu.
    # `restart: unless-stopped` onu geri kaldiriyor ama `_STATE` (aktif alarm
    # durumu) BELLEKTE oldugu icin sifirlaniyor: acik alarmlar yeniden "yeni
    # alarm" olarak POST ediliyor — mukerrer bildirim seli, birkac saatte bir.
    # TTL temizligi bunu cozmuyordu; o yalnizca 24 saattir veri gelmeyen
    # anahtarlari atiyor, aktif cihazlarda hicbir sey serbest birakmiyor.
    if _CACHE.needs_samples(str(signal_key)):
        _SAMPLES.put(str(signal_key), str(device_code or ""), value, now)

    _kod = str(device_code) if device_code else None
    for rule in _CACHE.rules_for(str(signal_key), _kod, _CACHE.device_model(_kod)):
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
                agg_lookup=lambda sk, dc, fn, win, arg, _now=now: _SAMPLES.lookup(
                    sk, dc, fn, win, arg, now=_now
                ),
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
                # SIGNAL_KEY KURALIN ANCHOR'I OLMALI — tetikleyen telemetrininki
                # DEGIL. Backend acik alarmi (rule_id, device_code, signal_key)
                # ile buluyor; clear tarafi zaten `rule.signal_key` gonderiyor.
                # Composite kuralda tetikleyen terim anchor'dan FARKLI bir sinyal
                # olabildigi icin ikisi ayrisiyordu ve alarm ASLA kapanmiyordu:
                # raise "master.current" ile aciliyor, clear "master.voltage"
                # ile geliyor, backend eslesme bulamiyordu. Basit kurallarda
                # ikisi zaten ayni (rules_for o signal_key ile eslesiyor), yani
                # bu degisiklik orada davranisi degistirmez.
                signal_key=rule.signal_key,
                threshold=rule.threshold,
                operator=rule.comparator,
                produces_fault=rule.produces_fault,
            )
            # Backend POST + RabbitMQ publish _BackendNotifier thread'inde
            # kosar — bu dongu asyncio event loop'unun icinde ve BLOKLANMAMALI
            # (senkron HTTP burada kosarken 401 cihazda prio kuyrugu birikti).
            # "Once backend POST, sonra RabbitMQ" sirasi worker icinde korunur.
            # geri_al: backend POST'u kalici basarisiz olursa aktiflik isareti
            # geri alinir, boylece bir sonraki telemetri alarmi YENIDEN dener.
            # Bu olmadan alarm sessizce ve kalici kayboluyordu.
            _NOTIFIER.submit_raise(
                alarm_payload,
                rule_id=rule.id,
                geri_al=lambda _k=key: _STATE.set_active(_k, False),
            )
            print(
                "alarm-service-raised "
                f"rule_id={rule.id} signal={rule.signal_key} dev={device_code} value={value}"
            )
        elif not should_be_active:
            # Clear YALNIZCA gercek gecis (prev_active=True -> False) icin
            # gonderilir. Eski "drift clear" yolu (alarm hic aktif olmamis
            # her (kural x cihaz) anahtari icin periyodik idempotent POST)
            # KALDIRILDI: 401 cihazda uretim hizi HTTP gonderim hizini
            # asiyor, kuyruk sinirsiz buyuyordu ve her POST backend'de DB
            # sorgusu demekti. Restart/state-drift telafisi zaten backend'in
            # isi: `alarm_reconciliation.py` 30 sn'de bir DB'deki TUM acik
            # alarmlari son telemetriye karsi degerlendirip kendisi kapatir
            # (alarm-service tamamen down olsa bile). Ayni isi iki yerde,
            # ustelik O(kural x cihaz) maliyetle yapmak gereksizdi.
            was_active = prev_active
            if was_active:
                _STATE.set_active(key, False)
                _NOTIFIER.submit_clear(
                    rule_id=rule.id,
                    rule_title=rule.name,
                    device_code=str(device_code) if device_code else None,
                    source_gateway=payload.get("source_gateway"),
                    signal_key=rule.signal_key,
                    was_active=True,
                )
            _STATE.clear_pending(key)


class _RabbitAlarmPublisher:
    """Alarm.created mesajlarini RabbitMQ'ya publish eden thread-safe wrapper.

    Telemetri tarafi JetStream'e gecti ama alarm.created akisi RabbitMQ'da
    kaliyor (notification-worker buradan tuketiyor, DLX/retry semantiki
    daha olgun). Bagklanti dustugunde otomatik reconnect; publish ederken
    lazy connect.
    """

    def __init__(self, url: str, exchange: str, dlx_exchange: str) -> None:
        self.url = url
        self.exchange = exchange
        self.dlx_exchange = dlx_exchange
        self._connection = None
        self._channel = None
        self._lock = Lock()

    def _ensure_channel(self):
        if self._connection is None or self._connection.is_closed:
            self._connection = pika.BlockingConnection(pika.URLParameters(self.url))
            self._channel = None
        if self._channel is None or self._channel.is_closed:
            ch = self._connection.channel()
            ch.exchange_declare(exchange=self.exchange, exchange_type="topic", durable=True)
            ch.exchange_declare(exchange=self.dlx_exchange, exchange_type="topic", durable=True)
            self._channel = ch
        return self._channel

    def publish(self, alarm_payload: dict) -> None:
        with self._lock:
            try:
                ch = self._ensure_channel()
                _publish_alarm(ch, alarm_payload)
            except Exception:
                # Reset connection, raise — caller decides retry (rules loop
                # zaten exception'i log'lar ve devam eder).
                try:
                    if self._connection is not None:
                        self._connection.close()
                except Exception:  # noqa: BLE001
                    pass
                self._connection = None
                self._channel = None
                raise

    def close(self) -> None:
        with self._lock:
            try:
                if self._connection is not None and not self._connection.is_closed:
                    self._connection.close()
            except Exception:  # noqa: BLE001
                pass
            self._connection = None
            self._channel = None


_ALARM_PUBLISHER: _RabbitAlarmPublisher | None = None


def _publish_alarm_to_rabbitmq(alarm_payload: dict) -> None:
    """`_process_rules_for_payload` icinden cagrilan adapter — channel yerine
    thread-safe publisher singleton kullaniyor."""
    global _ALARM_PUBLISHER
    if _ALARM_PUBLISHER is None:
        _ALARM_PUBLISHER = _RabbitAlarmPublisher(RABBIT_URL, EXCHANGE, DLX_EXCHANGE)
    _ALARM_PUBLISHER.publish(alarm_payload)


_stop_event = threading.Event()


async def _kalp_dongusu() -> None:
    """Olay dongusu yasadigi surece atar (bkz. watchdog.py).

    ALARMA DEGIL olay dongusune bagli: alarm uretmeyen sakin bir gece
    saglikli servisi oldurmemeli.
    """
    while True:
        await asyncio.sleep(5)
        watchdog.kalp_at()


async def _tek_hat_bosaldi_mi(js) -> bool:  # noqa: ANN001
    """Eski TEK HAT durable'inda islenmemis mesaj kaldi mi?

    Karar SUNUCUNUN sayaclarindan alinir (`num_pending` + `num_ack_pending`).
    Teslim edilmis ama ack'lenmemis mesajlar `num_pending`de GORUNMEZ; ikisi
    birlikte sorulmazsa "bitti" denip degerlendirilmemis olcumler terk
    edilirdi.
    """
    try:
        bilgi = await js.consumer_info(NATS_STREAM_NORMALIZED, NATS_DURABLE)
    except Exception:  # noqa: BLE001  (NotFoundError dahil — durable yok = bitti)
        return True
    bekleyen = int(getattr(bilgi, "num_pending", 0) or 0)
    ack_bekleyen = int(getattr(bilgi, "num_ack_pending", 0) or 0)
    return not (bekleyen or ack_bekleyen)


async def _hatlari_kur(js, cb):  # noqa: ANN001
    """Alarm degerlendirmesini hatlara baglar. Donus: (subs, hat_adlari).

    ALARM URETEN YOL ONCELIKLI HATTA. Onceden TEK durable tum akisi
    geziyordu: bir ariza bayragi, onunde bekleyen on binlerce ANALOG
    olcumunun ARDINDAN degerlendiriliyordu. Ariza alarminin gecikmesi
    konfor degil DOGRULUK meselesi.

    UC ABONELIK:
      prio    dijital/durum + ariza belirleyen her sey. Kucuk
              `max_ack_pending` ile: bu deger sunucunun teslim edip ack
              bekledigi tavandir, buyuk tutmak oncelikli hattin ICINE kendi
              kuyrugunu kurmak olurdu.
      bulk    analog. TERK EDILMEZ — analog esik kurallari ve composite
              `agg` terimleri buradan beslenir; terk edilseydi analog
              tabanli her alarm SESSIZCE susardi. Yalnizca sirasi dusuk.
      legacy  KALDIRILDI (2026-08-04): 4 token'li sinifsiz konuya artik
              hicbir uretici basmiyor; durable startup'ta silinir (asagida).

    ESKI TEK HAT DURABLE'I ONCE BOSALTILIR: icinde HENUZ DEGERLENDIRILMEMIS
    olcumler olabilir ve ayrima gecmek onu terk etmek demektir. Bosalana
    kadar bugunku davranis (tek abonelik) aynen surer.
    """
    from nats.js.api import ConsumerConfig, DeliverPolicy

    async def _abone(subject: str, durable: str, ack_pending: int):
        return await js.subscribe(
            subject=subject,
            durable=durable,
            cb=cb,
            manual_ack=True,
            config=ConsumerConfig(
                durable_name=durable,
                deliver_policy=DeliverPolicy.NEW,
                ack_wait=60,
                max_ack_pending=ack_pending,
                max_deliver=MAX_DELIVER,
                filter_subject=subject,
            ),
        )

    if not await _tek_hat_bosaldi_mi(js):
        print(
            f"alarm-service-hat-ayrimi-erteleniyor durable={NATS_DURABLE} — "
            "eski hatta degerlendirilmemis olcum var, once o bosaltilacak"
        )
        return [await _abone(NATS_SUBJECT_NORMALIZED, NATS_DURABLE, 10000)], ["karma"]

    # LEGACY HAT KALDIRILDI (2026-08-04): 4 token'li sinifsiz eski konuya
    # artik HICBIR uretici basmiyor (tag-engine 1.1+ her zaman sinif token'i
    # ekler; persist hatlari da yalnizca 5 token dinler — 4 token'li akis
    # platform genelinde zaten olu). Sahada legacy durable 7.9M "kacirilmis"
    # sayaciyla stream'in first_seq'ini geride tutup disk baskisi yaratti.
    # Durable asagida silinir; abonelik ACILMAZ.
    try:
        await js.delete_consumer(NATS_STREAM_NORMALIZED, NATS_DURABLE_LEGACY)
        print(f"alarm-service-legacy-durable-silindi durable={NATS_DURABLE_LEGACY}")
    except Exception:  # noqa: BLE001  (yoksa NotFoundError — normal)
        pass

    subs = []
    adlar = []
    for sinif, subject, durable, ack_pending in (
        ("prio", NATS_SUBJECT_PRIO, NATS_DURABLE_PRIO, ACK_PENDING_PRIO),
        ("bulk", NATS_SUBJECT_BULK, NATS_DURABLE_BULK, ACK_PENDING_BULK),
    ):
        subs.append(await _abone(subject, durable, ack_pending))
        adlar.append(sinif)
        print(
            f"alarm-service-hat sinif={sinif} subject={subject} "
            f"durable={durable} max_ack_pending={ack_pending}"
        )
    # Eski durable BOSTU ve artik uc yeni durable akisi tasiyor. Birakilirsa
    # kimse tuketmez, `num_pending` sinirsiz buyur ve bir surum geri
    # donusunde o birikimin tamami yeniden degerlendirilerek bayat alarm
    # uretir. Silinince eski kod onu `DeliverPolicy.NEW` ile yeniden yaratir.
    try:
        await js.delete_consumer(NATS_STREAM_NORMALIZED, NATS_DURABLE)
        print(f"alarm-service-eski-durable-silindi durable={NATS_DURABLE}")
    except Exception:  # noqa: BLE001  (yoksa NotFoundError — normal)
        pass
    return subs, adlar


async def _consume_jetstream() -> None:
    """Telemetry.normalized JetStream'i dinler, kural motoruna besler.

    Reconnect: NATS gelmediyse / dustuyse expo backoff. Durable consumer ile
    process restart'inda kaldigi yerden devam.
    """
    asyncio.create_task(_kalp_dongusu())
    backoff = 2
    while not _stop_event.is_set():
        nc = None
        try:
            nc = await nats.connect(
                servers=[NATS_URL],
                max_reconnect_attempts=-1,
                reconnect_time_wait=2,
                name="e1-alarm-service",
                # None = TLS kapali (varsayilan). Bkz. _nats_tls_context.
                tls=_nats_tls_context(),
            )
            js = nc.jetstream()

            async def _on_message(msg) -> None:  # noqa: ANN001
                try:
                    payload = json.loads(msg.data.decode("utf-8"))
                    # NOT: Otomatik "Haberleşme arızası" alarmı uretmiyoruz —
                    # kullanici kendi kurallari uzerinden bu durumu tanimliyor.
                    # Sadece tanimli alarm kurallarini degerlendir. publish-
                    # alarm RabbitMQ'da; rules loop _publish_alarm_to_rabbitmq
                    # adapter'ini cagiriyor olacak.
                    _process_rules_for_payload_jetstream(payload)
                    await msg.ack()
                except Exception as ex:  # noqa: BLE001
                    # Son denemede DLQ'ya tasi; aksi halde nak ile redeliver.
                    num_delivered = int(
                        getattr(getattr(msg, "metadata", None), "num_delivered", 1) or 1
                    )
                    is_terminal = num_delivered >= MAX_DELIVER
                    print(
                        f"alarm-service-failed error={ex} "
                        f"delivery={num_delivered}/{MAX_DELIVER} terminal={is_terminal}"
                    )
                    if is_terminal:
                        try:
                            orig_subject = getattr(msg, "subject", "unknown")
                            dlq_subject = f"{SUBJECT_DLQ_PREFIX}.{orig_subject}"
                            dlq_headers = {
                                "X-DLQ-Reason": "max_deliver_exceeded",
                                "X-DLQ-Service": "alarm-service",
                                "X-DLQ-Original-Subject": orig_subject,
                                "X-DLQ-Error": str(ex)[:500],
                                "X-DLQ-Delivery-Count": str(num_delivered),
                            }
                            await js.publish(dlq_subject, msg.data, headers=dlq_headers)
                            print(f"alarm-service-dlq subject={dlq_subject} error={ex}")
                            await msg.ack()
                        except Exception:  # noqa: BLE001
                            try:
                                await msg.nak()
                            except Exception:  # noqa: BLE001
                                pass
                    else:
                        try:
                            await msg.nak()
                        except Exception:  # noqa: BLE001
                            pass

            # ALARM URETEN YOL ONCELIKLI HATTA. Consumer parametreleri
            # (ack_wait, max_ack_pending, max_deliver) hat basina
            # `_hatlari_kur` icinde belirlenir.
            subs, hat_adlari = await _hatlari_kur(js, _on_message)
            print(f"alarm-service-running hatlar={hat_adlari} url={NATS_URL}")
            backoff = 2
            while not _stop_event.is_set():
                await asyncio.sleep(1)
                # Tek hat modunda kaldiysak (eski durable henuz bosalmadi)
                # periyodik olarak yeniden dene; bosalinca ayrima gecilir.
                if len(subs) == 1 and await _tek_hat_bosaldi_mi(js):
                    for sub in subs:
                        await sub.unsubscribe()
                    subs, hat_adlari = await _hatlari_kur(js, _on_message)
                    print(f"alarm-service-hat-ayrimi hatlar={hat_adlari}")
            for sub in subs:
                await sub.unsubscribe()
        except Exception as ex:  # noqa: BLE001
            if _stop_event.is_set():
                break
            print(f"alarm-service-reconnect error={ex} backoff={backoff}s url={NATS_URL}")
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, 60)
        finally:
            if nc is not None:
                try:
                    await nc.drain()
                except Exception:  # noqa: BLE001
                    pass


def _process_rules_for_payload_jetstream(payload: dict) -> None:
    """JetStream wrapper: eski _process_rules_for_payload imzasi `channel`
    aliyordu (RabbitMQ). Yeni mimaride publish ayri bir publisher uzerinden.
    Bu fonksiyon publish cagri yerini soyutluyor: _publish_alarm calls
    _publish_alarm_to_rabbitmq via channel=None marker.
    """
    # Mevcut _process_rules_for_payload'i bozulmadan kullanmak icin
    # channel parametresine "publisher proxy" gibi davranan bir obje versek
    # de refactor genis olur. Burada in-place: payload uzerinde once kural
    # eval edip alarm uretilirse RabbitMQ'ya publish'i adapter'la yapariz.
    # _publish_alarm'i monkey-patch etmeden, yine `channel` arguman tipi
    # disinda davranan minimal bir proxy verelim.
    class _ChannelProxy:
        def basic_publish(self, **_kwargs) -> None:
            # Hicbir zaman buraya dusmemeli — _publish_alarm imzasi degisti
            # (artik basic_publish'i RabbitAlarmPublisher uzerinden cagiriyoruz).
            # Backward compat icin sessiz.
            pass

    _process_rules_for_payload(_ChannelProxy(), payload)


def _validate_required_secrets() -> None:
    """Worker baslamadan once placeholder/bos secret tespit edip fail-fast yap.

    Operator env unutursa worker default 'change-me-internal-token' ile devam
    eder, backend 401 doner, worker sonsuz retry → log spam + sessiz bozulma.
    Bunun yerine clear error mesajla cik ve operator'i uyar.
    """
    _PLACEHOLDER_PREFIXES = ("change-me", "please-change-me", "change-this", "your-secret")

    def _is_placeholder(value: str) -> bool:
        v = (value or "").strip().lower()
        return (not v) or v.startswith(_PLACEHOLDER_PREFIXES)

    errors: list[str] = []
    if _is_placeholder(INTERNAL_SERVICE_TOKEN):
        errors.append(
            "INTERNAL_SERVICE_TOKEN .env'de set edilmemis veya placeholder "
            "('change-me-*'); backend 401 doner ve worker calismaz."
        )
    if not RABBIT_URL.strip():
        errors.append("RABBITMQ_URL .env'de set edilmemis; mesaj kuyruguna baglanilamaz.")
    if errors:
        msg = "\n  - ".join(errors)
        print(f"alarm-service ZORUNLU KONFIGURASYON EKSIK:\n  - {msg}", flush=True)
        raise SystemExit(2)


def main() -> None:
    _validate_required_secrets()
    # Esik 60 sn: atis 5 sn'de bir (12 kacirilmis atis). NATS yeniden
    # baglanma backoff'u sirasinda da olay dongusu calistigi icin atis surer.
    watchdog.baslat(60.0, servis="alarm-service")
    _start_health_server()
    _NOTIFIER.start()
    stop_event = Event()
    refresh_thread = Thread(target=_rules_refresh_loop, args=(stop_event,), daemon=True)
    refresh_thread.start()

    # Ilk kural cekimini blokla (en fazla 15 sn)
    for _ in range(30):
        if _CACHE.is_ready():
            break
        time.sleep(0.5)

    print(f"alarm-service-starting rules_ready={_CACHE.is_ready()}")

    def _signal_handler(signum, _frame):  # noqa: ANN001
        print(f"alarm-service-shutdown signal={signum}")
        _stop_event.set()
        stop_event.set()

    import signal as _signal

    _signal.signal(_signal.SIGINT, _signal_handler)
    _signal.signal(_signal.SIGTERM, _signal_handler)

    try:
        asyncio.run(_consume_jetstream())
    finally:
        # Once gonderim kuyrugu bosaltilir (bekleyen raise/clear'lar gitsin),
        # sonra RabbitMQ baglantisi kapanir.
        _NOTIFIER.stop()
        if _ALARM_PUBLISHER is not None:
            _ALARM_PUBLISHER.close()
        print("alarm-service-stopped")


if __name__ == "__main__":
    main()
