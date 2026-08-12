from dataclasses import dataclass

from sqlalchemy.orm import Session

from app.core.config import settings
from app.models.notification_settings import NotificationSettings
from app.services.secrets_vault import decrypt_secret, encrypt_secret


@dataclass
class DecryptedNotificationCredentials:
    """Dispatcher icin plaintext snapshot. SQLAlchemy row'unu MUTATE ETMEZ;
    DB'de hala `enc:v1:...` formatinda kalir. Sadece SMTP/Twilio/Telegram
    bind etmek icin geciici plaintext kopyasi."""

    smtp_password: str | None
    sms_api_key: str | None
    sms_account_sid: str | None
    telegram_bot_token: str | None


def decrypt_notification_credentials(row: NotificationSettings) -> DecryptedNotificationCredentials:
    """DB row'undaki sifreli credential alanlarini decrypt edip dataclass'ta
    doner. Row dokunulmaz — dispatch sonrasi sifreli haliyle kalir."""
    return DecryptedNotificationCredentials(
        smtp_password=decrypt_secret(row.smtp_password),
        sms_api_key=decrypt_secret(row.sms_api_key),
        sms_account_sid=decrypt_secret(row.sms_account_sid),
        telegram_bot_token=decrypt_secret(row.telegram_bot_token),
    )


def resolve_sender_name(db: Session) -> str | None:
    """Mektupta gorunecek GONDEREN ADI — tek kaynak.

    Sira:
        1. Mail Ayarlari > Gonderen Adi   (`smtp_from_name`)  <- ACIK secim
        2. Proje Ayarlari zinciri         (site_title -> project_name ->
                                           customer_name)      <- YEDEK
        3. None -> cagiran taraf `PRODUCT_NAME`a duser

    NEDEN TEK FONKSIYON: bu ad hem `From` basligindaki gorunen adi hem
    mektup basligindaki markayi belirliyor ve DORT ayri yerden cagriliyordu
    (alarm e-postasi, atama e-postasi, bildirim dagitimi, test gonderimi).
    Her biri zinciri kendi kopyasindan hesapliyordu; biri guncellenip digeri
    unutulsa ayni kurulum farkli mektuplarda farkli isimle gorunurdu ve bu
    hicbir yerde hata uretmezdi.

    ZINCIR NEDEN DURUYOR: alan bos olan mevcut kurulumlarda gonderen adi
    DEGISMEMELI. Aksi halde bu surumle birlikte musteriye giden mektuplarin
    adi kimse istemeden degisirdi.
    """
    row = db.get(NotificationSettings, 1)
    acik = (getattr(row, "smtp_from_name", "") or "").strip() if row else ""
    if acik:
        return acik

    # Yedek: Proje Ayarlari. Import BURADA — modul seviyesinde yapilirsa
    # project_settings <-> notification_settings dairesel importu olusur.
    from app.models.project_settings import ProjectSettings

    proj = db.get(ProjectSettings, 1)
    if proj is None:
        return None
    return (proj.site_title or proj.project_name or proj.customer_name) or None


def get_or_create_notification_settings(db: Session) -> NotificationSettings:
    settings_row = db.get(NotificationSettings, 1)
    if settings_row is not None:
        return settings_row

    # Initial seed — env'den gelen plaintext SMTP password/sms api key
    # Fernet ile sifrelenip DB'ye yazilir.
    settings_row = NotificationSettings(
        id=1,
        smtp_enabled=settings.smtp_enabled,
        smtp_host=settings.smtp_host,
        smtp_port=settings.smtp_port,
        smtp_username=settings.smtp_username,
        smtp_password=encrypt_secret(settings.smtp_password) if settings.smtp_password else "",
        smtp_from_email=settings.smtp_from_email,
        sms_enabled=settings.sms_enabled,
        sms_provider=settings.sms_provider,
        sms_api_url=settings.sms_api_url,
        sms_api_key=encrypt_secret(settings.sms_api_key) if settings.sms_api_key else "",
    )
    db.add(settings_row)
    db.commit()
    db.refresh(settings_row)
    return settings_row
