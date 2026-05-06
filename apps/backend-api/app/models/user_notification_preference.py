"""Kullanici basina bildirim kanal tercihleri.

Sistem cap'inda alarm bildirimleri uretilir; ancak hangi kanaldan
(web bildirim, e-posta, SMS) gonderilecegi kullanicinin kendi tercihine
biraktir. Tabloda bir satir = bir kullanici icin tercih demetidir.

Default'lar (satir yoksa):
  web_enabled=True (her zaman acik), email_enabled=True, sms_enabled=False
SMS varsayilan KAPALI cunku maliyet ve istem disi mesajlasmayi onlemek
icin opt-in mantigi tercih ettik.
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

    # Min seviye (info, warning, critical) — bu seviyeden DUSUK olanlar
    # bildirim olarak gonderilmez. Default "info" = hepsi.
    # Onumuzdeki suruda gerekirse kullanilir; simdilik info default.
    min_level_rank: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
