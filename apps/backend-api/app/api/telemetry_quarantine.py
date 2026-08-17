"""Bilinmeyen cihaz telemetri karantinasi — operator/yonetim uclari.

Yetki: SADECE installer/engineer (system_admin ile ayni sinir). Karantina
ham saha payload'i tasir ve replay KALICI telemetri yazar; operator rolune
acilmaz.

GET  /admin/telemetry-quarantine
  Ozet: hangi cihaz kodu kac olcum biriktirdi, kod artik tanimli mi,
  kapasite doldu mu.

GET  /admin/telemetry-quarantine/items
  Kayit listesi (ham payload HARIC).

POST /admin/telemetry-quarantine/replay
  Bekleyen kayitlari normal telemetri yoluna basar. Cihaz hala tanimsizsa
  kayit korunur.

NEDEN OTOMATIK DEGIL: replay kalici telemetri yazar. Cihaz olusturma
istegine otomatik baglamak, yanlis kodla acilan bir cihazin baska bir
sahanin olcumlerini kendine cekmesi demekti. Ilk surumde tetik ACIK ve
operatorundur.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Body, Depends, Query
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api.deps import get_db, require_roles
from app.models.device import Device
from app.models.enums import UserRole
from app.models.unknown_device_telemetry import UnknownDeviceTelemetry
from app.models.user import User
from app.schemas.unknown_device_telemetry import (
    ReplayRequest,
    ReplayResponse,
    UnknownTelemetryDeviceSummary,
    UnknownTelemetryRead,
    UnknownTelemetrySummary,
)
from app.services import unknown_device_quarantine as quarantine
from app.services import unknown_device_replay
from app.services.event_service import record_event

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/admin/telemetry-quarantine",
    tags=["telemetry-quarantine"],
    dependencies=[Depends(require_roles([UserRole.INSTALLER, UserRole.ENGINEER]))],
)


@router.get("", response_model=UnknownTelemetrySummary)
def get_summary(db: Session = Depends(get_db)) -> UnknownTelemetrySummary:
    saglik = quarantine.health_snapshot(db)

    satirlar = db.execute(
        select(
            UnknownDeviceTelemetry.device_code,
            UnknownDeviceTelemetry.gateway_code,
            func.count().label("adet"),
            func.min(UnknownDeviceTelemetry.first_seen_at).label("en_eski"),
        )
        .where(UnknownDeviceTelemetry.status == quarantine.STATUS_PENDING)
        .group_by(UnknownDeviceTelemetry.device_code, UnknownDeviceTelemetry.gateway_code)
        .order_by(func.count().desc())
    ).all()

    # "Bu kod artik tanimli mi" = replay edilebilir mi. Operatorun ekranda
    # gormesi gereken tek karar bu.
    kodlar = {r.device_code for r in satirlar}
    tanimli = set(
        db.scalars(select(Device.code).where(Device.code.in_(kodlar))).all()
    ) if kodlar else set()

    replayed = int(
        db.scalar(
            select(func.count())
            .select_from(UnknownDeviceTelemetry)
            .where(UnknownDeviceTelemetry.status == quarantine.STATUS_REPLAYED)
        )
        or 0
    )

    return UnknownTelemetrySummary(
        pending_total=int(saglik["unknown_device_quarantine_pending"]),
        replayed_total=replayed,
        rows_total=int(saglik["unknown_device_quarantine_rows"]),
        max_rows=int(saglik["unknown_device_quarantine_max_rows"]),
        capacity_full=bool(saglik["unknown_device_quarantine_capacity_full"]),
        oldest_pending_age_sec=saglik["oldest_pending_age_sec"],
        devices=[
            UnknownTelemetryDeviceSummary(
                device_code=r.device_code,
                gateway_code=r.gateway_code,
                pending=int(r.adet),
                oldest_pending_at=r.en_eski,
                device_exists=r.device_code in tanimli,
            )
            for r in satirlar
        ],
    )


@router.get("/items", response_model=list[UnknownTelemetryRead])
def list_items(
    db: Session = Depends(get_db),
    device_code: str | None = Query(default=None),
    gateway_code: str | None = Query(default=None),
    status_filter: str | None = Query(default=None, alias="status"),
    limit: int = Query(default=200, ge=1, le=1000),
) -> list[UnknownDeviceTelemetry]:
    sorgu = select(UnknownDeviceTelemetry)
    if device_code:
        sorgu = sorgu.where(UnknownDeviceTelemetry.device_code == device_code)
    if gateway_code:
        sorgu = sorgu.where(UnknownDeviceTelemetry.gateway_code == gateway_code)
    if status_filter:
        sorgu = sorgu.where(UnknownDeviceTelemetry.status == status_filter)
    sorgu = sorgu.order_by(UnknownDeviceTelemetry.first_seen_at.desc()).limit(limit)
    return list(db.scalars(sorgu).all())


@router.post("/replay", response_model=ReplayResponse)
def replay_quarantined(
    payload: ReplayRequest = Body(default_factory=ReplayRequest),
    db: Session = Depends(get_db),
    current_user: User = Depends(
        require_roles([UserRole.INSTALLER, UserRole.ENGINEER])
    ),
) -> ReplayResponse:
    sonuc = unknown_device_replay.replay(
        db,
        device_code=payload.device_code,
        gateway_code=payload.gateway_code,
        limit=payload.limit,
    )

    # KALICI DURUM DEGISIMI -> denetim kaydi. Replay gecmise telemetri
    # yazar; kimin ne zaman tetikledigi iz birakmadan gecmemeli.
    if sonuc.replayed or sonuc.skipped_already_processed:
        record_event(
            db,
            category="telemetry",
            event_type="unknown_device_telemetry_replayed",
            severity="info",
            actor_username=current_user.username,
            device_code=payload.device_code,
            message=(
                "Karantinadaki bilinmeyen cihaz telemetrisi replay edildi: "
                f"{sonuc.replayed} olcum yazildi, "
                f"{sonuc.skipped_already_processed} zaten islenmisti"
            ),
            metadata=sonuc.as_dict(),
            i18n_key="unknown_device_telemetry_replayed",
            i18n_params={
                "replayed": sonuc.replayed,
                "code": payload.device_code or "*",
            },
        )
        db.commit()

    return ReplayResponse(**sonuc.as_dict())
