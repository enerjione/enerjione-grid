from datetime import datetime, timezone
from uuid import uuid4

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.alarm import AlarmComment, AlarmEvent
from app.models.user import User
from app.services.event_service import record_event
from app.services.notification_service import create_notification, notify_users
from app.services.outbox_service import enqueue_outbox_event


def list_alarm_events(db: Session) -> list[AlarmEvent]:
    stmt = select(AlarmEvent).order_by(AlarmEvent.created_at.desc()).limit(500)
    return list(db.scalars(stmt).all())


def assign_alarm(db: Session, alarm_id: int, assigned_to: str | None, actor_username: str) -> AlarmEvent:
    alarm = db.get(AlarmEvent, alarm_id)
    if alarm is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Alarm not found")
    previous_assignee = alarm.assigned_to
    new_assignee = assigned_to.strip() if assigned_to else None
    alarm.assigned_to = new_assignee
    record_event(
        db,
        category="alarm",
        event_type="alarm_assigned",
        severity="info",
        actor_username=actor_username,
        message=f"\"{alarm.title}\" alarmının ataması güncellendi",
        metadata={"alarm_id": alarm.id, "assigned_to": new_assignee, "previous_assignee": previous_assignee},
    )
    # Bildirim mantigi:
    #  * Atanmis kisi degisti VE bos degil ise → atanan kisiye bildirim gonder
    #  * Atayan kullanici kendisi olsa bile bildirim olusur (gorsel feedback icin)
    #  * Ayni kisiye yeniden atama (degisiklik yok) → bildirim atlanir (spam onlemi)
    if new_assignee and new_assignee != previous_assignee:
        if new_assignee == actor_username:
            title = f"Bu alarmı kendi üstünüze aldınız: {alarm.title}"
            title_i18n_key = "alarm_assignment_self"
        else:
            title = f"Size yeni bir alarm atandı: {alarm.title}"
            title_i18n_key = "alarm_assignment_other"
        create_notification(
            db,
            recipient_username=new_assignee,
            category="alarm_assignment",
            severity=alarm.level or "info",
            title=title,
            body=alarm.description,
            actor_username=actor_username,
            link=f"/alarms#alarm-{alarm.id}",
            metadata={
                "alarm_id": alarm.id,
                "level": alarm.level,
                "_title_i18n": {"key": title_i18n_key, "params": {"title": alarm.title}},
            },
        )
        # E-posta bildirimi: kullanicinin email_enabled tercihi acik VE
        # NotificationSettings.smtp_enabled ise atanan kisiye HTML mail.
        try:
            _send_assignment_email(
                db,
                recipient_username=new_assignee,
                kind="alarm",
                title=title,
                description=alarm.description,
                level=alarm.level,
                actor_username=actor_username,
                link_path=f"/alarms#alarm-{alarm.id}",
            )
        except Exception:  # noqa: BLE001
            import logging as _logging
            _logging.getLogger(__name__).exception("alarm_assignment_email_failed")
    db.commit()
    db.refresh(alarm)
    return alarm


def _send_assignment_email(
    db: Session,
    *,
    recipient_username: str,
    kind: str,
    title: str,
    description: str | None,
    level: str | None,
    actor_username: str | None,
    link_path: str | None = None,
) -> None:
    """Atama bildirimi (alarm/fault) icin HTML mail.

    Sistem ayarlarinda SMTP aktif VE kullanicinin email tercihi acik (default
    True) ise gonderilir. UserNotificationPreference satiri yoksa default
    web+email True kabul edilir."""
    from app.models.notification_settings import NotificationSettings
    from app.models.project_settings import ProjectSettings
    from app.models.user import User
    from app.models.user_notification_preference import UserNotificationPreference
    from app.services.email_templates import render_assignment_email
    from app.services.notification_test_service import send_smtp_test

    settings = db.scalar(select(NotificationSettings).limit(1))
    if settings is None or not settings.smtp_enabled:
        return
    user = db.scalar(select(User).where(User.username == recipient_username))
    if user is None or not user.email:
        return
    pref = db.get(UserNotificationPreference, user.id)
    # Default: email_enabled = True (kullanici acikca kapatmadi).
    email_enabled = True if pref is None else bool(pref.email_enabled)
    if not email_enabled:
        return
    proj = db.get(ProjectSettings, 1)
    project_title = (
        (proj.site_title or proj.project_name or proj.customer_name) if proj else None
    ) or None
    subject, html_body = render_assignment_email(
        project_title=project_title,
        kind=kind,
        recipient_full_name=user.full_name or user.username,
        title=title,
        description=description,
        level=level,
        actor_username=actor_username,
        link_path=link_path,
    )
    plain_text = (
        f"{title}\n\n"
        f"Atayan: {actor_username or '-'}\n"
        f"Aciklama: {description or '-'}\n"
    )
    send_smtp_test(
        settings,
        recipient_email=user.email,
        subject=subject,
        message=plain_text,
        html_body=html_body,
    )


def list_alarm_comments(db: Session, alarm_id: int) -> list[AlarmComment]:
    alarm = db.get(AlarmEvent, alarm_id)
    if alarm is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Alarm not found")
    stmt = select(AlarmComment).where(AlarmComment.alarm_event_id == alarm_id).order_by(AlarmComment.created_at.desc())
    return list(db.scalars(stmt).all())


def create_alarm_comment(db: Session, alarm_id: int, comment: str, current_user: User) -> AlarmComment:
    alarm = db.get(AlarmEvent, alarm_id)
    if alarm is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Alarm not found")
    comment_text = comment.strip()
    if not comment_text:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Comment cannot be empty")

    row = AlarmComment(
        alarm_event_id=alarm_id,
        author_username=current_user.username,
        comment=comment_text,
        created_at=datetime.now(timezone.utc),
    )
    db.add(row)
    record_event(
        db,
        category="alarm",
        event_type="alarm_comment_added",
        severity="info",
        actor_username=current_user.username,
        message=f"\"{alarm.title}\" alarmına yorum eklendi",
        metadata={"alarm_id": alarm.id},
    )
    # Yorum bildirimi: alarmin atandigi kullaniciya (yorum yazandan farkli ise).
    # Title: "Yeni yorum: <alarm adi>" -> bildirim panelinde eyebrow+main olur.
    if alarm.assigned_to and alarm.assigned_to != current_user.username:
        notif_title = f"Yeni yorum: {alarm.title}"
        create_notification(
            db,
            recipient_username=alarm.assigned_to,
            category="alarm_comment",
            severity="info",
            title=notif_title,
            body=comment_text,
            actor_username=current_user.username,
            link=f"/alarms#alarm-{alarm.id}",
            metadata={
                "alarm_id": alarm.id,
                "comment_id": None,
                "_title_i18n": {"key": "alarm_comment_new", "params": {"title": alarm.title}},
            },
        )
        # Email bildirimi: kullanici email tercihi acik VE SMTP enabled ise.
        try:
            _send_assignment_email(
                db,
                recipient_username=alarm.assigned_to,
                kind="alarm",
                title=notif_title,
                description=comment_text,
                level="info",
                actor_username=current_user.username,
                link_path=f"/alarms#alarm-{alarm.id}",
            )
        except Exception:  # noqa: BLE001
            import logging as _logging
            _logging.getLogger(__name__).exception("alarm_comment_email_failed")
    db.commit()
    db.refresh(row)
    return row


def acknowledge_alarm(db: Session, alarm_id: int, actor_username: str) -> AlarmEvent:
    alarm = db.get(AlarmEvent, alarm_id)
    if alarm is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Alarm not found")
    alarm.acknowledged = True
    alarm.acknowledged_at = datetime.now(timezone.utc)
    record_event(
        db,
        category="alarm",
        event_type="alarm_acknowledged",
        severity="info",
        actor_username=actor_username,
        message=f"\"{alarm.title}\" alarmı onaylandı",
        metadata={"alarm_id": alarm.id},
    )
    db.commit()
    db.refresh(alarm)
    return alarm


def reset_alarm(db: Session, alarm_id: int, actor_username: str) -> AlarmEvent:
    alarm = db.get(AlarmEvent, alarm_id)
    if alarm is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Alarm not found")
    alarm.reset = True
    alarm.reset_at = datetime.now(timezone.utc)
    record_event(
        db,
        category="alarm",
        event_type="alarm_reset",
        severity="warning",
        actor_username=actor_username,
        message=f"\"{alarm.title}\" alarmı resetlendi",
        metadata={"alarm_id": alarm.id},
    )
    db.commit()
    db.refresh(alarm)
    return alarm


def acknowledge_all_alarms(db: Session, actor_username: str) -> list[AlarmEvent]:
    alarms = list_alarm_events(db)
    now = datetime.now(timezone.utc)
    for alarm in alarms:
        alarm.acknowledged = True
        alarm.acknowledged_at = now
    record_event(
        db,
        category="alarm",
        event_type="alarm_acknowledge_all",
        severity="info",
        actor_username=actor_username,
        message="Tüm alarmlar onaylandı",
        metadata={"count": len(alarms)},
    )
    db.commit()
    return alarms


def reset_all_alarms(db: Session, actor_username: str) -> list[AlarmEvent]:
    alarms = list_alarm_events(db)
    now = datetime.now(timezone.utc)
    for alarm in alarms:
        alarm.reset = True
        alarm.reset_at = now
    record_event(
        db,
        category="alarm",
        event_type="alarm_reset_all",
        severity="warning",
        actor_username=actor_username,
        message="Tüm alarmlar resetlendi",
        metadata={"count": len(alarms)},
    )
    db.commit()
    return alarms


def delete_alarm(db: Session, alarm_id: int, actor_username: str) -> None:
    """Reset edilmis (normal'e donmus) bir alarmi sil. Acik alarm silinemez."""
    alarm = db.get(AlarmEvent, alarm_id)
    if alarm is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Alarm not found")
    if not alarm.reset:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Sadece resetlenmis (normal'e donmus) alarmlar silinebilir.",
        )
    alarm_title = alarm.title  # Sildikten sonra erişilemez; once kopyalayalim
    # Yorumlari da sil (FK guvenligi).
    db.query(AlarmComment).filter(AlarmComment.alarm_event_id == alarm_id).delete(synchronize_session=False)
    db.delete(alarm)
    record_event(
        db,
        category="alarm",
        event_type="alarm_deleted",
        severity="info",
        actor_username=actor_username,
        message=f"\"{alarm_title}\" alarmı silindi",
        metadata={"alarm_id": alarm_id, "title": alarm_title},
    )
    db.commit()


def handle_telemetry_alarm_event(db: Session, payload: dict) -> None:
    quality = (payload.get("quality") or "good").lower()
    is_fault = quality in {"bad", "offline", "invalid"}
    if not is_fault:
        return

    device_id = payload.get("device_id")
    device_name = payload.get("device_name") or payload.get("device_code") or "Cihaz"
    signal_key = payload.get("signal_key") or "unknown"
    existing_stmt = (
        select(AlarmEvent)
        .where(AlarmEvent.device_id == device_id)
        .where(AlarmEvent.reset.is_(False))
        .order_by(AlarmEvent.created_at.desc())
        .limit(1)
    )
    existing = db.scalar(existing_stmt)
    if existing is not None:
        return

    alarm = AlarmEvent(
        device_id=device_id,
        level="critical",
        title=f"{device_name} haberleşme alarmı",
        description=f"{signal_key} sinyalinde kalite '{quality}' olarak geldi.",
        created_at=datetime.now(timezone.utc),
    )
    db.add(alarm)
    record_event(
        db,
        category="alarm",
        event_type="alarm_created",
        severity="warning",
        device_code=payload.get("device_code"),
        message=f"{device_name} için otomatik alarm üretildi",
        metadata={"signal_key": signal_key, "quality": quality},
    )
    alarm_event_payload = {
        "message_id": str(uuid4()),
        "correlation_id": payload.get("correlation_id") or payload.get("message_id"),
        "device_id": device_id,
        "device_code": payload.get("device_code"),
        "device_name": device_name,
        "signal_key": signal_key,
        "quality": quality,
    }
    enqueue_outbox_event(
        db,
        topic="alarm.created",
        payload=alarm_event_payload,
        dedup_key=alarm_event_payload["message_id"],
    )
