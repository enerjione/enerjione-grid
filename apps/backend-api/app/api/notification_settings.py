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
    TelegramDiscoverChatsRequest,
    TelegramDiscoverChatsResult,
    TelegramDiscoveredChat,
    WhatsappWebGroupsResult,
    WhatsappWebQr,
    WhatsappWebStatus,
    WhatsappWebTestRequest,
)
from app.models.project_settings import ProjectSettings
from app.services import whatsapp_web_client_service
from app.services.email_templates import PRODUCT_NAME
from app.services.event_service import record_event
from app.services.notification_settings_service import get_or_create_notification_settings
from app.services.notification_test_service import (
    discover_telegram_chats,
    send_sms_test,
    send_smtp_test,
    send_telegram_test,
)
from app.services.secrets_vault import decrypt_secret, encrypt_secret


# Bu alanlar DB'de Fernet ile sifrelenmis (`enc:v1:...`) saklanir.
# update endpoint write'ta encrypt, read endpoint'inde decrypt yapilir.
# Idempotent re-encrypt: PUT'ta zaten sifreli gelen deger dokunulmaz (UI
# "***" gosterse de re-encrypt edip yeniden yazmamis olur).
_ENCRYPTED_FIELDS = (
    "smtp_password",
    "sms_api_key",
    "sms_account_sid",
    "telegram_bot_token",
)


def _brand_title(db: Session) -> str:
    """Test bildirimlerinde gorunecek ad.

    Ayni oncelik notification_dispatch_service._project_title ile: kurulumun
    kendi adi varsa o, yoksa urun adi. Test maili ile gercek alarm maili
    ayni gondericiden gelmeli — aksi halde operator test'i gorup "tamam"
    derken alarm baska bir isimle geliyor.
    """
    from app.services.notification_settings_service import resolve_sender_name

    return resolve_sender_name(db) or PRODUCT_NAME


def _decrypted_view(row):
    """Read tarafinda DB row'un sifreli alanlarini decrypt edip frontend'e
    yansiyacak hali doner. Pydantic `from_attributes` ile NotificationSettingsRead
    olusturulurken bu method'un cikti'sini geciriyoruz."""
    # SQLAlchemy row'i mutate etmiyoruz (transaction state'i bozulmasin);
    # dict snapshot uzerinde decrypt.
    snapshot = {c.name: getattr(row, c.name) for c in row.__table__.columns}
    for field in _ENCRYPTED_FIELDS:
        if field in snapshot:
            snapshot[field] = decrypt_secret(snapshot[field])
    return snapshot

router = APIRouter(prefix="/notification-settings", tags=["notification-settings"])


@router.get("", response_model=NotificationSettingsRead)
def get_notification_settings(
    _: User = Depends(require_role(UserRole.INSTALLER)),
    db: Session = Depends(get_db),
):
    row = get_or_create_notification_settings(db)
    return NotificationSettingsRead.model_validate(_decrypted_view(row))


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
    # Encrypt secret alanlari — Fernet (`enc:v1:...`). Idempotent: zaten
    # encrypted gelen deger re-encrypt edilmez.
    settings_row.smtp_password = encrypt_secret(payload.smtp_password)
    settings_row.smtp_from_email = payload.smtp_from_email
    settings_row.smtp_from_name = (payload.smtp_from_name or "").strip()
    settings_row.sms_enabled = payload.sms_enabled
    settings_row.sms_provider = payload.sms_provider
    settings_row.sms_api_url = payload.sms_api_url
    settings_row.sms_api_key = encrypt_secret(payload.sms_api_key)
    settings_row.sms_account_sid = encrypt_secret(payload.sms_account_sid)
    settings_row.sms_from_number = payload.sms_from_number
    settings_row.telegram_enabled = payload.telegram_enabled
    settings_row.telegram_bot_token = encrypt_secret(payload.telegram_bot_token)
    settings_row.telegram_chat_ids = payload.telegram_chat_ids
    settings_row.whatsapp_web_enabled = payload.whatsapp_web_enabled
    settings_row.whatsapp_web_group_jids = payload.whatsapp_web_group_jids
    settings_row.whatsapp_web_group_mode = payload.whatsapp_web_group_mode
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
    return NotificationSettingsRead.model_validate(_decrypted_view(settings_row))


@router.post("/test-smtp", response_model=NotificationTestResult)
def test_smtp_settings(
    payload: NotificationSmtpTestRequest,
    current_user: User = Depends(require_role(UserRole.INSTALLER)),
    db: Session = Depends(get_db),
):
    settings_row = get_or_create_notification_settings(db)
    # Marka: kurulumun kendi adi (Proje Ayarlari) varsa o, yoksa urun adi.
    # Eskiden "Horstman ..." yaziyordu — Horstmann izlenen CIHAZIN ureticisi,
    # bu yazilimin adi degil; test maili yanlis gonderenden gelmis gibi
    # gorunuyordu.
    brand = _brand_title(db)
    subject = payload.subject or f"{brand} SMTP Test"
    message = payload.message or f"Bu mesaj {brand} SMTP test gönderimidir."
    try:
        send_smtp_test(
            settings_row,
            recipient_email=payload.recipient_email,
            subject=subject,
            message=message,
            from_name=brand,
        )
        record_event(
            db,
            category="settings",
            event_type="notification_smtp_test_ok",
            severity="info",
            actor_username=current_user.username,
            message=f"SMTP test succeeded: {payload.recipient_email}",
            i18n_key="notification_smtp_test_ok",
            i18n_params={"recipient": payload.recipient_email},
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
            message=f"SMTP test failed: {ex}",
            i18n_key="notification_smtp_test_failed",
            i18n_params={"error": str(ex)},
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
    message = payload.message or f"Bu mesaj {_brand_title(db)} SMS test gönderimidir."
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
            message=f"SMS test succeeded: {payload.recipient_phone}",
            i18n_key="notification_sms_test_ok",
            i18n_params={"recipient": payload.recipient_phone},
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
            message=f"SMS test failed: {ex}",
            i18n_key="notification_sms_test_failed",
            i18n_params={"error": str(ex)},
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
    message = payload.message or f"{_brand_title(db)} Telegram test."
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
            message=f"Telegram test succeeded: chat={payload.chat_id}",
            i18n_key="notification_telegram_test_ok",
            i18n_params={"chat": payload.chat_id},
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
            message=f"Telegram test failed: {ex}",
            i18n_key="notification_telegram_test_failed",
            i18n_params={"error": str(ex)},
        )
        db.commit()
        return NotificationTestResult(ok=False, detail=f"Telegram test başarısız: {ex}")


@router.post("/discover-telegram-chats", response_model=TelegramDiscoverChatsResult)
def discover_chats(
    payload: TelegramDiscoverChatsRequest,
    _: User = Depends(require_role(UserRole.INSTALLER)),
    db: Session = Depends(get_db),
):
    """Telegram bot'a son yazilmis benzersiz chat'leri listeler.

    Kullanici 'Chat ID'leri otomatik bul' butonuna basinca cagrilir.
    Kullanim akisi:
      1) Bot'u BotFather'dan olustur, token'i ayara kaydet.
      2) Bildirim almak istedigin grup/kanal'a bot'u ekle veya bota
         direkt mesaj at (`/start` yeterli).
      3) Bu endpoint'i cagir; bot'a yazan tum benzersiz chat'lerin
         id + tip + baslik'i listelenir.
      4) Kullanici tikladigi chat'i 'Chat ID Listesi' alanina ekler.

    Webhook ayarli botlarda getUpdates kullanilamaz — bu durumda servis
    kullaniciya 'webhook'u kapatip tekrar deneyin' diye geri doner.
    """
    settings_row = get_or_create_notification_settings(db)
    # DB'de Fernet sifreli; getUpdates icin plaintext gerek.
    from app.services.notification_settings_service import decrypt_notification_credentials

    stored_token = decrypt_notification_credentials(settings_row).telegram_bot_token or ""
    token = (payload.bot_token or "").strip() or stored_token
    if not token:
        return TelegramDiscoverChatsResult(
            ok=False,
            detail="Telegram Bot Token tanımlı değil. Önce token'i kaydedin.",
            chats=[],
        )
    try:
        chats = discover_telegram_chats(token)
    except Exception as ex:  # noqa: BLE001
        return TelegramDiscoverChatsResult(ok=False, detail=str(ex), chats=[])
    if not chats:
        return TelegramDiscoverChatsResult(
            ok=True,
            detail=(
                "Bota henüz mesaj yazan kimse yok. Lütfen bota /start "
                "atın veya bildirim göndermek istediğiniz gruba bot'u "
                "ekleyip bir mesaj yazın, ardından tekrar deneyin."
            ),
            chats=[],
        )
    return TelegramDiscoverChatsResult(
        ok=True,
        detail=f"{len(chats)} chat bulundu.",
        chats=[TelegramDiscoveredChat(**c) for c in chats],
    )


@router.get("/whatsapp-web/status", response_model=WhatsappWebStatus)
def get_whatsapp_web_status(_: User = Depends(require_role(UserRole.INSTALLER))):
    return WhatsappWebStatus(**whatsapp_web_client_service.fetch_status())


@router.get("/whatsapp-web/qr", response_model=WhatsappWebQr)
def get_whatsapp_web_qr(_: User = Depends(require_role(UserRole.INSTALLER))):
    return WhatsappWebQr(**whatsapp_web_client_service.fetch_qr())


@router.get("/whatsapp-web/groups", response_model=WhatsappWebGroupsResult)
def get_whatsapp_web_groups(_: User = Depends(require_role(UserRole.INSTALLER))):
    return WhatsappWebGroupsResult(**whatsapp_web_client_service.fetch_groups())


@router.post("/whatsapp-web/test", response_model=NotificationTestResult)
def test_whatsapp_web(
    payload: WhatsappWebTestRequest,
    current_user: User = Depends(require_role(UserRole.INSTALLER)),
    db: Session = Depends(get_db),
):
    message = payload.message or "Bu mesaj EnerjiOne Grid WhatsApp test gönderimidir."
    try:
        whatsapp_web_client_service.send_test_message(payload.recipient_phone, message)
        record_event(
            db,
            category="settings",
            event_type="notification_whatsapp_web_test_ok",
            severity="info",
            actor_username=current_user.username,
            message=f"WhatsApp Web test succeeded: {payload.recipient_phone}",
            i18n_key="notification_whatsapp_web_test_ok",
            i18n_params={"recipient": payload.recipient_phone},
        )
        db.commit()
        return NotificationTestResult(ok=True, detail="WhatsApp test mesajı gönderildi.")
    except Exception as ex:
        record_event(
            db,
            category="settings",
            event_type="notification_whatsapp_web_test_failed",
            severity="error",
            actor_username=current_user.username,
            message=f"WhatsApp Web test failed: {ex}",
            i18n_key="notification_whatsapp_web_test_failed",
            i18n_params={"error": str(ex)},
        )
        db.commit()
        return NotificationTestResult(ok=False, detail=f"WhatsApp test başarısız: {ex}")


@router.post("/whatsapp-web/logout", response_model=NotificationTestResult)
def logout_whatsapp_web(
    current_user: User = Depends(require_role(UserRole.INSTALLER)),
    db: Session = Depends(get_db),
):
    try:
        whatsapp_web_client_service.logout()
        record_event(
            db,
            category="settings",
            event_type="notification_whatsapp_web_logout",
            severity="info",
            actor_username=current_user.username,
            message="WhatsApp Web oturumu kapatıldı",
            i18n_key="notification_whatsapp_web_logout",
        )
        db.commit()
        return NotificationTestResult(ok=True, detail="WhatsApp bağlantısı kesildi.")
    except Exception as ex:
        return NotificationTestResult(ok=False, detail=f"Bağlantı kesme başarısız: {ex}")
