"""Aktif kullanici oturumlari (JWT jti -> kullanici/IP/UA/timestamp).

Her basarili login bir UserSession satiri yaratir; jti = JWT token ID.
get_current_user her authenticated istekte last_seen_at'i 30sn debounce
ile guncelliye. Installer 'Aktif Oturumlar' sayfasinda gorur ve istedigi
oturumu atabilir (revoke + revoked_at set).

Schema:
  - jti: PK, JWT'nin unique kimliği
  - user_id: FK users(id)
  - ip_address: son gorulen client IP (proxy varsa X-Forwarded-For); login'de
    yazilir, sonraki isteklerde IP degistiyse guncellenir
  - user_agent: tarayici/cihaz tanimi (truncate 255)
  - login_at: oturum baslangici
  - last_seen_at: son istek zamani (debounce 30s)
  - expires_at: JWT'nin exp'i. Bu ani gecen satir artik "aktif" degildir —
    token dogrulanamaz. NULL = eski kayit (surum 0015 oncesi), bilinmiyor.
  - revoked_at: NULL = aktif, dolu = installer/logout ile sonlandirildi
  - revoked_by_user_id: kim revoke etti (audit)
"""
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.sql import func

from app.db.base import Base


class UserSession(Base):
    __tablename__ = "user_sessions"

    jti: Mapped[str] = mapped_column(String(64), primary_key=True)
    user_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    ip_address: Mapped[str | None] = mapped_column(String(64), nullable=True)
    user_agent: Mapped[str | None] = mapped_column(String(255), nullable=True)

    login_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    last_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False, index=True
    )
    # JWT exp — bu ani gecmis satirlar 'Aktif Oturumlar' listesinde
    # gosterilmez. Nullable: 0015 oncesi kayitlarda bilinmiyor.
    expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, index=True
    )

    # Revoke: dolu ise oturum sonlandi (logout veya installer 'at')
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    revoked_by_user_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
