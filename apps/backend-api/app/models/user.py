from sqlalchemy import Enum, String
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
    hashed_password: Mapped[str] = mapped_column(String(255))
    role: Mapped[UserRole] = mapped_column(Enum(UserRole), nullable=False)
    # i18n: kullanicinin tercih ettigi arayuz dili (ISO 639-1: 'tr', 'en'...).
    # NULL = sistem default (tr). Kullanici giris yaptiginda bu alan
    # frontend i18n'e yuklenir ve UI bu dile gore acilir.
    language: Mapped[str | None] = mapped_column(String(8), nullable=True)
