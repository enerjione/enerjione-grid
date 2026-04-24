from sqlalchemy import Boolean, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class OutboundTarget(Base):
    __tablename__ = "outbound_targets"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(120), unique=True, index=True)
    # rest | mqtt | iec104  (ileride: modbus | opcua)
    protocol: Mapped[str] = mapped_column(String(20), index=True)
    # REST: base URL; MQTT: broker hostname; IEC 104: kullanilmiyor (listen_host/port).
    endpoint: Mapped[str] = mapped_column(String(500), default="")
    topic: Mapped[str | None] = mapped_column(String(255), nullable=True)
    event_filter: Mapped[str] = mapped_column(String(40), default="all", index=True)  # all | telemetry | alarm
    auth_header: Mapped[str | None] = mapped_column(String(255), nullable=True)
    auth_token: Mapped[str | None] = mapped_column(String(255), nullable=True)
    qos: Mapped[int] = mapped_column(Integer, default=0)
    retain: Mapped[bool] = mapped_column(Boolean, default=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, index=True)

    # IEC 60870-5-104 sunucu parametreleri. protocol='iec104' icin zorunlu;
    # digerlerinde yok sayilir.
    #   listen_host               : Server'in baglanacagi ag arayuzu (0.0.0.0 = tum).
    #   listen_port               : IEC 104 varsayilan TCP portu 2404.
    #   iec104_common_address     : ASDU Common Address (CA). Dis SCADA ile
    #                               onceden anlasilmis tek bir unite kimligi.
    #   iec104_ioa_device_stride  : Cihaz basina IOA blok buyuklugu
    #                               (absolute_ioa = device_index * stride + signal.offset).
    listen_host: Mapped[str | None] = mapped_column(String(255), nullable=True)
    listen_port: Mapped[int | None] = mapped_column(Integer, nullable=True)
    iec104_common_address: Mapped[int | None] = mapped_column(Integer, nullable=True)
    iec104_ioa_device_stride: Mapped[int | None] = mapped_column(Integer, nullable=True)
