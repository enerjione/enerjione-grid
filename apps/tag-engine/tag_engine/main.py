"""Tag-engine — NATS JetStream uzerinde ham telemetri akisini normalize eder.

Akis:
  e1.telemetry.raw.<gw>          (gateway'lerin yaylinladigi ham)
    -> tag-engine consume
    -> normalize (quality, status, processed_at)
    -> publish: e1.telemetry.normalized.<gw>.<sinif>
       (alarm-service, kalicilastirma, iec104/modbus-outbound buradan tuketir)

IKI HAT — konudaki son token
----------------------------
`<sinif>` iki degerden biridir:
  prio  ONCELIKLI hat. Dijital/durum sinyalleri ve ariza belirleyen her sey
        (ariza bayraklari, yon, sayaclar, Class 1 analoglar, katalogda
        olmayanlar). Kendi durable'iyla tuketilir.
  bulk  TOPLU hat. Yalnizca surekli taranan Class 2 analog olcumler.

Karar KATALOGDAN gelir (bkz. katalog.py) — kodda sinyal adi listesi YOK.
Sinif belirlenemiyorsa (katalog henuz gelmedi, sinyal katalogda yok, tip
taninmiyor) ONCELIKLI hat secilir: siniflandiramadigimiz bir sinyali
geciktirmek, gereksiz hizlandirmaktan risklidir.

KONU DEGISIMI ESKI TUKETICIYI KIRMAZ: subject 4 token'dan 5 token'a cikar,
stream filtresi `e1.telemetry.normalized.>` (`>` = BIR VEYA DAHA FAZLA token)
bunu aynen yakalar. NATS izin listesi de ayni kok altinda
(`e1.telemetry.normalized.>`), yani sahadaki `nats-server.conf` yeniden
render EDILMEK ZORUNDA DEGIL — aksi halde oncelikli hat izin ihlaliyle
sessizce olurdu (bkz. update.sh conf imza kontrolu).

RabbitMQ'dan tamamen kaldirildi. Telemetri akisi tamamen JetStream uzerinden
ilerler; alarm.created akisi backend tarafinda RabbitMQ'da kalir (tag-engine
onunla ilgilenmez).
"""

import asyncio
import json
import logging
import os
import ssl
import signal
import threading
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, HTTPServer
from threading import Thread
from uuid import uuid4

import nats

from . import katalog, watchdog

# Yapilandirilabilir logging — eski `print()` cagrilari structured log'a
# tasindi. LOG_LEVEL env ile kontrol (INFO/WARNING/ERROR/DEBUG).
logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO").upper(),
    format="%(asctime)s %(levelname)-5s tag-engine %(message)s",
)
logger = logging.getLogger("tag-engine")

NATS_URL = os.getenv("NATS_URL", "nats://localhost:4222")
# Stream isimleri — backend'in olusturdugu stream'lerle ayni olmali.
STREAM_NORMALIZED = os.getenv("NATS_STREAM_TELEMETRY_NORMALIZED", "TELEMETRY_NORMALIZED")
# Consumer subject pattern (incoming) — backend'in TELEMETRY_RAW stream'ine bind.
# Cati 0.x cutover sonrasi `e1.*` prefix kullanilir (gateway+backend ile tutarli).
SUBJECT_RAW = os.getenv("NATS_SUBJECT_TELEMETRY_RAW", "e1.telemetry.raw.>")
# Outgoing subject prefix — konkre subject: e1.telemetry.normalized.<gw>.<sinif>
SUBJECT_NORMALIZED_PREFIX = os.getenv(
    "NATS_SUBJECT_NORMALIZED_PREFIX", "e1.telemetry.normalized"
)
DURABLE_NAME = os.getenv("NATS_TAG_ENGINE_DURABLE", "tag-engine-normalize")
# DLQ subject prefix — max_deliver'a takilan mesajlar buraya gider.
# Subject: e1.dlq.tag-engine.<original-subject>
SUBJECT_DLQ_PREFIX = os.getenv("NATS_SUBJECT_DLQ_PREFIX", "e1.dlq.tag-engine")
MAX_DELIVER = int(os.getenv("NATS_WORKER_MAX_DELIVER", "10"))
HEALTH_HOST = os.getenv("WORKER_HEALTH_HOST", "127.0.0.1")
HEALTH_PORT = int(os.getenv("WORKER_HEALTH_PORT", "8011"))

# Sinyal -> hat sinifi tablosu. `main()` icinde baslatilir; o ana kadar (ve
# katalog cekilemedigi surece) `sinif()` her sinyale ONCELIKLI der, yani
# davranis BUGUNKUNE esit olur: tek hat, karisik trafik.
KATALOG = katalog.onbellek_kur()


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


class _HealthHandler(BaseHTTPRequestHandler):
    def do_GET(self):  # noqa: N802
        if self.path == "/health":
            # ONCEDEN SABIT 200 DONUYORDU. Saglik sunucusu AYRI bir iplikte
            # oldugu icin ana dongu kilitlense de 200 doner ve container
            # "saglikli" gorunurdu — yani tam da yakalamasi gereken arizayi
            # yakalamiyordu. Artik son kalp atisina bakiyor.
            iyi = watchdog.saglikli()
            self.send_response(200 if iyi else 503)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.end_headers()
            self.wfile.write(
                b'{"status":"ok","service":"tag-engine"}' if iyi
                else b'{"status":"stalled","service":"tag-engine"}'
            )
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


def _normalize_quality(quality: str) -> str:
    return (quality or "good").strip().lower()


# CIHAZ seviyesinde "haberlesme yok" demek olan kaliteler.
_OFFLINE_QUALITIES = frozenset({"bad", "offline", "invalid", "comm_lost", "restart"})
# NOKTA seviyesinde gelebilen ama CIHAZI offline SAYMAMASI gereken kaliteler.
# Ayrimi `dnp3_flags`in VARLIGI yapar: alan doluysa gateway 0.5.0+ demektir ve
# kalite tek bir NOKTA icindir ("bu olcum gecersiz"), cihaz pekala ayaktadir.
# Alan yoksa (0.4.x legacy) ayni kelime CIHAZ seviyesindedir ve OFFLINE dogru
# karardir. Backend `map_quality_to_status_scoped` ile AYNI mantik — ikisi
# ayrisirsa arsivdeki durum ile ekrandaki durum celisir.
_POINT_LEVEL_QUALITIES = frozenset({"invalid", "restart", "forced"})


def _durum(quality: str, dnp3_flags) -> str:  # noqa: ANN001
    if dnp3_flags is not None and quality in _POINT_LEVEL_QUALITIES:
        return "online"
    return "offline" if quality in _OFFLINE_QUALITIES else "online"


def _build_processed_payload(payload: dict) -> dict:
    quality = _normalize_quality(str(payload.get("quality", "good")))
    now_iso = datetime.now(timezone.utc).isoformat()
    # KALICILASTIRMA BU AKISTAN BESLENIYOR — asagidaki uc alan DUSURULEMEZ.
    # Dusurulurse belirti sessizdir:
    #   dnp3_flags       -> kapsam bilgisi kaybolur, tek bir noktanin
    #                       referans hatasi TUM CIHAZI offline gosterir
    #   device_event_at  -> telemetry/telemetry_history/telemetry_latest
    #   timestamp_quality   kolonlari sessizce NULL kalir; SOE ve ariza-suresi
    #                       analizi kaybolur
    dnp3_flags = payload.get("dnp3_flags")
    return {
        "message_id": payload.get("message_id") or str(uuid4()),
        "correlation_id": payload.get("correlation_id") or payload.get("message_id") or str(uuid4()),
        "source_gateway": payload.get("source_gateway") or "unknown",
        "device_code": payload.get("device_code"),
        "signal_key": payload.get("signal_key"),
        "signal_data_type": payload.get("signal_data_type"),
        "value": payload.get("value"),
        # DNP3 Group 110 (Octet String) sinyallerinde gateway numeric value
        # alanini None yollar; gercek metin value_string'tedir. Backend'in
        # bu alani persist edebilmesi icin field'i drop etmeden geciriyoruz.
        "value_string": payload.get("value_string"),
        "quality": quality,
        # Ham DNP3 kalite bayraklari. VARLIGI bir surum isaretidir (bkz.
        # _durum ve backend schemas/telemetry.py) — degeri kadar var/yok
        # olmasi da anlamlidir, o yuzden None ise de alan GECER.
        "dnp3_flags": dnp3_flags,
        # Gateway dnp3 adapter'leri "comm_lost" (TCP/link kopuk) ve "restart"
        # (cihaz reboot etti, baseline bekleniyor) kalitelerini de yayinlar.
        # Frontend'de cihazin "offline" gozukmesi icin bu kaliteler de
        # offline olarak isaretlenmelidir; aksi halde gateway'in son iyi
        # degeri tekrar yayinlanmadigi durumda cihaz hala "online" gorunur.
        "status": _durum(quality, dnp3_flags),
        "source_timestamp": payload.get("source_timestamp") or now_iso,
        # Cihazin KENDI olay zamani + o damganin guvenilirligi. Backend bunu
        # ayri kolonlarda saklar; `source_timestamp` (PK/partition kolonu)
        # ile karistirilmamali.
        "device_event_at": payload.get("device_event_at"),
        "timestamp_quality": payload.get("timestamp_quality"),
        "processed_at": now_iso,
    }


_stop_event = threading.Event()


def _install_signal_handlers() -> None:
    def _handler(signum, _frame):  # noqa: ANN001
        logger.info("tag-engine-shutdown signal=%s", signum)
        _stop_event.set()

    signal.signal(signal.SIGINT, _handler)
    signal.signal(signal.SIGTERM, _handler)


async def _run() -> None:
    # Kalp atisi gorevi: olay dongusu yasadigi surece atar. Bekci bunun
    # gecikmesine bakip asili kalan sureci sonlandirir.
    asyncio.create_task(_kalp_dongusu())
    backoff = 2
    while not _stop_event.is_set():
        nc = None
        try:
            nc = await nats.connect(
                servers=[NATS_URL],
                max_reconnect_attempts=-1,
                reconnect_time_wait=2,
                name="e1-tag-engine",
                # None = TLS kapali (varsayilan). Bkz. _nats_tls_context.
                tls=_nats_tls_context(),
            )
            js = nc.jetstream()

            async def _on_message(msg) -> None:  # noqa: ANN001
                try:
                    payload = json.loads(msg.data.decode("utf-8"))
                    processed = _build_processed_payload(payload)
                    if processed["source_gateway"] == "unknown":
                        # Sadece anomali (eksik gateway) durumunda log.
                        logger.warning(
                            "missing_source_gateway msg=%s dev=%s",
                            processed["message_id"],
                            processed["device_code"],
                        )
                    # HAT SECIMI — konudaki son token. Bilinmeyen her durumda
                    # oncelikli hat (bkz. katalog.KatalogOnbellegi.sinif).
                    sinif = KATALOG.sinif(processed.get("signal_key"))
                    out_subject = (
                        f"{SUBJECT_NORMALIZED_PREFIX}."
                        f"{processed['source_gateway']}.{sinif}"
                    )
                    # Nats-Msg-Id: stream-side dedup (2dk pencerede ayni id'yi tek alir).
                    headers = {
                        "Nats-Msg-Id": processed["message_id"],
                        "X-Correlation-Id": str(processed.get("correlation_id") or ""),
                        "source_gateway": str(processed["source_gateway"]),
                        "device_code": str(processed.get("device_code") or ""),
                        "signal_key": str(processed.get("signal_key") or ""),
                        # Tuketici konudaki token'i ayristirmadan sinifi
                        # gorebilsin (DLQ/olay kaydi icin).
                        "X-Signal-Class": sinif,
                    }
                    await js.publish(
                        out_subject,
                        json.dumps(processed, ensure_ascii=False).encode("utf-8"),
                        headers=headers,
                    )
                    await msg.ack()
                except Exception as ex:  # noqa: BLE001
                    # Son denemede DLQ'ya tasi; aksi halde nak ile redeliver.
                    # max_deliver asilinca NATS mesaji sessizce discard eder —
                    # DLQ ile operator gorebilir ve root-cause sonra replay edebilir.
                    num_delivered = int(
                        getattr(getattr(msg, "metadata", None), "num_delivered", 1) or 1
                    )
                    is_terminal = num_delivered >= MAX_DELIVER
                    logger.warning(
                        "tag-engine-failed error=%s delivery=%d/%d terminal=%s",
                        ex,
                        num_delivered,
                        MAX_DELIVER,
                        is_terminal,
                    )
                    if is_terminal:
                        try:
                            orig_subject = getattr(msg, "subject", "unknown")
                            dlq_subject = f"{SUBJECT_DLQ_PREFIX}.{orig_subject}"
                            dlq_headers = {
                                "X-DLQ-Reason": "max_deliver_exceeded",
                                "X-DLQ-Service": "tag-engine",
                                "X-DLQ-Original-Subject": orig_subject,
                                "X-DLQ-Error": str(ex)[:500],
                                "X-DLQ-Delivery-Count": str(num_delivered),
                            }
                            await js.publish(dlq_subject, msg.data, headers=dlq_headers)
                            logger.error(
                                "tag-engine-dlq subject=%s error=%s", dlq_subject, ex
                            )
                            await msg.ack()
                        except Exception:  # noqa: BLE001
                            logger.exception("dlq_publish_failed")
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
            # telemetry_consumer.py'daki ayni konfig). max_ack_pending=10000
            # 600 cihaz x 10 msg/s yukunde suspend onler; max_deliver=10 poison
            # mesaji discard eder; DeliverPolicy.NEW restart sonrasi 7gunluk
            # history replay'i engeller.
            from nats.js.api import ConsumerConfig, DeliverPolicy

            consumer_cfg = ConsumerConfig(
                durable_name=DURABLE_NAME,
                deliver_policy=DeliverPolicy.NEW,
                ack_wait=60,
                max_ack_pending=10000,
                max_deliver=10,
            )
            sub = await js.subscribe(
                subject=SUBJECT_RAW,
                durable=DURABLE_NAME,
                cb=_on_message,
                manual_ack=True,
                config=consumer_cfg,
            )
            logger.info(
                "tag-engine-running url=%s in=%s out=%s.<gw>.{prio|bulk} durable=%s "
                "katalog_yuklendi=%s sinyal=%d",
                NATS_URL,
                SUBJECT_RAW,
                SUBJECT_NORMALIZED_PREFIX,
                DURABLE_NAME,
                KATALOG.yuklendi,
                KATALOG.boyut,
            )
            backoff = 2  # connect basarili
            while not _stop_event.is_set():
                await asyncio.sleep(1)
            await sub.unsubscribe()
        except Exception as ex:  # noqa: BLE001
            if _stop_event.is_set():
                break
            logger.warning(
                "tag-engine-reconnect error=%s backoff=%ds url=%s", ex, backoff, NATS_URL
            )
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, 60)
        finally:
            if nc is not None:
                try:
                    await nc.drain()
                except Exception:  # noqa: BLE001
                    pass


async def _kalp_dongusu() -> None:
    """Olay dongusu yasadigi surece atis yapar.

    ATIS NEYE BAGLI: "mesaj isledim"e DEGIL, "olay dongum hala planlaniyor"a.
    Mesaja baglasaydik, telemetri gelmeyen sakin bir gecede saglikli servis
    oldurulurdu. Bu haliyle yakalanan sey gercek kilitlenme: olay dongusunu
    bloke eden senkron bir cagri (donmeyen bir DB/soket islemi gibi) atisi da
    durdurur ve bekci devreye girer.
    """
    while True:
        await asyncio.sleep(5)
        watchdog.kalp_at()


def main() -> None:
    # Esik 60 sn: atis 5 sn'de bir, yani 12 kacirilmis atis. Yeniden baglanma
    # backoff'u (en fazla 60 sn) sirasinda bile olay dongusu calistigi icin
    # atis surer; esik o yola gore genis tutuldu.
    watchdog.baslat(60.0, servis="tag-engine")
    _start_health_server()
    _install_signal_handlers()
    # Katalog AYRI ipliktedir; olay dongusu backend'in yavasligina bagli
    # kalmaz. Ilk cekim bitene kadar her sinyal oncelikli hatta gider.
    KATALOG.baslat()
    logger.info("tag-engine-starting")
    asyncio.run(_run())
    KATALOG.durdur()
    logger.info("tag-engine-stopped")


if __name__ == "__main__":
    main()
