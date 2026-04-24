from sqlalchemy import Boolean, Float, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class SignalCatalog(Base):
    """Standart sinyal listesi.

    Sistemdeki tum cihazlar ayni sinyal setini okur. Kurulumcu (INSTALLER) rolu
    bu tabloyu yonetir; operator ve muhendis sadece okur. Her sinyalin DNP3
    okuma adresi burada tanimlidir; cihaz eklendiginde otomatik olarak bu liste
    uygulanir.

    Horstmann SN2 cihazinda sinyaller 3 kaynaktan gelir:
      - master     : ana unite (modem, ayarlar, pozisyon)
      - sat01      : Satellite 01 (1. fazin olcum/ariza bilgileri)
      - sat02      : Satellite 02 (2. fazin olcum/ariza bilgileri)
    Ayni sinyal ismi (ornegin "overcurrent_tripped") farkli kaynaklarda ayri
    key olarak tutulur ki alarmin hangi fazdan/uniteden geldigi karismasin.
    """

    __tablename__ = "signal_catalog"

    id: Mapped[int] = mapped_column(primary_key=True, index=True)
    key: Mapped[str] = mapped_column(String(120), unique=True, index=True)
    label: Mapped[str] = mapped_column(String(200))
    unit: Mapped[str | None] = mapped_column(String(40), nullable=True)
    description: Mapped[str | None] = mapped_column(String(500), nullable=True)

    # Kaynak ve DNP3 sinifi (event reporting)
    source: Mapped[str] = mapped_column(String(20), default="master", index=True)
    dnp3_class: Mapped[str] = mapped_column(String(20), default="Class 1")

    # DNP3 adresleme:
    #   1 = binary_input (G1), 10 = binary_output (G10),
    #   20 = counter (G20), 30 = analog_input (G30),
    #   40 = analog_output (G40), 110 = string (G110)
    data_type: Mapped[str] = mapped_column(String(20), default="analog")
    dnp3_object_group: Mapped[int] = mapped_column(Integer, default=30)
    dnp3_index: Mapped[int] = mapped_column(Integer, default=0)

    # Olcek ve ofset - ham deger * scale + offset = gercek deger
    scale: Mapped[float] = mapped_column(Float, default=1.0)
    offset: Mapped[float] = mapped_column(Float, default=0.0)

    supports_alarm: Mapped[bool] = mapped_column(Boolean, default=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    display_order: Mapped[int] = mapped_column(Integer, default=0)

    # IEC 60870-5-104 adresleme.
    #   iec104_type_id  : ASDU Type Identifier (1=M_SP_NA_1 binary,
    #                     13=M_ME_NC_1 analog short float, 15=M_IT_NA_1 counter).
    #                     string / desteklenmeyen tipler icin NULL.
    #   iec104_ioa_offset: Cihaz bloku icindeki goreli IOA (Information Object Address).
    #                     Server, cihaz index'ine `iec104_ioa_device_stride` carpar ve
    #                     bu offseti ekleyerek mutlak IOA'yi hesaplar.
    iec104_type_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    iec104_ioa_offset: Mapped[int | None] = mapped_column(Integer, nullable=True)
