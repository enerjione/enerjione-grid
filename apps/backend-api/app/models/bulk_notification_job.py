"""Zamanlanmis (scheduled) toplu bildirim isi.

Operator UI'dan 'Sonra Gonder' diyerek ileri tarihli bildirim
olusturabilir; scheduler background thread her N saniyede bir
status='pending' + scheduled_at <= now olan kayitlari isler ve
status='sent' / 'failed' isaretler. Gonderme uygulamasi
bulk_notification_service'i kullanir.

Hedef snapshot: target_json — gonderim zamani hedef listesi degismis
olabilir (kullanici/ekip silinebilir). Snapshot iceriginden hareket
ettigimiz icin tutarli kalir; ops_manager filtre bulk service icinde
yeniden uygulanir.
"""
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.sql import func

from app.db.base import Base


class BulkNotificationJob(Base):
    __tablename__ = "bulk_notification_jobs"

    id: Mapped[int] = mapped_column(primary_key=True, index=True)
    # Ne zaman gonderilecek (UTC)
    scheduled_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)
    # pending / sent / failed / cancelled
    status: Mapped[str] = mapped_column(String(20), default="pending", nullable=False, index=True)

    subject: Mapped[str] = mapped_column(String(200))
    message: Mapped[str] = mapped_column(Text)
    channels: Mapped[str] = mapped_column(String(80), default="web")
    # JSON: {"user_ids": [...], "team_ids": [...], "send_to_all": bool, "actor_username": "..."}
    target_json: Mapped[str] = mapped_column(Text, default="{}", nullable=False)

    created_by_user_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    # Calistirildiktan sonra dolu (sent/failed isaretlendiginde)
    executed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # Sonuc ozet — kac kisiye ulastigi, hata varsa text
    result_summary: Mapped[str | None] = mapped_column(Text, nullable=True)
