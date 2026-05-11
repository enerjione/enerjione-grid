"""Kullanici basina bildirim kanal tercihleri.

Sistem cap'inda alarm bildirimleri uretilir; ancak hangi kanaldan
(web bildirim, e-posta, SMS, Telegram) gonderilecegi kullanicinin kendi
tercihine biraktir. Tabloda bir satir = bir kullanici icin tercih demetidir.

Default'lar (satir yoksa):
  web_enabled=True, email_enabled=True, sms_enabled=False,
  telegram_enabled=False
SMS ve Telegram varsayilan KAPALI cunku maliyet (SMS) ya da kullanicinin
bot'a hic mesaj atmamis olmasi durumunda (Telegram) anlamsiz bildirim
denenmesini onlemek icin opt-in mantigi tercih ettik.
"""

from sqlalchemy import Boolean, ForeignKey, Integer
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class UserNotificationPreference(Base):
    __tablename__ = "user_notification_preferences"

    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    web_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    email_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    sms_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    telegram_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    # Min seviye (info, warning, critical) — bu seviyeden DUSUK olanlar
    # bildirim olarak gonderilmez. Default "info" = hepsi.
    # Onumuzdeki suruda gerekirse kullanilir; simdilik info default.
    min_level_rank: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
