"""Kullanici basina bildirim kanal tercihleri — BILINCLI OPT-OUT.

Hangi alarmin hangi kanaldan cikacagini ALARM KURALI belirler
(`alarm_rules.notify_*`), kimin alacagini ise sorumluluk alani
(`scope_service.get_users_in_scope_for_device`). Bu tablo ucuncu bir kapi
degildir; yalnizca "ben bu kanaldan bildirim istemiyorum" diyen kullanici
icin vardir. Tum alanlarin varsayilani ACIK.

TARIHCE: sms/telegram/whatsapp varsayilani eskiden KAPALI'ydi (maliyet ve
opt-in gerekcesiyle) ve dispatcher "kuralda secili VE burada acik" seklinde
AND'liyordu. Ustelik satir, kullanici bildirim ekranini sadece ACTIGINDA
bile kapali degerlerle yaziliyordu. Sonuc: alarm kuralinda SMS/WhatsApp
isaretlenmis olmasina ragmen sahada hicbir bildirim gitmiyor, hicbir iz de
kalmiyordu. Bkz. migration 0047.
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
    sms_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    telegram_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    whatsapp_web_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    # Min seviye (info, warning, critical) — bu seviyeden DUSUK olanlar
    # bildirim olarak gonderilmez. Default "info" = hepsi.
    # Onumuzdeki suruda gerekirse kullanilir; simdilik info default.
    min_level_rank: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
