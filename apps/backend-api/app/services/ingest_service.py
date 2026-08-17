import hashlib
import hmac
import logging
import threading
import time as _time
from datetime import datetime, timezone
from uuid import uuid4

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models.gateway import Gateway
from app.models.gateway_ingest_batch import GatewayIngestBatch
from app.models.telemetry import Telemetry
from app.schemas.telemetry import GatewayTelemetryBatch, TelemetryIn
from app.services.outbox_service import enqueue_outbox_event
from app.services.event_service import record_event

logger = logging.getLogger(__name__)

# STANDART telemetri rotasi gateway -> NATS JetStream'dir; bu HTTP endpoint'i
# YEDEK yoldur. Bir gateway telemetriyi surekli HTTP'den basiyorsa ya NATS'a
# erisemiyordur ya da NATS oncesi compose ile kuruludur (NATS_URL yok/anonim).
# Bu gorunmez kalmamali: HTTP yolu her olcumu outbox'a yazdirip backend'e
# gereksiz yuk bindirir (bkz. outbox_flush_worker). Uyari gateway basina
# rate-limit'lidir; cozum icin gateway "Guncelle" akisi compose'u guncel
# NATS URL'i ile yeniden uretir.
_HTTP_FALLBACK_WARN_INTERVAL_SEC = 600.0
_http_fallback_lock = threading.Lock()
_http_fallback_last_warn: dict[str, float] = {}


def _warn_http_fallback(gateway_code: str, reading_count: int) -> None:
    now = _time.monotonic()
    with _http_fallback_lock:
        last = _http_fallback_last_warn.get(gateway_code)
        if last is not None and now - last < _HTTP_FALLBACK_WARN_INTERVAL_SEC:
            return
        _http_fallback_last_warn[gateway_code] = now
    logger.warning(
        "gateway_http_fallback_ingest gateway=%s batch=%d — telemetri HTTP "
        "yedek yolundan geliyor; standart rota NATS JetStream. Gateway "
        "compose'unda NATS_URL eksik/anonim olabilir; panelden 'Guncelle' "
        "compose'u guncel NATS URL'i ile yeniden uretir.",
        gateway_code,
        reading_count,
    )


def hash_gateway_token(token: str) -> str:
    """Gateway token'in SHA-256 hex digest'i. DB'de plaintext yerine bu
    saklanir; saldirgan DB read-only erisimi alsa bile reverse ile orijinal
    token elde edemez.
    """
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def list_latest_telemetry(
    db: Session, *, visible_device_ids: set[int] | None = None
) -> list[Telemetry]:
    """Son 200 telemetri satiri.

    `visible_device_ids` None DEGILSE sonuc o cihazlara daraltilir. Eskiden
    kapsam suzgeci HIC yoktu: bir operator `/telemetry/latest` ile sorumlu
    olmadigi hatlarin son okumalarini gorebiliyordu — ayni kullanici
    `/devices` cagirdiginda o cihazlar listede bile cikmazken.
    """
    stmt = select(Telemetry)
    if visible_device_ids is not None:
        stmt = stmt.where(Telemetry.device_id.in_(visible_device_ids))
    stmt = stmt.order_by(Telemetry.source_timestamp.desc()).limit(200)
    return list(db.scalars(stmt).all())


def ingest_direct_telemetry(db: Session, readings: list[TelemetryIn]) -> int:
    accepted = _persist_readings(db=db, readings=readings)
    db.commit()
    return accepted


#: Gecis uyarisi icin: gateway basina bir kez logla, 1 Hz poll'da bogmasin.
_f5_legacy_uyarildi: set[str] = set()


def validate_gateway_command_delivery_token(
    gateway: Gateway,
    x_gateway_command_token: str | None,
) -> None:
    """Kuyruklanmis komut duzlemi credential'ini dogrular (F5A).

    Bu, `validate_gateway_token`in YERINE GECMEZ; ONDAN SONRA cagrilir.
    Komut credential'i tek basina gateway kimligi saymaz — iki dogrulama
    birlikte gecmelidir (defence-in-depth).

    SOZLESME
    --------
      gateway.command_delivery_token NULL  -> GECIS: provision edilmemis
          gateway eski davranisi surdurur, komut kanali KESILMEZ.
      gateway.command_delivery_token DOLU  -> STRICT: baslik eksik ya da
          yanlissa REDDEDILIR. `X-Gateway-Token`a GERI DUSULMEZ.

    Baska bir gateway'in komut token'i burada da tutmaz: karsilastirma
    yalnizca ILGILI gateway kaydinin kendi sirriyla yapilir.
    """
    beklenen = (gateway.command_delivery_token or "").strip()
    if not beklenen:
        kod = gateway.code
        if kod not in _f5_legacy_uyarildi:
            _f5_legacy_uyarildi.add(kod)
            logger.warning(
                "gateway_command_plane_legacy gateway=%s — komut duzlemi icin ayri "
                "credential provision EDILMEMIS; `X-Gateway-Token` ile calisiliyor. "
                "Bu GECICI bir durumdur (F5C saha aktivasyonu bekleniyor).",
                kod,
            )
        return

    verilen = (x_gateway_command_token or "").strip()
    if not verilen or not hmac.compare_digest(
        verilen.encode("utf-8"), beklenen.encode("utf-8")
    ):
        # Sir LOGLANMAZ; yalnizca hangi gateway ve hangi eksiklik.
        logger.warning(
            "gateway_command_token_rejected gateway=%s reason=%s",
            gateway.code,
            "missing" if not verilen else "mismatch",
        )
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid gateway command token",
        )


def validate_gateway_token(
    db: Session,
    gateway_code: str,
    x_gateway_token: str | None,
    *,
    allow_inactive: bool = False,
) -> Gateway:
    """Gateway token'i timing-safe dogrula.

    Karsilastirma sirasi:
      1. gateway.token_hash varsa: SHA-256 hash + hmac.compare_digest
         (production yolu — DB'de plaintext yok)
      2. token_hash bos ise legacy: plaintext gateway.token ile
         hmac.compare_digest (eski kayitlar; opportunistic migration —
         basarili dogrulama sonrasi hash kolonu doldurulur, plaintext bir
         sonraki release'te kaldirilir)

    `allow_inactive=True`: is_active=False kayitlari icin 403 atma; caller
    (ornek: `/gateways/{code}/config`) bu durumu 200 + is_active flag ile
    handle eder, gateway polling'ini askiya alir.
    """
    gateway = db.scalar(select(Gateway).where(Gateway.code == gateway_code))
    if gateway is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Gateway not found")
    if not gateway.is_active and not allow_inactive:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Gateway is inactive")
    if not x_gateway_token:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid gateway token")

    provided_hash = hash_gateway_token(x_gateway_token).encode("ascii")
    ok = False
    if gateway.token_hash:
        ok = hmac.compare_digest(provided_hash, gateway.token_hash.encode("ascii"))
    elif gateway.token:
        # Legacy path: plaintext compare (timing-safe)
        ok = hmac.compare_digest(
            (x_gateway_token or "").encode("utf-8"),
            (gateway.token or "").encode("utf-8"),
        )
        if ok:
            # Opportunistic migration: dogrulanmis token'in hash'ini DB'ye yaz
            # ki bir sonraki istek hash yolundan dogrulansin.
            try:
                gateway.token_hash = hash_gateway_token(x_gateway_token)
                db.flush()
            except Exception:  # noqa: BLE001
                pass
    if not ok:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid gateway token")
    return gateway


def ingest_gateway_batch(db: Session, payload: GatewayTelemetryBatch, x_gateway_token: str | None) -> int:
    gateway = validate_gateway_token(db, payload.gateway_code, x_gateway_token)
    if gateway.code != payload.gateway_code:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Gateway code mismatch")

    batch_row = GatewayIngestBatch(
        gateway_code=payload.gateway_code,
        sequence_no=payload.sequence_no,
        sent_at=payload.sent_at,
        created_at=datetime.now(timezone.utc),
    )
    db.add(batch_row)
    try:
        db.flush()
    except IntegrityError:
        db.rollback()
        return 0

    accepted = _persist_readings(db=db, readings=payload.readings)
    _warn_http_fallback(payload.gateway_code, accepted)
    gateway.last_seen_at = datetime.now(timezone.utc)
    record_event(
        db,
        category="telemetry",
        event_type="gateway_batch_ingested",
        severity="info",
        message=f"Gateway {gateway.name} batch processed",
        i18n_key="gateway_batch_ingested",
        i18n_params={"name": gateway.name, "count": accepted},
        metadata={"gateway_code": payload.gateway_code, "sequence_no": payload.sequence_no, "accepted": accepted},
    )
    db.commit()
    return accepted


def ingest_gateway_legacy(
    db: Session,
    gateway_code: str,
    readings: list[TelemetryIn],
    x_gateway_token: str | None,
) -> int:
    gateway = validate_gateway_token(db, gateway_code, x_gateway_token)
    accepted = _persist_readings(db=db, readings=readings)
    _warn_http_fallback(gateway_code, accepted)
    gateway.last_seen_at = datetime.now(timezone.utc)
    db.commit()
    return accepted


def _persist_readings(db: Session, readings: list[TelemetryIn]) -> int:
    accepted = 0
    for reading in readings:
        payload = reading.model_dump(mode="json")
        payload["source_gateway"] = payload.get("source_gateway") or "api-manual"
        message_id = payload.get("message_id") or str(uuid4())
        payload["message_id"] = message_id
        enqueue_outbox_event(
            db,
            topic="telemetry.raw_received",
            payload=payload,
            dedup_key=message_id,
        )
        accepted += 1
    # NOT: flush artik request yolunda DEGIL. Ingest sadece DB'ye yazar/commit
    # eder; RabbitMQ yayinini arka plan outbox flush worker'i yapar (bkz.
    # main.py _run_outbox_flush_worker). Boylece 200 cihaz yukunde ingest
    # response'u senkron broker publish'i beklemez -> gateway "Read timed out"
    # gitti. At-least-once korunur: satir DB'de published=False durur.
    return accepted
