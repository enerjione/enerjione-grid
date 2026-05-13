"""Toplu bildirim API'si — operator yoneticisi (ops_manager) ve installer/engineer
icin manuel duyuru/uyari gonderme + sablon yonetimi."""
from __future__ import annotations

import json
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import require_roles
from app.db.session import get_db
from app.models.bulk_notification_job import BulkNotificationJob
from app.models.bulk_notification_template import BulkNotificationTemplate
from app.models.enums import UserRole
from app.models.user import User
from app.services.bulk_notification_service import send_bulk_notification
from app.services.event_service import record_event

router = APIRouter(prefix="/bulk-notifications", tags=["bulk-notifications"])


_ALLOWED_ROLES = [UserRole.INSTALLER, UserRole.ENGINEER, UserRole.OPS_MANAGER]


class BulkNotifyRequest(BaseModel):
    subject: str = Field(min_length=1, max_length=200)
    message: str = Field(min_length=1, max_length=2000)
    channels: list[str] = Field(default_factory=lambda: ["web"])
    user_ids: list[int] = Field(default_factory=list)
    team_ids: list[int] = Field(default_factory=list)
    send_to_all: bool = False


class BulkNotifyResultRead(BaseModel):
    recipients_count: int
    web_sent: int
    email_sent: int
    email_failed: int
    sms_sent: int
    sms_failed: int
    skipped_no_email: int
    skipped_no_phone: int
    errors: list[str]


@router.post("", response_model=BulkNotifyResultRead)
def send_bulk(
    payload: BulkNotifyRequest,
    current_user: User = Depends(require_roles(_ALLOWED_ROLES)),
    db: Session = Depends(get_db),
):
    if not payload.user_ids and not payload.team_ids and not payload.send_to_all:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="En az bir hedef sec: user_ids, team_ids veya send_to_all=True",
        )
    valid_channels = {"web", "email", "sms"}
    bad = [c for c in payload.channels if c not in valid_channels]
    if bad:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Gecersiz kanal: {bad}. Gecerli: {sorted(valid_channels)}",
        )

    result = send_bulk_notification(
        db,
        actor=current_user,
        subject=payload.subject,
        message=payload.message,
        channels=payload.channels,
        user_ids=payload.user_ids,
        team_ids=payload.team_ids,
        send_to_all=payload.send_to_all,
    )

    # Audit log
    record_event(
        db,
        category="notification",
        event_type="bulk_notification_sent",
        severity="info",
        actor_username=current_user.username,
        message=(
            f"{current_user.username} sent bulk notification "
            f"(recipients={result.recipients_count}, "
            f"web={result.web_sent}, email={result.email_sent}, sms={result.sms_sent})"
        ),
        metadata={
            "subject": payload.subject,
            "channels": payload.channels,
            "user_ids": payload.user_ids,
            "team_ids": payload.team_ids,
            "send_to_all": payload.send_to_all,
            "recipients_count": result.recipients_count,
            "web_sent": result.web_sent,
            "email_sent": result.email_sent,
            "sms_sent": result.sms_sent,
        },
        i18n_key="bulk_notification_sent",
        i18n_params={
            "actor": current_user.username,
            "count": result.recipients_count,
        },
    )
    db.commit()

    return BulkNotifyResultRead(
        recipients_count=result.recipients_count,
        web_sent=result.web_sent,
        email_sent=result.email_sent,
        email_failed=result.email_failed,
        sms_sent=result.sms_sent,
        sms_failed=result.sms_failed,
        skipped_no_email=result.skipped_no_email,
        skipped_no_phone=result.skipped_no_phone,
        errors=result.errors,
    )


# ============================================================
# SABLONLAR — kayitli mesaj+kanal+hedef seti
# ============================================================


class TemplateTarget(BaseModel):
    user_ids: list[int] = Field(default_factory=list)
    team_ids: list[int] = Field(default_factory=list)
    send_to_all: bool = False


class TemplateCreateRequest(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    subject: str = Field(min_length=1, max_length=200)
    message: str = Field(min_length=1, max_length=2000)
    channels: list[str] = Field(default_factory=lambda: ["web"])
    target: TemplateTarget | None = None


class TemplateRead(BaseModel):
    id: int
    name: str
    subject: str
    message: str
    channels: list[str]
    target: TemplateTarget | None
    created_at: str
    updated_at: str


def _row_to_template(row: BulkNotificationTemplate) -> TemplateRead:
    channels = [c.strip() for c in (row.channels or "").split(",") if c.strip()]
    target: TemplateTarget | None = None
    if row.target_json:
        try:
            data = json.loads(row.target_json)
            target = TemplateTarget(
                user_ids=list(data.get("user_ids") or []),
                team_ids=list(data.get("team_ids") or []),
                send_to_all=bool(data.get("send_to_all", False)),
            )
        except Exception:  # noqa: BLE001
            target = None
    return TemplateRead(
        id=row.id,
        name=row.name,
        subject=row.subject,
        message=row.message,
        channels=channels,
        target=target,
        created_at=row.created_at.isoformat() if isinstance(row.created_at, datetime) else str(row.created_at),
        updated_at=row.updated_at.isoformat() if isinstance(row.updated_at, datetime) else str(row.updated_at),
    )


@router.get("/templates", response_model=list[TemplateRead])
def list_templates(
    _: User = Depends(require_roles(_ALLOWED_ROLES)),
    db: Session = Depends(get_db),
):
    rows = list(
        db.scalars(
            select(BulkNotificationTemplate).order_by(BulkNotificationTemplate.updated_at.desc())
        ).all()
    )
    return [_row_to_template(r) for r in rows]


@router.post("/templates", response_model=TemplateRead, status_code=status.HTTP_201_CREATED)
def create_template(
    payload: TemplateCreateRequest,
    current_user: User = Depends(require_roles(_ALLOWED_ROLES)),
    db: Session = Depends(get_db),
):
    # Channels validasyonu
    valid = {"web", "email", "sms"}
    bad = [c for c in payload.channels if c not in valid]
    if bad:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Gecersiz kanal: {bad}. Gecerli: {sorted(valid)}",
        )
    # Ayni isimde sablon var mi?
    existing = db.scalar(
        select(BulkNotificationTemplate).where(BulkNotificationTemplate.name == payload.name)
    )
    if existing:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Bu isimde sablon zaten var: {payload.name}",
        )

    target_json = None
    if payload.target is not None:
        target_json = json.dumps(
            {
                "user_ids": payload.target.user_ids,
                "team_ids": payload.target.team_ids,
                "send_to_all": payload.target.send_to_all,
            },
            ensure_ascii=False,
        )

    row = BulkNotificationTemplate(
        name=payload.name.strip(),
        subject=payload.subject.strip(),
        message=payload.message.strip(),
        channels=",".join(payload.channels) if payload.channels else "web",
        target_json=target_json,
        created_by_user_id=current_user.id,
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return _row_to_template(row)


@router.delete("/templates/{template_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_template(
    template_id: int,
    current_user: User = Depends(require_roles(_ALLOWED_ROLES)),
    db: Session = Depends(get_db),
):
    row = db.get(BulkNotificationTemplate, template_id)
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Sablon bulunamadi")
    # ops_manager sadece kendi sabonlarini silebilir; installer/engineer hepsi
    if current_user.role == UserRole.OPS_MANAGER and row.created_by_user_id != current_user.id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Bu sabonu silmeye yetkin yok",
        )
    db.delete(row)
    db.commit()
    return None


# ============================================================
# ZAMANLANMIS BILDIRIMLER — ileri tarihli gonderim
# ============================================================


class ScheduleRequest(BaseModel):
    scheduled_at: datetime
    subject: str = Field(min_length=1, max_length=200)
    message: str = Field(min_length=1, max_length=2000)
    channels: list[str] = Field(default_factory=lambda: ["web"])
    user_ids: list[int] = Field(default_factory=list)
    team_ids: list[int] = Field(default_factory=list)
    send_to_all: bool = False


class ScheduleRead(BaseModel):
    id: int
    scheduled_at: str
    status: str
    subject: str
    message: str
    channels: list[str]
    user_ids: list[int]
    team_ids: list[int]
    send_to_all: bool
    created_by_username: str | None
    created_at: str
    executed_at: str | None
    result_summary: str | None


def _row_to_schedule(row: BulkNotificationJob, db: Session) -> ScheduleRead:
    channels = [c.strip() for c in (row.channels or "").split(",") if c.strip()]
    payload: dict = {}
    try:
        payload = json.loads(row.target_json or "{}")
    except Exception:  # noqa: BLE001
        payload = {}
    created_by_username: str | None = None
    if row.created_by_user_id is not None:
        u = db.get(User, row.created_by_user_id)
        if u is not None:
            created_by_username = u.username
    return ScheduleRead(
        id=row.id,
        scheduled_at=row.scheduled_at.isoformat(),
        status=row.status,
        subject=row.subject,
        message=row.message,
        channels=channels,
        user_ids=list(payload.get("user_ids") or []),
        team_ids=list(payload.get("team_ids") or []),
        send_to_all=bool(payload.get("send_to_all", False)),
        created_by_username=created_by_username,
        created_at=row.created_at.isoformat(),
        executed_at=row.executed_at.isoformat() if row.executed_at else None,
        result_summary=row.result_summary,
    )


@router.get("/scheduled", response_model=list[ScheduleRead])
def list_scheduled(
    current_user: User = Depends(require_roles(_ALLOWED_ROLES)),
    db: Session = Depends(get_db),
):
    stmt = (
        select(BulkNotificationJob)
        .order_by(BulkNotificationJob.scheduled_at.desc())
        .limit(200)
    )
    # ops_manager sadece kendi olusturduklarini gorsun
    if current_user.role == UserRole.OPS_MANAGER:
        stmt = stmt.where(BulkNotificationJob.created_by_user_id == current_user.id)
    rows = list(db.scalars(stmt).all())
    return [_row_to_schedule(r, db) for r in rows]


@router.post("/scheduled", response_model=ScheduleRead, status_code=status.HTTP_201_CREATED)
def create_scheduled(
    payload: ScheduleRequest,
    current_user: User = Depends(require_roles(_ALLOWED_ROLES)),
    db: Session = Depends(get_db),
):
    # Validasyon
    if not payload.user_ids and not payload.team_ids and not payload.send_to_all:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="En az bir hedef sec: user_ids, team_ids veya send_to_all=True",
        )
    valid_channels = {"web", "email", "sms"}
    bad = [c for c in payload.channels if c not in valid_channels]
    if bad:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Gecersiz kanal: {bad}. Gecerli: {sorted(valid_channels)}",
        )
    # Tarih kontrolu — gecmis olamaz
    sched = payload.scheduled_at
    if sched.tzinfo is None:
        sched = sched.replace(tzinfo=timezone.utc)
    now_utc = datetime.now(timezone.utc)
    if sched <= now_utc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="scheduled_at gelecekte olmali",
        )

    target_payload = {
        "user_ids": payload.user_ids,
        "team_ids": payload.team_ids,
        "send_to_all": payload.send_to_all,
        "actor_username": current_user.username,
    }
    row = BulkNotificationJob(
        scheduled_at=sched,
        status="pending",
        subject=payload.subject.strip(),
        message=payload.message.strip(),
        channels=",".join(payload.channels) if payload.channels else "web",
        target_json=json.dumps(target_payload, ensure_ascii=False),
        created_by_user_id=current_user.id,
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return _row_to_schedule(row, db)


@router.delete("/scheduled/{job_id}", status_code=status.HTTP_204_NO_CONTENT)
def cancel_scheduled(
    job_id: int,
    current_user: User = Depends(require_roles(_ALLOWED_ROLES)),
    db: Session = Depends(get_db),
):
    row = db.get(BulkNotificationJob, job_id)
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Job bulunamadi")
    # ops_manager sadece kendi job'larini iptal edebilir
    if current_user.role == UserRole.OPS_MANAGER and row.created_by_user_id != current_user.id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Bu zamanlanmis bildirimi iptal etmeye yetkin yok",
        )
    if row.status in ("sent", "failed"):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Zaten {row.status} olmus bir bildirimi iptal edemezsin",
        )
    row.status = "cancelled"
    row.executed_at = datetime.now(timezone.utc)
    db.commit()
    return None


