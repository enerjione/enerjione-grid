"""DB Yedekleme — model'ler.

İki tablo:
  - BackupJob: tek bir yedek dosyasinin metadata'si (manual / scheduled,
    durum, dosya yolu, boyut, hata mesaji).
  - BackupSchedule: periyodik yedek ayarlari (singleton id=1):
    enabled, interval_hours, retention_count.

Yedek dosyalari sunucu diskinde BACKUP_DIR (env, default
/var/lib/horstmann/backups) altinda tutulur. pg_dump ciktisi
custom format (.dump) — pg_restore ile geri yuklenir.
"""

from datetime import datetime, timezone

from sqlalchemy import Boolean, DateTime, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class BackupJob(Base):
    __tablename__ = "backup_jobs"

    id: Mapped[int] = mapped_column(primary_key=True)

    # "manual" | "scheduled"
    job_type: Mapped[str] = mapped_column(String(20), default="manual", index=True)

    # "running" | "success" | "failed"
    status: Mapped[str] = mapped_column(String(20), default="running", index=True)

    # Dosya yolu (mutlak); silindiyse None yapilabilir.
    file_path: Mapped[str | None] = mapped_column(String(500), nullable=True)
    size_bytes: Mapped[int | None] = mapped_column(Integer, nullable=True)

    # Hata mesaji (failed durumda)
    error_message: Mapped[str | None] = mapped_column(String(2000), nullable=True)

    # Olusturan kullanici (manual icin)
    created_by_username: Mapped[str | None] = mapped_column(String(120), nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        index=True,
        nullable=False,
    )
    completed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )


class BackupSchedule(Base):
    """Periyodik yedek ayarlari. Tek satir (id=1) olarak tutulur."""

    __tablename__ = "backup_schedule"

    id: Mapped[int] = mapped_column(primary_key=True, default=1)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    # Yedek alma araligi (saat). Min 1.
    interval_hours: Mapped[int] = mapped_column(Integer, nullable=False, default=24)
    # Tutulacak yedek sayisi (retention). En eski silinir.
    retention_count: Mapped[int] = mapped_column(Integer, nullable=False, default=7)
    # Son scheduled run zamani — interval kontrolu icin.
    last_run_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
