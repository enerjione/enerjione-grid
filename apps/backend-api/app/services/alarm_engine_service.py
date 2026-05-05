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
        else:
            title = f"Size yeni bir alarm atandı: {alarm.title}"
        create_notification(
            db,
            recipient_username=new_assignee,
            category="alarm_assignment",
            severity=alarm.level or "info",
            title=title,
            body=alarm.description,
            actor_username=actor_username,
            link=f"/alarms#alarm-{alarm.id}",
            metadata={"alarm_id": alarm.id, "level": alarm.level},
        )
    db.commit()
    db.refresh(alarm)
    return alarm


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
    # Yorum bildirimi: alarmin atandigi kullaniciya (yorum yazandan farkli ise)
    # ve onceki yorum sahiplerine — basit versiyonda sadece atanan kisiye.
    if alarm.assigned_to and alarm.assigned_to != current_user.username:
        create_notification(
            db,
            recipient_username=alarm.assigned_to,
            category="alarm_comment",
            severity="info",
            title=f"\"{alarm.title}\" alarmına yorum eklendi",
            body=comment_text,
            actor_username=current_user.username,
            link=f"/alarms#alarm-{alarm.id}",
            metadata={"alarm_id": alarm.id, "comment_id": None},
        )
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
