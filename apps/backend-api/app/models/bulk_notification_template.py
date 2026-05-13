"""Toplu bildirim sablonu (kaydedilmis mesaj/kanal/hedef seti).

Operator tekrar tekrar ayni mesaji yazmak yerine sablon kaydedip
sonradan tek tikla yukleyebilir. Hedef listesi opsiyonel — sablon
sadece mesaj+kanal'i tutabilir, hedef her seferinde manuel secilir.

Idempotent — `name` unique, ayni isim varsa hata.
"""
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.sql import func

from app.db.base import Base


class BulkNotificationTemplate(Base):
    __tablename__ = "bulk_notification_templates"

    id: Mapped[int] = mapped_column(primary_key=True, index=True)
    name: Mapped[str] = mapped_column(String(120), unique=True, index=True)
    subject: Mapped[str] = mapped_column(String(200))
    message: Mapped[str] = mapped_column(Text)
    # CSV: 'web,email,sms' subset.
    channels: Mapped[str] = mapped_column(String(80), default="web")
    # Hedef snapshot (JSON metin): {"user_ids": [...], "team_ids": [...], "send_to_all": bool}.
    # NULL = sablon hedef icermez (operator her seferinde manuel secer).
    target_json: Mapped[str | None] = mapped_column(Text, nullable=True)

    created_by_user_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )
