"""Toplu (manuel) bildirim servisi — operator yoneticisi UI'sinden cagrilir.

Mevcut alarm/fault notification yapisindan ayri tutuldu: bu manuel mesaj
gonderimi (kullanici/ekipe ozel duyuru) ve kanal secimi (web/email/sms)
caller'a aittir. Mevcut `notification_dispatch_service` alarm-event'lerine
bagli oldugu icin tekrar kullanmak yerine ozel bir API yazildi.

Hedef cozumu:
- user_ids: dogrudan kullanici id listesi
- team_ids: responsibility_area id listesi -> uyelerini cek
- send_to_all: True ise tum aktif kullanicilara

Kanallar:
- "web": create_notification (NotificationBell'de gozukur)
- "email": settings.smtp_enabled ise send_smtp_test ile yollar
- "sms": settings.sms_enabled ise send_sms_test ile yollar

Idempotent degil — caller her cagri yeni mesaj kabul edilir.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.enums import UserRole
from app.models.notification_settings import NotificationSettings
from app.models.responsibility_area import responsibility_area_users
from app.models.user import User
from app.services.notification_service import create_notification
from app.services.notification_test_service import (
    send_smtp_test,
    send_sms_test,
)

logger = logging.getLogger(__name__)


@dataclass
class BulkNotifyResult:
    """Caller'a istatistik don."""
    recipients_count: int = 0
    web_sent: int = 0
    email_sent: int = 0
    email_failed: int = 0
    sms_sent: int = 0
    sms_failed: int = 0
    skipped_no_email: int = 0
    skipped_no_phone: int = 0
    errors: list[str] = field(default_factory=list)


def _resolve_recipients(
    db: Session,
    *,
    user_ids: list[int],
    team_ids: list[int],
    send_to_all: bool,
    actor: User,
) -> list[User]:
    """Hedef kullanicilari topla — duplicate'i kaldir, self'i icermeyebilir.

    actor yalnizca ops_manager ise SADECE operator rolundeki kullanicilari
    secebilir; aksi halde hedef listesinden cikarilir (silent strip — sayfa
    UI'sinde zaten secemiyor olmali, defense-in-depth).
    """
    seen: set[int] = set()
    result: list[User] = []

    def add(u: User):
        if u.id in seen:
            return
        if actor.role == UserRole.OPS_MANAGER and u.role != UserRole.OPERATOR:
            return  # ops_manager yalnizca operator'e atabilir
        seen.add(u.id)
        result.append(u)

    if send_to_all:
        stmt = select(User)
        if actor.role == UserRole.OPS_MANAGER:
            stmt = stmt.where(User.role == UserRole.OPERATOR)
        for u in db.scalars(stmt).all():
            add(u)
        return result

    if user_ids:
        for u in db.scalars(select(User).where(User.id.in_(user_ids))).all():
            add(u)

    if team_ids:
        # Ekibin uye user_id'lerini topla (M2M tablo)
        rows = db.execute(
            select(responsibility_area_users.c.user_id)
            .where(responsibility_area_users.c.area_id.in_(team_ids))
        ).all()
        member_ids = list({r[0] for r in rows})
        if member_ids:
            for u in db.scalars(select(User).where(User.id.in_(member_ids))).all():
                add(u)

    return result


def send_bulk_notification(
    db: Session,
    *,
    actor: User,
    subject: str,
    message: str,
    channels: list[str],
    user_ids: list[int] | None = None,
    team_ids: list[int] | None = None,
    send_to_all: bool = False,
) -> BulkNotifyResult:
    """Toplu bildirim gonder.

    channels: ["web", "email", "sms"] subset. Bos liste = sadece web (default).
    Caller responsibility: en az bir kanal + en az bir hedef.
    """
    channels_set = {c.strip().lower() for c in channels if c.strip()}
    if not channels_set:
        channels_set = {"web"}

    recipients = _resolve_recipients(
        db,
        user_ids=user_ids or [],
        team_ids=team_ids or [],
        send_to_all=send_to_all,
        actor=actor,
    )

    result = BulkNotifyResult(recipients_count=len(recipients))
    if not recipients:
        result.errors.append("no_recipients_resolved")
        return result

    # SMTP/SMS ayarlari (gerekirse)
    settings: NotificationSettings | None = None
    if "email" in channels_set or "sms" in channels_set:
        settings = db.scalar(select(NotificationSettings))

    subject = subject.strip() or "Bildirim"
    body = message.strip()

    for user in recipients:
        # Web bildirim
        if "web" in channels_set:
            try:
                create_notification(
                    db,
                    recipient_username=user.username,
                    category="system",
                    severity="info",
                    title=subject,
                    body=body,
                    metadata={
                        "kind": "bulk_notification",
                        "sender": actor.username,
                    },
                )
                result.web_sent += 1
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "bulk_notify_web_failed user=%s error=%s", user.username, exc,
                )
                result.errors.append(f"web:{user.username}:{exc}")

        # Email
        if "email" in channels_set:
            if settings is None or not settings.smtp_enabled:
                result.errors.append("email_channel_disabled")
            elif not user.email:
                result.skipped_no_email += 1
            else:
                try:
                    send_smtp_test(
                        settings,
                        recipient_email=user.email,
                        subject=subject,
                        message=body,
                    )
                    result.email_sent += 1
                except Exception as exc:  # noqa: BLE001
                    logger.warning(
                        "bulk_notify_email_failed user=%s error=%s",
                        user.username, exc,
                    )
                    result.email_failed += 1
                    result.errors.append(f"email:{user.username}:{exc}")

        # SMS
        if "sms" in channels_set:
            if settings is None or not settings.sms_enabled:
                result.errors.append("sms_channel_disabled")
            elif not user.phone_number:
                result.skipped_no_phone += 1
            else:
                try:
                    send_sms_test(
                        settings,
                        recipient_phone=user.phone_number,
                        message=f"{subject}: {body}"[:300],
                    )
                    result.sms_sent += 1
                except Exception as exc:  # noqa: BLE001
                    logger.warning(
                        "bulk_notify_sms_failed user=%s error=%s",
                        user.username, exc,
                    )
                    result.sms_failed += 1
                    result.errors.append(f"sms:{user.username}:{exc}")

    return result
