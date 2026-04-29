from datetime import datetime, timezone
from uuid import uuid4

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.alarm import AlarmComment, AlarmEvent
from app.models.user import User
from app.services.event_service import record_event
from app.services.outbox_service import enqueue_outbox_event


def list_alarm_events(db: Session) -> list[AlarmEvent]:
    stmt = select(AlarmEvent).order_by(AlarmEvent.created_at.desc()).limit(500)
    return list(db.scalars(stmt).all())


def assign_alarm(db: Session, alarm_id: int, assigned_to: str | None, actor_username: str) -> AlarmEvent:
    alarm = db.get(AlarmEvent, alarm_id)
    if alarm is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Alarm not found")
    alarm.assigned_to = assigned_to.strip() if assigned_to else None
    record_event(
        db,
        category="alarm",
        event_type="alarm_assigned",
        severity="info",
        actor_username=actor_username,
        message=f"Alarm #{alarm.id} ataması güncellendi",
        metadata={"alarm_id": alarm.id, "assigned_to": alarm.assigned_to},
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
        message=f"Alarm #{alarm.id} için yorum eklendi",
        metadata={"alarm_id": alarm.id},
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
        message=f"Alarm #{alarm.id} onaylandı",
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
        message=f"Alarm #{alarm.id} resetlendi",
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
    # Yorumlari da sil (FK guvenligi).
    db.query(AlarmComment).filter(AlarmComment.alarm_event_id == alarm_id).delete(synchronize_session=False)
    db.delete(alarm)
    record_event(
        db,
        category="alarm",
        event_type="alarm_deleted",
        severity="info",
        actor_username=actor_username,
        message=f"Alarm #{alarm_id} silindi",
        metadata={"alarm_id": alarm_id},
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
