from datetime import datetime, timezone

from fastapi import APIRouter, Depends, Header, HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.db.session import get_db
from app.models.alarm import AlarmEvent
from app.models.alarm_rule import AlarmRule
from app.models.device import Device
from app.models.gateway import Gateway
from app.models.signal_catalog import SignalCatalog
from app.schemas.alarm_rule import AlarmRuleRead
from app.schemas.gateway import GatewayRead
from app.schemas.internal import InternalAlarmIngest
from app.schemas.signal_catalog import SignalCatalogRead
from app.services.event_service import record_event

router = APIRouter(prefix="/internal", tags=["internal"])


def _require_service_token(token: str | None) -> None:
    if token != settings.internal_service_token:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid service token")


@router.get("/alarm-rules", response_model=list[AlarmRuleRead])
def list_alarm_rules_internal(
    db: Session = Depends(get_db),
    x_service_token: str | None = Header(default=None),
):
    """Alarm-service'in aktif kurallari cekmesi icin internal endpoint."""
    _require_service_token(x_service_token)
    stmt = select(AlarmRule).where(AlarmRule.is_active.is_(True))
    return list(db.scalars(stmt).all())


@router.get("/signals", response_model=list[SignalCatalogRead])
def list_signals_internal(
    db: Session = Depends(get_db),
    x_service_token: str | None = Header(default=None),
):
    """Ic servislerin standart sinyal listesini (supports_alarm dahil) cekmesi icin."""
    _require_service_token(x_service_token)
    stmt = select(SignalCatalog).where(SignalCatalog.is_active.is_(True))
    return list(db.scalars(stmt).all())


@router.post("/alarms", status_code=status.HTTP_202_ACCEPTED)
def ingest_alarm(
    payload: InternalAlarmIngest,
    db: Session = Depends(get_db),
    x_service_token: str | None = Header(default=None),
):
    _require_service_token(x_service_token)

    device_id = payload.device_id
    if device_id is None and payload.device_code:
        device = db.scalar(select(Device).where(Device.code == payload.device_code))
        device_id = device.id if device else None
    if device_id is None:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="device_id or valid device_code required")

    alarm = AlarmEvent(
        device_id=device_id,
        level=payload.level,
        title=payload.title,
        description=payload.description,
        created_at=datetime.now(timezone.utc),
    )
    db.add(alarm)
    record_event(
        db,
        category="alarm",
        event_type="alarm_ingested_internal",
        severity="warning",
        device_code=payload.device_code,
        message=f"Alarm service eventi backend'e alındı: {payload.title}",
        metadata={
            "message_id": payload.message_id,
            "correlation_id": payload.correlation_id,
            "source_gateway": payload.source_gateway,
        },
    )
    db.commit()
    return {"status": "accepted"}


@router.get("/gateways", response_model=list[GatewayRead])
def list_gateways_internal(
    db: Session = Depends(get_db),
    x_service_token: str | None = Header(default=None),
):
    """Kontrol paneli ve diger ic servislerin gateway listesini (is_active dahil)
    cekebilmesi icin token korumali endpoint. Installer login'e gerek kalmaz."""
    _require_service_token(x_service_token)
    stmt = select(Gateway).order_by(Gateway.name.asc())
    return list(db.scalars(stmt).all())


@router.post("/gateways/{gateway_code}/enable", response_model=GatewayRead)
def enable_gateway_internal(
    gateway_code: str,
    db: Session = Depends(get_db),
    x_service_token: str | None = Header(default=None),
):
    """Kontrol panelinin uzak gateway'i aktiflestirmesi icin servis token'li endpoint."""
    _require_service_token(x_service_token)
    row = db.scalar(select(Gateway).where(Gateway.code == gateway_code))
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Gateway not found")
    row.is_active = True
    db.commit()
    db.refresh(row)
    return row


@router.post("/gateways/{gateway_code}/disable", response_model=GatewayRead)
def disable_gateway_internal(
    gateway_code: str,
    db: Session = Depends(get_db),
    x_service_token: str | None = Header(default=None),
):
    """Kontrol panelinin uzak gateway'i pasiflestirmesi icin servis token'li endpoint."""
    _require_service_token(x_service_token)
    row = db.scalar(select(Gateway).where(Gateway.code == gateway_code))
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Gateway not found")
    row.is_active = False
    db.commit()
    db.refresh(row)
    return row
