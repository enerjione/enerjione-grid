"""Public REST API — dış sistemlerin tüketmesi için, API Key korumalı, read-only.

Erişim:
  Authorization: Bearer hsl_pat_<token>

Scope'lar:
  * devices:read    — /public/devices
  * signals:read    — /public/signals
  * telemetry:read  — /public/telemetry/*
  * alarms:read     — /public/alarms
  * system:read     — /public/system/*

Tasarım kararları:
  * Path: `/api/v1/public/*` — `/api/v1/` web/mobil JWT akışı kullanır, `/public`
    altı API Key (PAT) akışı. İleride `/api/v2/public/...` ile versiyonlama.
  * Sayfalama: `limit` + `offset` (max limit 1000). Çok satırlı endpoint'lerde
    `X-Total-Count` header'ı doldurulur ki istemci tüm sayfayı tahmin edebilsin.
  * Tarih formatı: ISO 8601 UTC ("2026-05-08T12:30:00Z").
  * Hata cevap formatı: FastAPI default `{"detail": "..."}` — istemci stabil
    pattern beklesin.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from sqlalchemy import select, func
from sqlalchemy.orm import Session

from app.api.public_deps import ApiKeyContext, require_scope
from app.db.session import get_db
from app.models.alarm import AlarmEvent
from app.models.device import Device
from app.models.gateway import Gateway
from app.models.signal_catalog import SignalCatalog
from app.models.telemetry import Telemetry
from app.schemas.alarm import AlarmEventRead
from app.schemas.device import DeviceRead
from app.schemas.gateway import GatewayRead
from app.schemas.signal_catalog import SignalCatalogRead
from app.services import device_kit_service

# Tum endpoint'ler bu prefix altinda. Tag = OpenAPI dokumantasyonunda gruplama.
router = APIRouter(prefix="/public", tags=["public-api"])


# ---------------------------------------------------------------------------
# /public/me — token sahibi + scope listesi
# ---------------------------------------------------------------------------

@router.get(
    "/me",
    summary="Token sahibinin bilgileri",
    description=(
        "Bu endpoint, token'in geçerli ve aktif olduğunu doğrulamak için "
        "kullanılır. Herhangi bir scope gerektirmez."
    ),
)
def read_me(ctx: ApiKeyContext = Depends(require_scope("devices:read"))):
    # require_scope yerine require_api_key ile cagrilabilir ama her token'da
    # minimum 'devices:read' default scope oldugundan pratik olarak ayni.
    # Yine de "scope-free" bir endpoint isteyenler require_api_key kullanmali.
    return {
        "user": {
            "id": ctx.user.id,
            "username": ctx.user.username,
            "role": ctx.user.role.value if hasattr(ctx.user.role, "value") else str(ctx.user.role),
        },
        "api_key": {
            "id": ctx.api_key.id,
            "name": ctx.api_key.name,
            "scopes": ctx.api_key.scopes.split(",") if ctx.api_key.scopes else [],
            "expires_at": ctx.api_key.expires_at,
        },
    }


# ---------------------------------------------------------------------------
# /public/devices
# ---------------------------------------------------------------------------

@router.get(
    "/devices",
    response_model=list[DeviceRead],
    summary="Cihazları listele",
    description=(
        "Sistemdeki tüm cihazları döner. Filtreler ile alt küme çekilebilir. "
        "Scope: `devices:read`."
    ),
)
def list_devices(
    response: Response,
    db: Session = Depends(get_db),
    ctx: ApiKeyContext = Depends(require_scope("devices:read")),
    gateway_code: str | None = Query(None, description="Belirli gateway'in cihazları"),
    model: str | None = Query(None, description="Cihaz modeli (örn. `horstmann_sn_2_0`)"),
    limit: int = Query(100, ge=1, le=1000),
    offset: int = Query(0, ge=0),
):
    base = select(Device)
    count_q = select(func.count()).select_from(Device)
    if gateway_code:
        base = base.where(Device.gateway_code == gateway_code)
        count_q = count_q.where(Device.gateway_code == gateway_code)
    if model:
        base = base.where(Device.model == model)
        count_q = count_q.where(Device.model == model)
    base = base.order_by(Device.code.asc()).limit(limit).offset(offset)
    total = db.scalar(count_q) or 0
    response.headers["X-Total-Count"] = str(total)
    # `annotate`: `alarm_active` KOLONDAN okunamaz, canli alarmdan turer
    # (bkz. models/device.py). Bu ucu atlarsak dis entegrasyon her cihazi
    # sonsuza kadar "alarm yok" gorurdu.
    return device_kit_service.annotate(db, list(db.scalars(base).all()))


@router.get(
    "/devices/{code}",
    response_model=DeviceRead,
    summary="Cihaz detayı",
)
def get_device(
    code: str,
    db: Session = Depends(get_db),
    ctx: ApiKeyContext = Depends(require_scope("devices:read")),
):
    row = db.scalar(select(Device).where(Device.code == code))
    if row is None:
        raise HTTPException(status_code=404, detail="Cihaz bulunamadı")
    return device_kit_service.annotate_one(db, row)


# ---------------------------------------------------------------------------
# /public/signals
# ---------------------------------------------------------------------------

@router.get(
    "/signals",
    response_model=list[SignalCatalogRead],
    summary="Sinyal kataloğunu listele",
    description="Sistemdeki aktif sinyallerin tanımları. Scope: `signals:read`.",
)
def list_signals(
    db: Session = Depends(get_db),
    ctx: ApiKeyContext = Depends(require_scope("signals:read")),
    model: str | None = Query(None),
    active_only: bool = Query(True),
):
    stmt = select(SignalCatalog)
    if active_only:
        stmt = stmt.where(SignalCatalog.is_active.is_(True))
    if model:
        stmt = stmt.where(SignalCatalog.model == model)
    stmt = stmt.order_by(SignalCatalog.display_order.asc(), SignalCatalog.key.asc())
    return list(db.scalars(stmt).all())


# ---------------------------------------------------------------------------
# /public/telemetry
# ---------------------------------------------------------------------------

@router.get(
    "/telemetry/{device_code}/latest",
    summary="Bir cihazın en son sinyal değerleri",
    description=(
        "Cihazın signal_catalog'undaki her sinyal için en son okunan değer "
        "döner. Scope: `telemetry:read`."
    ),
)
def latest_telemetry(
    device_code: str,
    db: Session = Depends(get_db),
    ctx: ApiKeyContext = Depends(require_scope("telemetry:read")),
):
    device = db.scalar(select(Device).where(Device.code == device_code))
    if device is None:
        raise HTTPException(status_code=404, detail="Cihaz bulunamadı")

    # Her signal_key icin en son satir (window function ile tek query, alternatif:
    # GROUP BY signal_key + MAX(source_timestamp)).
    subq = (
        select(
            Telemetry.signal_key,
            func.max(Telemetry.source_timestamp).label("ts"),
        )
        .where(Telemetry.device_id == device.id)
        .group_by(Telemetry.signal_key)
        .subquery()
    )
    stmt = (
        select(Telemetry)
        .join(
            subq,
            (Telemetry.signal_key == subq.c.signal_key)
            & (Telemetry.source_timestamp == subq.c.ts),
        )
        .where(Telemetry.device_id == device.id)
    )
    rows = list(db.scalars(stmt).all())
    return [
        {
            "signal_key": r.signal_key,
            "value": r.value,
            "value_string": r.value_string,
            "quality": r.quality,
            "source_timestamp": r.source_timestamp,
        }
        for r in rows
    ]


@router.get(
    "/telemetry/{device_code}/history",
    summary="Bir cihazın bir sinyali için geçmiş telemetri",
    description=(
        "Belirtilen `signal_key` için zaman aralığında okumalar. "
        "Scope: `telemetry:read`. Maks 5000 satır."
    ),
)
def telemetry_history(
    device_code: str,
    signal_key: str = Query(..., description="Sinyalin key'i (örn. `master.voltage_a`)"),
    response: Response = None,  # type: ignore[assignment]
    db: Session = Depends(get_db),
    ctx: ApiKeyContext = Depends(require_scope("telemetry:read")),
    since: datetime | None = Query(None, description="Bu zamandan sonra (UTC ISO 8601)"),
    until: datetime | None = Query(None, description="Bu zamana kadar (UTC ISO 8601)"),
    limit: int = Query(500, ge=1, le=5000),
    order: Literal["asc", "desc"] = Query("desc"),
):
    device = db.scalar(select(Device).where(Device.code == device_code))
    if device is None:
        raise HTTPException(status_code=404, detail="Cihaz bulunamadı")

    stmt = select(Telemetry).where(
        Telemetry.device_id == device.id,
        Telemetry.signal_key == signal_key,
    )
    if since:
        if since.tzinfo is None:
            since = since.replace(tzinfo=timezone.utc)
        stmt = stmt.where(Telemetry.source_timestamp >= since)
    if until:
        if until.tzinfo is None:
            until = until.replace(tzinfo=timezone.utc)
        stmt = stmt.where(Telemetry.source_timestamp <= until)

    stmt = stmt.order_by(
        Telemetry.source_timestamp.asc() if order == "asc"
        else Telemetry.source_timestamp.desc()
    ).limit(limit)

    rows = list(db.scalars(stmt).all())
    if response is not None:
        response.headers["X-Returned-Count"] = str(len(rows))
    return [
        {
            "signal_key": r.signal_key,
            "value": r.value,
            "value_string": r.value_string,
            "quality": r.quality,
            "source_timestamp": r.source_timestamp,
        }
        for r in rows
    ]


# ---------------------------------------------------------------------------
# /public/alarms
# ---------------------------------------------------------------------------

@router.get(
    "/alarms",
    response_model=list[AlarmEventRead],
    summary="Alarm event'lerini listele",
    description="Filtre + sayfalama. Scope: `alarms:read`.",
)
def list_alarms(
    response: Response,
    db: Session = Depends(get_db),
    ctx: ApiKeyContext = Depends(require_scope("alarms:read")),
    device_code: str | None = Query(None),
    level: Literal["info", "warning", "critical"] | None = Query(None),
    open_only: bool = Query(False, description="Sadece reset edilmemiş alarmlar"),
    since: datetime | None = Query(None),
    until: datetime | None = Query(None),
    limit: int = Query(100, ge=1, le=1000),
    offset: int = Query(0, ge=0),
):
    stmt = select(AlarmEvent)
    if device_code:
        device = db.scalar(select(Device).where(Device.code == device_code))
        if device is None:
            raise HTTPException(status_code=404, detail="Cihaz bulunamadı")
        stmt = stmt.where(AlarmEvent.device_id == device.id)
    if level:
        stmt = stmt.where(AlarmEvent.level == level)
    if open_only:
        stmt = stmt.where(AlarmEvent.reset.is_(False))
    if since:
        if since.tzinfo is None:
            since = since.replace(tzinfo=timezone.utc)
        stmt = stmt.where(AlarmEvent.created_at >= since)
    if until:
        if until.tzinfo is None:
            until = until.replace(tzinfo=timezone.utc)
        stmt = stmt.where(AlarmEvent.created_at <= until)

    stmt = stmt.order_by(AlarmEvent.created_at.desc()).limit(limit).offset(offset)
    rows = list(db.scalars(stmt).all())
    response.headers["X-Returned-Count"] = str(len(rows))
    return rows


# ---------------------------------------------------------------------------
# /public/system
# ---------------------------------------------------------------------------

@router.get(
    "/system/gateways",
    response_model=list[GatewayRead],
    summary="Gateway listesi",
    description="Tanımlı gateway'lerin temel bilgisi. Scope: `system:read`.",
)
def list_gateways(
    db: Session = Depends(get_db),
    ctx: ApiKeyContext = Depends(require_scope("system:read")),
    active_only: bool = Query(True),
):
    stmt = select(Gateway)
    if active_only:
        stmt = stmt.where(Gateway.is_active.is_(True))
    stmt = stmt.order_by(Gateway.code.asc())
    return list(db.scalars(stmt).all())


@router.get(
    "/system/health",
    summary="Sistem temel sağlık özeti",
    description=(
        "Public API'nin çalıştığını ve auth katmanının doğru kurulduğunu "
        "doğrulamak için kısa bir özet. Scope: `system:read`."
    ),
)
def public_health(
    db: Session = Depends(get_db),
    ctx: ApiKeyContext = Depends(require_scope("system:read")),
):
    device_count = db.scalar(select(func.count()).select_from(Device)) or 0
    gateway_count = db.scalar(select(func.count()).select_from(Gateway)) or 0
    open_alarms = db.scalar(
        select(func.count()).select_from(AlarmEvent).where(AlarmEvent.reset.is_(False))
    ) or 0
    return {
        "status": "ok",
        "ts": datetime.now(timezone.utc),
        "device_count": device_count,
        "gateway_count": gateway_count,
        "open_alarm_count": open_alarms,
    }
