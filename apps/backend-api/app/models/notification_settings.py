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
    sms_provider: Mapped[str] = mapped_column(String(80), default="mock")
    sms_api_url: Mapped[str] = mapped_column(String(500), default="")
    sms_api_key: Mapped[str] = mapped_column(String(255), default="")
    # Telegram Bot API. Kanal/grup chat_id'leri virgulle ayrilmis liste.
    # Bot tokeni @BotFather'dan alinir; chat_id'ler @userinfobot veya
    # bot'un /getUpdates endpoint'inden ogrenilir.
    telegram_enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    telegram_bot_token: Mapped[str] = mapped_column(String(255), default="")
    telegram_chat_ids: Mapped[str] = mapped_column(String(2000), default="")
