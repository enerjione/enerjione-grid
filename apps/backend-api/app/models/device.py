from datetime import date, datetime

from sqlalchemy import Date, DateTime, Enum, Float, ForeignKey, Integer, JSON, String
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.models.enums import CommunicationStatus


class Device(Base):
    __tablename__ = "devices"

    id: Mapped[int] = mapped_column(primary_key=True, index=True)
    code: Mapped[str] = mapped_column(String(50), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(255))
    # Cihazin seri numarasi — config dosya adinin (`<seri>_Configuration.csv`)
    # ve FTP eslestirmesinin BIRINCIL kaynagi. Kurulumda elle girilir; cihaz
    # baglaninca `master.serial_number` telemetrisinden OTOMATIK guncellenir
    # (bkz. telemetry_consumer). Salt telemetriye guvenmek sahada kirildi:
    # cihaz bir an seri=0 gonderdi ve sistem `0_Configuration.csv` uretti.
    serial_number: Mapped[str | None] = mapped_column(
        String(20), nullable=True, index=True
    )
    description: Mapped[str | None] = mapped_column(String(500), nullable=True)
    model: Mapped[str] = mapped_column(String(80), default="horstmann_sn_2_0", index=True)
    installation_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    gateway_code: Mapped[str | None] = mapped_column(
        ForeignKey("gateways.code", ondelete="RESTRICT", onupdate="CASCADE"),
        nullable=True,
        index=True,
    )
    ip_address: Mapped[str] = mapped_column(String(120))
    dnp3_outstation_port: Mapped[int] = mapped_column(Integer, default=20001)
    dnp3_address: Mapped[int] = mapped_column(Integer, default=1)
    dnp3_extended: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    poll_interval_sec: Mapped[int] = mapped_column(Integer, default=2)
    timeout_ms: Mapped[int] = mapped_column(Integer, default=3000)
    retry_count: Mapped[int] = mapped_column(Integer, default=2)
    signal_profile: Mapped[str] = mapped_column(String(80), default="horstmann_sn2_fixed")
    latitude: Mapped[float] = mapped_column(Float)
    longitude: Mapped[float] = mapped_column(Float)
    battery_percent: Mapped[float] = mapped_column(Float, default=100.0)
    communication_status: Mapped[CommunicationStatus] = mapped_column(
        Enum(CommunicationStatus), default=CommunicationStatus.UNKNOWN
    )
    alarm_active: Mapped[bool] = mapped_column(default=False)
    last_update_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    # IEC 60870-5-104 ASDU Common Address (CA). Outbound IEC 104 servisi
    # bu cihazdan gelen sinyalleri ASDU yayinlayacaginda hangi CA ile
    # paketleyecegini buradan ogrenir. NULL ise outbound target'in
    # `iec104_common_address` (default) degeri kullanilir.
    iec104_common_address: Mapped[int | None] = mapped_column(Integer, nullable=True)
