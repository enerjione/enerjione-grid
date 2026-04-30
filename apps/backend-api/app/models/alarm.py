from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class AlarmEvent(Base):
    __tablename__ = "alarm_events"

    id: Mapped[int] = mapped_column(primary_key=True)
    device_id: Mapped[int] = mapped_column(ForeignKey("devices.id"), index=True)
    level: Mapped[str] = mapped_column(String(30), index=True)
    title: Mapped[str] = mapped_column(String(255))
    description: Mapped[str] = mapped_column(String(1000))
    # Alarmin tetiklendigi sinyalin key'i. Kaynak (master/sat01/sat02) bilgisini
    # frontend prefix'ten turetir, boylece UI'da "Master / Sat 01 / Sat 02"
    # rozeti gosterilir. Eski kayitlar icin NULL olabilir.
    signal_key: Mapped[str | None] = mapped_column(String(120), nullable=True, index=True)
    assigned_to: Mapped[str | None] = mapped_column(String(120), nullable=True, index=True)
    acknowledged: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    reset: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    acknowledged_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, index=True)
    reset_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)


class AlarmComment(Base):
    __tablename__ = "alarm_comments"

    id: Mapped[int] = mapped_column(primary_key=True)
    alarm_event_id: Mapped[int] = mapped_column(ForeignKey("alarm_events.id"), index=True)
    author_username: Mapped[str] = mapped_column(String(120), index=True)
    comment: Mapped[str] = mapped_column(String(1000))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
