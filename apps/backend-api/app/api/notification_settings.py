from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.api.deps import require_role
from app.db.session import get_db
from app.models.enums import UserRole
from app.models.user import User
from app.schemas.notification import (
    NotificationSettingsRead,
    NotificationSettingsUpdate,
    NotificationSmsTestRequest,
    NotificationSmtpTestRequest,
    NotificationTelegramTestRequest,
    NotificationTestResult,
)
from app.services.event_service import record_event
from app.services.notification_settings_service import get_or_create_notification_settings
from app.services.notification_test_service import (
    send_sms_test,
    send_smtp_test,
    send_telegram_test,
)

router = APIRouter(prefix="/notification-settings", tags=["notification-settings"])


@router.get("", response_model=NotificationSettingsRead)
def get_notification_settings(
    _: User = Depends(require_role(UserRole.INSTALLER)),
    db: Session = Depends(get_db),
):
    return get_or_create_notification_settings(db)


@router.put("", response_model=NotificationSettingsRead)
def update_notification_settings(
    payload: NotificationSettingsUpdate,
    current_user: User = Depends(require_role(UserRole.INSTALLER)),
    db: Session = Depends(get_db),
):
    settings_row = get_or_create_notification_settings(db)
    settings_row.smtp_enabled = payload.smtp_enabled
    settings_row.smtp_host = payload.smtp_host
    settings_row.smtp_port = payload.smtp_port
    settings_row.smtp_username = payload.smtp_username
    settings_row.smtp_password = payload.smtp_password
    settings_row.smtp_from_email = payload.smtp_from_email
    settings_row.sms_enabled = payload.sms_enabled
    settings_row.sms_provider = payload.sms_provider
    settings_row.sms_api_url = payload.sms_api_url
    settings_row.sms_api_key = payload.sms_api_key
    settings_row.telegram_enabled = payload.telegram_enabled
    settings_row.telegram_bot_token = payload.telegram_bot_token
    settings_row.telegram_chat_ids = payload.telegram_chat_ids
    record_event(
        db,
        category="settings",
        event_type="notification_settings_updated",
        severity="info",
        actor_username=current_user.username,
        message="Bildirim ayarları güncellendi",
        i18n_key="notification_settings_updated",
    )
    db.commit()
    db.refresh(settings_row)
    return settings_row


@router.post("/test-smtp", response_model=NotificationTestResult)
def test_smtp_settings(
    payload: NotificationSmtpTestRequest,
    current_user: User = Depends(require_role(UserRole.INSTALLER)),
    db: Session = Depends(get_db),
):
    settings_row = get_or_create_notification_settings(db)
    subject = payload.subject or "Horstman SMTP Test"
    message = payload.message or "Bu mesaj Horstman Smart Logger SMTP test gönderimidir."
    try:
        send_smtp_test(
            settings_row,
            recipient_email=payload.recipient_email,
            subject=subject,
            message=message,
        )
        record_event(
            db,
            category="settings",
            event_type="notification_smtp_test_ok",
            severity="info",
            actor_username=current_user.username,
            message=f"SMTP test başarılı: {payload.recipient_email}",
        )
        db.commit()
        return NotificationTestResult(ok=True, detail="SMTP test mesajı gönderildi.")
    except Exception as ex:
        record_event(
            db,
            category="settings",
            event_type="notification_smtp_test_failed",
            severity="error",
            actor_username=current_user.username,
            message=f"SMTP test başarısız: {ex}",
        )
        db.commit()
        return NotificationTestResult(ok=False, detail=f"SMTP test başarısız: {ex}")


@router.post("/test-sms", response_model=NotificationTestResult)
def test_sms_settings(
    payload: NotificationSmsTestRequest,
    current_user: User = Depends(require_role(UserRole.INSTALLER)),
    db: Session = Depends(get_db),
):
    settings_row = get_or_create_notification_settings(db)
    message = payload.message or "Bu mesaj Horstman Smart Logger SMS test gönderimidir."
    try:
        send_sms_test(
            settings_row,
            recipient_phone=payload.recipient_phone,
            message=message,
        )
        record_event(
            db,
            category="settings",
            event_type="notification_sms_test_ok",
            severity="info",
            actor_username=current_user.username,
            message=f"SMS test başarılı: {payload.recipient_phone}",
        )
        db.commit()
        detail = (
            "SMS test isteği gönderildi."
            if settings_row.sms_provider != "mock"
            else "SMS sağlayıcı 'mock' olduğu için test yerel olarak başarılı sayıldı."
        )
        return NotificationTestResult(ok=True, detail=detail)
    except Exception as ex:
        record_event(
            db,
            category="settings",
            event_type="notification_sms_test_failed",
            severity="error",
            actor_username=current_user.username,
            message=f"SMS test başarısız: {ex}",
        )
        db.commit()
        return NotificationTestResult(ok=False, detail=f"SMS test başarısız: {ex}")


@router.post("/test-telegram", response_model=NotificationTestResult)
def test_telegram_settings(
    payload: NotificationTelegramTestRequest,
    current_user: User = Depends(require_role(UserRole.INSTALLER)),
    db: Session = Depends(get_db),
):
    settings_row = get_or_create_notification_settings(db)
    message = payload.message or "Horstman Smart Logger Telegram test."
    try:
        send_telegram_test(
            settings_row,
            chat_id=payload.chat_id,
            message=message,
            parse_mode="HTML",
        )
        record_event(
            db,
            category="settings",
            event_type="notification_telegram_test_ok",
            severity="info",
            actor_username=current_user.username,
            message=f"Telegram test başarılı: chat={payload.chat_id}",
        )
        db.commit()
        return NotificationTestResult(ok=True, detail="Telegram test mesajı gönderildi.")
    except Exception as ex:
        record_event(
            db,
            category="settings",
            event_type="notification_telegram_test_failed",
            severity="error",
            actor_username=current_user.username,
            message=f"Telegram test başarısız: {ex}",
        )
        db.commit()
        return NotificationTestResult(ok=False, detail=f"Telegram test başarısız: {ex}")
