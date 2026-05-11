from sqlalchemy import Boolean, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class NotificationSettings(Base):
    __tablename__ = "notification_settings"

    id: Mapped[int] = mapped_column(primary_key=True)
    smtp_enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    smtp_host: Mapped[str] = mapped_column(String(255), default="")
    smtp_port: Mapped[int] = mapped_column(Integer, default=25)
    smtp_username: Mapped[str] = mapped_column(String(255), default="")
    smtp_password: Mapped[str] = mapped_column(String(255), default="")
    smtp_from_email: Mapped[str] = mapped_column(String(255), default="")
    sms_enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    # 'mock' | 'netgsm' | 'twilio' | 'generic' — UI'da seclir.
    sms_provider: Mapped[str] = mapped_column(String(80), default="mock")
    # Generic JSON-POST sagayicilar (eski 'netgsm/jsonpost' davranisi) icin
    # endpoint URL; Twilio'da kullanilmaz (URL Twilio API'sinden hesaplanir).
    sms_api_url: Mapped[str] = mapped_column(String(500), default="")
    # Generic sagayicilarda API key; Twilio'da Auth Token olarak kullanilir.
    sms_api_key: Mapped[str] = mapped_column(String(255), default="")
    # Twilio'ya ozel: Account SID (AC.. ile baslayan) ve gonderen numara
    # (E.164 formatinda, orn. +14057769058). Diger sagayicilarda bos kalir.
    sms_account_sid: Mapped[str] = mapped_column(String(120), default="")
    sms_from_number: Mapped[str] = mapped_column(String(40), default="")
    # Twilio WhatsApp modu: True ise hem 'From' hem 'To' numaralarinin
    # basina otomatik 'whatsapp:' prefixi eklenir. Twilio WhatsApp business
    # 24 saat penceresi disinda template mesaj kullanir; bu durumda
    # sms_twilio_content_sid bos degilse Body yerine ContentSid + bos
    # ContentVariables ile request yapilir.
    sms_twilio_use_whatsapp: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    sms_twilio_content_sid: Mapped[str] = mapped_column(String(64), default="")
    # Telegram Bot API. Kanal/grup chat_id'leri virgulle ayrilmis liste.
    # Bot tokeni @BotFather'dan alinir; chat_id'ler @userinfobot veya
    # bot'un /getUpdates endpoint'inden ogrenilir.
    telegram_enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    telegram_bot_token: Mapped[str] = mapped_column(String(255), default="")
    telegram_chat_ids: Mapped[str] = mapped_column(String(2000), default="")
