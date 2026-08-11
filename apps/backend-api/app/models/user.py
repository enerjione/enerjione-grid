from datetime import datetime

from sqlalchemy import Boolean, DateTime, Enum, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.models.enums import UserRole


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True, index=True)
    username: Mapped[str] = mapped_column(String(80), unique=True, index=True)
    email: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    phone_number: Mapped[str | None] = mapped_column(String(32), nullable=True)
    full_name: Mapped[str] = mapped_column(String(255))
    # Profil fotografi — `data:image/...;base64,...` gomulu veri URI'si.
    #
    # NEDEN DOSYA DEGIL: ayri bir dosya deposu kalici bir birim (volume),
    # nginx'te statik bir yol, yedekleme/geri yukleme adimi ve silinmis
    # kullanicilarda artik dosya temizligi demek. Sistem on-prem ve kullanici
    # sayisi onlarla olculuyor; kucuk bir gomulu goruntu (istemci 192px'e
    # kuculteir, sunucu ~150 KB uzerini reddeder) yedege kendiliginden girer
    # ve hicbir ek altyapi gerektirmez.
    avatar_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    # NULL = davet edilmis ama henuz sifre belirlememis kullanici. Davet
    # token'i ile setup-password sayfasinda ilk sifresini belirleyince burasi
    # dolar. Login flow null hashed_password gorurse 401 doner (token sahibi
    # olmayan kimse giremez).
    hashed_password: Mapped[str | None] = mapped_column(String(255), nullable=True)
    role: Mapped[UserRole] = mapped_column(Enum(UserRole), nullable=False)
    # i18n: kullanicinin tercih ettigi arayuz dili (ISO 639-1: 'tr', 'en'...).
    # NULL = sistem default (tr).
    language: Mapped[str | None] = mapped_column(String(8), nullable=True)
    # Brute-force koruma: ardisik basarisiz login denemesi sayaci.
    failed_login_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    # Hesap kilit son zamani. NULL veya gecmis tarih = kilit yok.
    locked_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # Default password veya admin tarafindan reset edilmis sifre — kullanici
    # ilk login'de yeni sifre belirlemek ZORUNDA. /auth/me/change-password
    # disindaki mutating endpoint'lerde 428 (Precondition Required) doner.
    # seed_installer.py ChangeMe123! ile ilk admin'i yaratirken bu flag'i
    # true set eder; admin login olur olmaz frontend modal acar.
    must_change_password: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False
    )
    # Davet/sifre reset token'i — kullanici davet ile yaratildiginda admin
    # sifre belirlemeden link/email gonderir. Kullanici setup-password
    # sayfasinda token + yeni sifre POST eder; backend SHA-256 hash'i ile
    # eslestirip sifreyi set eder ve token'i temizler. Tek kullanim.
    password_reset_token_hash: Mapped[str | None] = mapped_column(
        String(128), nullable=True
    )
    password_reset_token_expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
