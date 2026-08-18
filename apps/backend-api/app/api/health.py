"""Health endpoint'i — gercek bagimliliklari probe eder.

Endpoint'ler:
  * GET /health         : Backwards-compatible (default). Gercek probe.
                           Postgres saglikli degilse 503; NATS/JetStream/
                           RabbitMQ dustuyse 200 + `status="degraded"` +
                           `degraded_reasons`. Operator / docker-compose
                           healthcheck buradan bilgi alir. Yanit body'sinde
                           her bagimlilik detayli durumu raporlar.
                           (Kuyruk arizasi arayuzu kapatmaz — bkz.
                           `_build_health_body` docstring'i.)
  * GET /health/live    : Liveness probe (k8s). Sadece process up mu —
                           dependency check YOK. 200 doner.
  * GET /health/ready   : Readiness probe. /health ile esdeger; dependency
                           saglikli olmadan trafik almasin.

Tasarim notu: cevap suresi <500ms olmali; her probe'a kısa timeout.
"""

from __future__ import annotations

import logging
import socket
import time
from urllib.parse import urlparse

from fastapi import APIRouter, Depends, status
from fastapi.responses import JSONResponse

from app.api.deps import require_roles
from app.models.enums import UserRole

# Health endpoint'lerinin bir alt grubu (ws-stats, dlq) hassas telemetri
# dondurur — anonim leak engellensin. /health, /health/live, /health/ready
# probe icin auth'suz kalir.
_require_engineer_or_installer = require_roles([UserRole.INSTALLER, UserRole.ENGINEER])
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.core.config import settings
from app.db.session import get_db

router = APIRouter(prefix="/health", tags=["health"])
logger = logging.getLogger(__name__)


def _probe_db(db: Session) -> tuple[bool, str | None, float]:
    """Postgres SELECT 1 — gercek query, baglantilik kontrolu yetmez."""
    started = time.monotonic()
    try:
        db.execute(text("SELECT 1"))
        return True, None, (time.monotonic() - started) * 1000
    except Exception as exc:  # noqa: BLE001
        return False, str(exc)[:200], (time.monotonic() - started) * 1000


def _probe_tcp(url: str, *, default_port: int, timeout: float = 1.0) -> tuple[bool, str | None, float]:
    """URL'den host:port cikar ve TCP connect dene.

    AMQP `connect` ve credential dogrulamasi pahali; basit TCP probe
    broker'in up oldugunu yeterince soyler. RabbitMQ icin 5672, NATS
    icin 4222 default.
    """
    started = time.monotonic()
    try:
        parsed = urlparse(url)
        host = parsed.hostname or "localhost"
        port = parsed.port or default_port
        with socket.create_connection((host, port), timeout=timeout):
            return True, None, (time.monotonic() - started) * 1000
    except Exception as exc:  # noqa: BLE001
        return False, str(exc)[:200], (time.monotonic() - started) * 1000


def _probe_jetstream() -> tuple[bool, str | None]:
    """Backend startup'ta start_bus_if_enabled() singleton bus olusturur;
    `is_ready` flag'i true ise NATS bagli + JetStream context hazir demek."""
    try:
        from app.services.jetstream_bus import get_bus

        bus = get_bus()
        if bus is None:
            return False, "bus_not_initialized"
        # is_ready property (call yapma, attribute oku)
        if not bus.is_ready:
            return False, "bus_not_ready"
        return True, None
    except Exception as exc:  # noqa: BLE001
        return False, str(exc)[:200]


def _build_health_body(db: Session) -> tuple[dict, int]:
    """Tum probe'lari calistir; en kotu durumu HTTP status'a yansit.

    Probe stratejisi:
      * DB fail → 503 (critical; hicbir istek anlamli sonuc uretemez)
      * NATS / JetStream fail → 200 + degraded
      * RabbitMQ fail → 200 + degraded

    NEDEN NATS ARTIK KRITIK DEGIL:
    Kuyruk cokerse TELEMETRI AKISI durur; ama arayuz, giris, yetkilendirme,
    ayarlar, gecmis veri, alarm/ariza listesi, yedekleme ve uzaktan bakim
    calismaya devam eder. Buna ragmen 503 dondurmek compose zincirini
    kilitliyordu: `frontend-web` -> `depends_on: backend-api: service_healthy`
    (docker-compose.yml) oldugu icin backend saglikli sayilmadan ARAYUZ HIC
    BASLAMIYOR, yani 80 portunda hicbir sey olmuyordu. Sonuc: NATS'taki tek
    bir yanlis yapilandirma (or. yarim uygulanmis TLS) tum cihazi karartiyor
    ve kimsenin basinda olmadigi bir sahada teshis edilemez hale geliyordu.

    Kuyruk arizasi cihazi karartmamali. Durum GIZLENMIYOR: HTTP 200 doner
    ama govde `status="degraded"` ve `degraded_reasons` ile hangi bagimliligin
    dustugunu acikca soyler; Sistem Durumu ekrani ve uzaktan izleme bunu
    okur. Restart da dogru karar degildi zaten — backend'i yeniden baslatmak
    NATS'i duzeltmez.
    """
    db_ok, db_err, db_ms = _probe_db(db)
    # Sema uyumlulugu SALT OKUNUR bir bayraktir (acilista bir kez olculur;
    # bkz. app/db/schema_guard.py). Burada DB'ye ek sorgu ATILMAZ.
    from app.db import schema_guard

    schema_ok, schema_sebep = schema_guard.hazir_mi()
    js_ok, js_err = _probe_jetstream()
    nats_ok, nats_err, nats_ms = _probe_tcp(settings.nats_url, default_port=4222)
    rmq_ok, rmq_err, rmq_ms = _probe_tcp(settings.rabbitmq_url, default_port=5672)

    deps = {
        "database": {
            "ok": db_ok,
            "latency_ms": round(db_ms, 1),
            **({"error": db_err} if db_err else {}),
        },
        "schema": {
            "ok": schema_ok,
            "expected": schema_guard.DURUM.get("beklenen"),
            "actual": schema_guard.DURUM.get("gercek"),
            **({"error": schema_sebep} if not schema_ok else {}),
        },
        "nats_tcp": {
            "ok": nats_ok,
            "latency_ms": round(nats_ms, 1),
            **({"error": nats_err} if nats_err else {}),
        },
        "jetstream_bus": {
            "ok": js_ok,
            **({"error": js_err} if js_err else {}),
        },
        "rabbitmq_tcp": {
            "ok": rmq_ok,
            "latency_ms": round(rmq_ms, 1),
            **({"error": rmq_err} if rmq_err else {}),
        },
    }

    # Rol + liderlik: arka plan islerini KIMIN calistirdigi gorunur olmali.
    #
    # Ayrik kurulumda (api + worker) sessiz bir ariza mumkun: worker
    # container'i ayaga kalkmazsa HTTP saglikli gorunmeye devam eder ama
    # telemetri yazilmaz, alarm uretilmez, yedek alinmaz. Bu alan olmadan
    # bunu ancak veri eksikliginden fark ederdiniz.
    #
    # HTTP DURUMUNU ETKILEMEZ: `api` rolundeki bir surec zaten bilerek lider
    # degildir; 503 dondurmek saglikli bir surece trafigi kestirirdi.
    from app.core.service_role import leader as _leader

    background = _leader.status()

    # Kritik: Postgres ERISILEBILIR olmali VE sema bu imajla UYUMLU olmali.
    #
    # Sema neden kritik: uyumsuz semayla acilmak "yesil yalan"dir — surec
    # saglikli gorunur, ilk sorguda patlar. Eski davranis daha da kotusuydu:
    # acilista `create_all` + ~124 DDL ile eksigi SESSIZCE tamamlamak.
    #
    # Pratikte bu dal Docker yolunda ULASILMAZ: komut `migrate_db && uvicorn`
    # zinciri ve `migrate_db` sema tasiyamazsa NON-ZERO ile biter, uvicorn hic
    # baslamaz. Buradaki kontrol, migration'i atlayan elle kurulumlar ve
    # yarim kalmis bir tasima icin son emniyet kemeri.
    kritik = [ad for ad, ok in (("database", db_ok), ("schema", schema_ok)) if not ok]
    if kritik:
        if not schema_ok:
            logger.error("health_unhealthy sema uyumsuz: %s", schema_sebep)
        body = {
            "status": "unhealthy",
            "dependencies": deps,
            "background": background,
            "degraded_reasons": kritik,
        }
        return body, status.HTTP_503_SERVICE_UNAVAILABLE

    degraded_reasons = [
        name
        for name, ok in (("nats_tcp", nats_ok), ("jetstream_bus", js_ok), ("rabbitmq_tcp", rmq_ok))
        if not ok
    ]
    if degraded_reasons:
        # Sessizce degraded kalmak da bir ariza modudur: kimse /health'e
        # bakmiyorsa telemetri gunlerce akmayabilir. Log'a yaz ki journalctl
        # ve saha teshisi bunu gorsun.
        logger.warning("health_degraded reasons=%s", ",".join(degraded_reasons))
        body = {
            "status": "degraded",
            "dependencies": deps,
            "background": background,
            "degraded_reasons": degraded_reasons,
        }
        return body, status.HTTP_200_OK

    body = {"status": "ok", "dependencies": deps, "background": background, "degraded_reasons": []}
    return body, status.HTTP_200_OK


@router.get("")
def healthcheck(db: Session = Depends(get_db)):
    """Backwards-compatible health endpoint — readiness ile esdeger.

    docker-compose healthcheck ve uzaktan monitoring icin gercek probe.
    Dependency saglikli olmadan 503 doner; orchestrator container'i
    restart edebilir veya LB trafigi durdurabilir.
    """
    body, status_code = _build_health_body(db)
    return JSONResponse(content=body, status_code=status_code)


@router.get("/live")
def liveness():
    """Liveness probe (k8s `livenessProbe`). Process up mu — dependency
    check YOK. DB/NATS down olsa bile process restart EDILMEZ; sadece
    `/health` 503 doner ve readiness/LB devre disi birakir."""
    return {"status": "ok"}


@router.get("/ready")
def readiness(db: Session = Depends(get_db)):
    """Readiness probe (k8s `readinessProbe`) — `/health` ile esdeger.

    Ayri endpoint olmasinin sebebi: liveness ve readiness farkli
    semantikler (liveness=restart, readiness=trafik yonlendirme).
    """
    body, status_code = _build_health_body(db)
    return JSONResponse(content=body, status_code=status_code)


@router.get("/ws-stats")
def ws_broadcaster_stats(
    # ENGINEER+ yetkisi: subscriber count, NATS stream isimleri, slow consumer
    # detaylari operator telemetrisidir — anonim erisilirse recon ipucu olur.
    _user=Depends(_require_engineer_or_installer),
):
    """WebSocket broadcaster istatistikleri — slow-consumer izlemek icin.

    Donen alanlar:
      * active_subscribers: bagli WS client sayisi
      * total_received_messages: tum client'lar icin denemeler toplami
      * total_dropped_messages: queue full olunca drop edilen mesaj sayisi
      * drop_ratio_percent: dropped / received yuzdesi (operator alarm
        esigi: >5% slow consumer var demektir; frontend zaten reconnect
        ile telafi eder ama UX bozuk olabilir).
      * top_droppers: en cok drop yapan 5 client (filter ve sayilar).
    """
    try:
        from app.services.ws_broadcaster import broadcaster

        return broadcaster.stats()
    except Exception:  # noqa: BLE001
        # Hata detayini caller'a sizdirma; sunucu log'una yaz.
        logging.getLogger(__name__).exception("ws_broadcaster_stats_failed")
        return {"error": "stats unavailable"}


@router.get("/dlq")
def dlq_status(
    # DLQ stream isimleri, mesaj subject pattern'i, byte sayilari operator
    # telemetrisi. Anonim leak engellensin.
    _user=Depends(_require_engineer_or_installer),
):
    """DLQ stream durumu — operator poison mesaj sayisini hizlica gorebilir.

    Worker'lar max_deliver'a takilan mesajlari TELEMETRY_DLQ stream'ine
    (subject: e1.dlq.>) tasiyor. Bu endpoint stream'in mesaj sayisini ve
    son mesaj subject pattern'ini doner. Aktif izleme:
      - messages > 0 = inceleme gerekir (poison payload, kod hatasi vs.)
      - bytes buyuk: stream max_age'e gore retention; eski mesajlar kendi
        kendine silinir.

    DLQ stream yoksa (NATS olusmadi) {"available": false} doner.
    """
    try:
        from app.services.jetstream_bus import get_bus

        bus = get_bus()
        if bus is None or not bus.is_ready:
            return {"available": False, "reason": "jetstream_bus_not_ready"}

        # Stream info'yu run_coroutine_threadsafe ile cek; bus'in kendi
        # asyncio loop'unda calistirilir.
        import asyncio

        async def _fetch() -> dict:
            try:
                info = await bus._js.stream_info(bus._stream_dlq)
                state = info.state
                return {
                    "available": True,
                    "stream": bus._stream_dlq,
                    "messages": int(getattr(state, "messages", 0) or 0),
                    "bytes": int(getattr(state, "bytes", 0) or 0),
                    "first_seq": int(getattr(state, "first_seq", 0) or 0),
                    "last_seq": int(getattr(state, "last_seq", 0) or 0),
                }
            except Exception as exc:  # noqa: BLE001
                return {"available": False, "reason": f"stream_info_failed: {exc}"}

        loop = bus._loop
        if loop is None:
            return {"available": False, "reason": "loop_not_running"}
        future = asyncio.run_coroutine_threadsafe(_fetch(), loop)
        return future.result(timeout=3)
    except Exception as exc:  # noqa: BLE001
        return {"available": False, "reason": str(exc)[:200]}
