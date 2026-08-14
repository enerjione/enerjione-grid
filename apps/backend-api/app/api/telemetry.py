from fastapi import APIRouter, Body, Depends, Header, HTTPException, Request, status
from sqlalchemy.orm import Session

from app.api.deps import get_current_user, require_roles
from app.core.rate_limit import limiter
from app.db.session import get_db
from app.models.enums import UserRole
from app.models.user import User
from app.schemas.telemetry import GatewayTelemetryBatch, TelemetryIn, TelemetryRead
from app.services import scope_service
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
def list_latest(
    db: Session = Depends(get_db), user: User = Depends(get_current_user)
):
    """Son telemetri okumalari — KAPSAM suzgeci uygulanir.

    Kapsam kontrolu eskiden yoktu: operator tum sahanin son okumalarini
    gorebiliyordu (bkz. `scope_service`, `/devices` ile ayni kural).
    """
    return list_latest_telemetry(
        db, visible_device_ids=scope_service.get_visible_device_ids(db, user)
    )


@router.post("", status_code=status.HTTP_202_ACCEPTED)
@limiter.limit("10/minute")
def ingest(
    request: Request,
    payload: list[TelemetryIn] = Body(..., max_length=_MANUAL_INGEST_MAX_BATCH),
    db: Session = Depends(get_db),
    # Manuel/test ingest yolu: ENGINEER veya INSTALLER zorunlu. OPERATOR
    # rolu sahte telemetri injection ile alarm motorunu tetikleyemesin.
    # Gercek gateway akisi `/telemetry/gateway/{code}` endpoint'inde
    # `X-Gateway-Token` header ile dogrulanir (auth oradan).
    _user: User = Depends(require_roles([UserRole.INSTALLER, UserRole.ENGINEER])),
):
    """Manuel/test telemetri ingest (engineer+ rolu gerektirir).

    Production'da gateway'ler bu endpoint'i KULLANMAZ — telemetri NATS
    JetStream uzerinden direkt yayinlanir. Bu route geriye uyumluluk +
    operator UI test araclari icin tutulur. OPERATOR rolu reddedilir;
    rate-limit 10/dakika ile downstream fan-out (alarm-service,
    iec104-outbound) flood'u onlenir.
    """
    _ = request  # slowapi key_func icin gerekli
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
