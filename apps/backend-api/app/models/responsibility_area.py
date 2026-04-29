"""Sorumluluk alanlari (responsibility areas).

Bir alan; bakim/operasyon ekibini temsil eder. Cihazlar ve kullanicilar bir
ya da birden fazla alana M2M olarak baglanir:

  - alarm tetiklendiginde bildirim, cihazin bagli oldugu alanlar(in
    kullanicilarina gider.
  - cihaz listesi alana gore filtrelenebilir.
  - mantiksal sorumluluk: bir cihaza birden fazla ekip (or. "Elektrik Bakim"
    + "Saha Operasyon") atanabilir.

Yetki: INSTALLER ve ENGINEER duzenleyebilir; OPERATOR sadece okur.
"""

from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, Table, Column
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


# M2M: Sorumluluk alani <-> Kullanici
responsibility_area_users = Table(
    "responsibility_area_users",
    Base.metadata,
    Column("area_id", Integer, ForeignKey("responsibility_areas.id", ondelete="CASCADE"), primary_key=True),
    Column("user_id", Integer, ForeignKey("users.id", ondelete="CASCADE"), primary_key=True),
)


# M2M: Sorumluluk alani <-> Cihaz
responsibility_area_devices = Table(
    "responsibility_area_devices",
    Base.metadata,
    Column("area_id", Integer, ForeignKey("responsibility_areas.id", ondelete="CASCADE"), primary_key=True),
    Column("device_id", Integer, ForeignKey("devices.id", ondelete="CASCADE"), primary_key=True),
)


class ResponsibilityArea(Base):
    __tablename__ = "responsibility_areas"

    id: Mapped[int] = mapped_column(primary_key=True, index=True)
    code: Mapped[str] = mapped_column(String(80), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(160))
    description: Mapped[str | None] = mapped_column(String(500), nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow)
