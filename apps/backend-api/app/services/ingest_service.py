import hashlib
import hmac
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


def hash_gateway_token(token: str) -> str:
    """Gateway token'in SHA-256 hex digest'i. DB'de plaintext yerine bu
    saklanir; saldirgan DB read-only erisimi alsa bile reverse ile orijinal
    token elde edemez.
    """
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def list_latest_telemetry(db: Session) -> list[Telemetry]:
    stmt = select(Telemetry).order_by(Telemetry.source_timestamp.desc()).limit(200)
    return list(db.scalars(stmt).all())


def ingest_direct_telemetry(db: Session, readings: list[TelemetryIn]) -> int:
    accepted = _persist_readings(db=db, readings=readings)
    db.commit()
    return accepted


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
    gateway.last_seen_at = datetime.now(timezone.utc)
    record_event(
        db,
        category="telemetry",
        event_type="gateway_batch_ingested",
        severity="info",
        message=f"Gateway {gateway.name} batch processed",
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
    return accepted
