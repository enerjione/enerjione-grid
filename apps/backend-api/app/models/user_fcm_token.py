"""Mobile push (FCM) icin kullanici cihaz token'lari.

Bir kullanicinin birden cok cihazi olabilir (telefon + tablet vb.), bu
yuzden user_id <-> token N:M degil 1:N olarak tutuluyor. Token gecersiz
oldugunda (FCM 404/UNREGISTERED) silmek icin son kullanim zamani da
takip ediyoruz.
"""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, String, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base


class UserFcmToken(Base):
    __tablename__ = "user_fcm_tokens"
    __table_args__ = (
        UniqueConstraint("token", name="uq_fcm_token"),
    )

    id: Mapped[int] = mapped_column(primary_key=True, index=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    token: Mapped[str] = mapped_column(String(255), nullable=False)
    platform: Mapped[str | None] = mapped_column(String(16), nullable=True)
    device_label: Mapped[str | None] = mapped_column(String(120), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    last_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
