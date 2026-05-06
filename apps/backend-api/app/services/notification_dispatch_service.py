"""Alarm bildirimi fan-out servisi.

Yeni bir AlarmEvent oluştuğunda (POST /internal/alarms) cagrilir.

Akis:
  1) Alarmin device_id'sinden, ilgili kullanicilari bul
     (scope_service.get_users_in_scope_for_device).
     Engineer/Installer her zaman dahildir; Operator yalnizca cihaza
     dogrudan/dolayli (line/region uzerinden) sorumlu olduğu ekipteyse.
  2) Her kullanici icin tercihlerini oku (user_notification_preferences).
  3) Sistem cap'inda etkin olan kanallar (NotificationSettings) AND
     kullanicinin kendi tercihi acik olan kanallardan gönder:
       - Web: Notification kaydi olustur (zaten broadcast var; burada
         hedef kullanici icin kisisel olarak da ekliyoruz; bu UI'da
         "bildirimlerim" listesinde gorunmesini saglar).
       - Email: SMTP gateway uzerinden e-mail.
       - SMS: SMS gateway uzerinden cep.
  4) Hatalar yutulur (loglanır) — alarm yaratimini engellemez.
"""

from __future__ import annotations

import logging
from typing import Iterable

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.alarm import AlarmEvent
from app.models.device import Device
from app.models.notification_settings import NotificationSettings
from app.models.user import User
from app.models.user_notification_preference import UserNotificationPreference
from app.services.notification_service import create_notification
from app.services.notification_test_service import send_smtp_test, send_sms_test
from app.services.scope_service import get_users_in_scope_for_device

logger = logging.getLogger(__name__)


_LEVEL_RANK = {"info": 0, "warning": 1, "error": 2, "critical": 3}


def _level_rank(level: str | None) -> int:
    if not level:
        return 0
    return _LEVEL_RANK.get(level.lower(), 0)


def _get_pref(db: Session, user_id: int) -> UserNotificationPreference:
    pref = db.get(UserNotificationPreference, user_id)
    if pref is None:
        # Default: web ve email acik, sms kapali. Tabloya KAYDETMEYIZ —
        # dispatcher cagrisi sadece okumadir; kullanicinin tercih
        # gostermesi gerekene kadar default'larla calisir.
        pref = UserNotificationPreference(
            user_id=user_id,
            web_enabled=True,
            email_enabled=True,
            sms_enabled=False,
            min_level_rank=0,
        )
    return pref


def _system_settings(db: Session) -> NotificationSettings | None:
    return db.scalar(select(NotificationSettings).limit(1))


def _send_email_for_user(
    settings: NotificationSettings | None, user: User, alarm: AlarmEvent
) -> None:
    if settings is None or not settings.smtp_enabled:
        return
    if not user.email:
        return
    subject = f"[Alarm] {alarm.title}"
    body = f"{alarm.description}\n\nSeviye: {alarm.level}\nTarih: {alarm.created_at}"
    try:
        send_smtp_test(settings, recipient_email=user.email, subject=subject, message=body)
        logger.info("alarm_email_sent user=%s alarm_id=%d", user.username, alarm.id)
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "alarm_email_failed user=%s alarm_id=%d error=%s",
            user.username, alarm.id, exc,
        )


def _send_sms_for_user(
    settings: NotificationSettings | None, user: User, alarm: AlarmEvent
) -> None:
    if settings is None or not settings.sms_enabled:
        return
    if not user.phone_number:
        return
    msg = f"Alarm: {alarm.title} - {alarm.description}"[:300]
    try:
        send_sms_test(settings, recipient_phone=user.phone_number, message=msg)
        logger.info("alarm_sms_sent user=%s alarm_id=%d", user.username, alarm.id)
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "alarm_sms_failed user=%s alarm_id=%d error=%s",
            user.username, alarm.id, exc,
        )


def _create_personal_web_notification(
    db: Session, user: User, alarm: AlarmEvent
) -> None:
    # Ek olarak kullaniciya OZEL bir bildirim kaydı acariz; broadcast
    # zaten internal endpoint'te yaratiliyor ama kisiye ozel kayit
    # "bildirimlerim" listesinde isaretsiz gozukmesi icin gerekli.
    severity = (
        "critical" if (alarm.level or "").lower() == "critical"
        else "error" if (alarm.level or "").lower() in ("error", "high")
        else "warning"
    )
    try:
        create_notification(
            db,
            recipient_username=user.username,
            category="alarm",
            severity=severity,
            title=f"Alarm: {alarm.title}",
            body=alarm.description,
            link=f"/alarms#alarm-{alarm.id}",
            metadata={"alarm_id": alarm.id, "device_id": alarm.device_id},
        )
    except Exception:  # noqa: BLE001
        logger.exception("alarm_personal_web_notif_failed user=%s", user.username)


def dispatch_alarm_notifications(db: Session, alarm: AlarmEvent) -> None:
    """Verilen alarm icin ilgili kullanicilara web/email/sms gonder."""
    if alarm.device_id is None:
        return
    recipients = get_users_in_scope_for_device(db, alarm.device_id)
    if not recipients:
        logger.info("alarm_dispatch_no_recipients alarm_id=%d", alarm.id)
        return
    settings = _system_settings(db)
    alarm_rank = _level_rank(alarm.level)
    for user in recipients:
        pref = _get_pref(db, user.id)
        # Min seviye filtresi
        if alarm_rank < (pref.min_level_rank or 0):
            continue
        if pref.web_enabled:
            _create_personal_web_notification(db, user, alarm)
        if pref.email_enabled:
            _send_email_for_user(settings, user, alarm)
        if pref.sms_enabled:
            _send_sms_for_user(settings, user, alarm)


def _unused_for_imports(_d: Device, _i: Iterable):  # pragma: no cover
    return None
