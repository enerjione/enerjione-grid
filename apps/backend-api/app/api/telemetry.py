from fastapi import APIRouter, Body, Depends, Header, HTTPException, status
from sqlalchemy.orm import Session

from app.api.deps import get_current_user
from app.db.session import get_db
from app.models.user import User
from app.schemas.telemetry import GatewayTelemetryBatch, TelemetryIn, TelemetryRead
from app.services.ingest_service import (
    ingest_direct_telemetry,
    ingest_gateway_batch,
    ingest_gateway_legacy,
    list_latest_telemetry,
)

router = APIRouter(prefix="/telemetry", tags=["telemetry"])

# Manuel ingest icin tek istekte kabul edilebilecek maksimum kayit sayisi.
# Gercek telemetri akisi gateway → NATS uzerinden gelir; bu endpoint sadece
# operator/test amaclidir, asiri buyuk batch'ler kabul edilmemeli.
_MANUAL_INGEST_MAX_BATCH = 1000


@router.get("/latest", response_model=list[TelemetryRead])
def list_latest(db: Session = Depends(get_db), _: User = Depends(get_current_user)):
    return list_latest_telemetry(db)


@router.post("", status_code=status.HTTP_202_ACCEPTED)
def ingest(
    payload: list[TelemetryIn] = Body(..., max_length=_MANUAL_INGEST_MAX_BATCH),
    db: Session = Depends(get_db),
    # Manuel/test ingest yolu: kullanici oturumu zorunlu. Sahte telemetri
    # injection'i (anonim DB write + DoS) bu auth ile kapatildi. Gerçek
    # gateway-batch akisi `/telemetry/gateway/{code}` endpoint'inde
    # `X-Gateway-Token` header ile dogrulanir (auth oradan).
    _user: User = Depends(get_current_user),
):
    """Manuel/test telemetri ingest (operator login'i gerektirir).

    Production'da gateway'ler bu endpoint'i KULLANMAZ — telemetri NATS
    JetStream uzerinden direkt yayinlanir. Bu route geriye uyumluluk +
    operator UI test araclari icin tutulur.
    """
    if not payload:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Empty telemetry batch",
        )
    accepted = ingest_direct_telemetry(db, payload)
    return {"accepted": accepted}


@router.post("/gateway/{gateway_code}", status_code=status.HTTP_202_ACCEPTED)
def ingest_from_gateway(
    gateway_code: str,
    payload: GatewayTelemetryBatch | list[TelemetryIn],
    db: Session = Depends(get_db),
    x_gateway_token: str | None = Header(default=None),
):
    if isinstance(payload, list):
        accepted = ingest_gateway_legacy(db, gateway_code, payload, x_gateway_token)
        return {"accepted": accepted}
    if payload.gateway_code != gateway_code:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Gateway code mismatch")
    accepted = ingest_gateway_batch(db, payload, x_gateway_token)
    return {"accepted": accepted}
