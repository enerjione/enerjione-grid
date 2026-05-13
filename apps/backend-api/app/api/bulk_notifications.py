"""Toplu bildirim API'si — operator yoneticisi (ops_manager) ve installer/engineer
icin manuel duyuru/uyari gonderme."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.api.deps import require_roles
from app.db.session import get_db
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
