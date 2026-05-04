from datetime import datetime

from sqlalchemy import DateTime, Float, ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class Telemetry(Base):
    __tablename__ = "telemetry"

    id: Mapped[int] = mapped_column(primary_key=True)
    device_id: Mapped[int] = mapped_column(ForeignKey("devices.id"), index=True)
    signal_key: Mapped[str] = mapped_column(String(120), index=True)
    # DNP3 Group 110 (Octet String) sinyallerinde gateway numeric value yerine
    # value_string yollar; bu satirlarda value NULL olur. Numeric tipler icin
    # value her zaman dolu gelir.
    value: Mapped[float | None] = mapped_column(Float, nullable=True)
    value_string: Mapped[str | None] = mapped_column(Text, nullable=True)
    quality: Mapped[str] = mapped_column(String(50), default="good")
    source_timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True))
