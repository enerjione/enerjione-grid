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
    # Sinyal hangi cihaz modeline ait. Kullanici (cihaz formundan) modeli sectiginde,
    # bu cihaza ait okuma listesi sadece ayni `model` degerine sahip sinyallerden olur.
    model: Mapped[str] = mapped_column(String(80), default="horstmann_sn_2_0", index=True)
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
    #   iec104_ioa      : Sinyal icin mutlak IOA (Information Object Address, 24-bit).
    #                     Cihaz bazli ayrim sinyal duzeyinde DEGIL, ASDU Common
    #                     Address (cihazin `iec104_common_address` alani) ile
    #                     yapilir. Boylece tek bir TCP oturumunda farkli CA'lara
    #                     ait ASDU'lar yayinlanabilir.
    #   iec104_ioa_offset: Eski ad (geri uyumluluk). Yeni alan dolu degilse bu
    #                     mutlak IOA olarak okunur; her iki alan da NULL olan
    #                     sinyaller IEC 104 ile yayinlanmaz.
    iec104_type_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    iec104_ioa: Mapped[int | None] = mapped_column(Integer, nullable=True)
    iec104_ioa_offset: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # IEC 104 yayini sinyal bazinda gecici kapatma. Type ID + IOA dolu olsa bile
    # bu False ise outbound dispatcher bu sinyali atlar. Default True (yayinla).
    iec104_enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    # Modbus outbound adresleme. Kullanici outbound template uzerinden cihaza
    # standart bir Modbus adresi verir; cihaz instance'inin asdu/slave id'si
    # ile birlikte gercek adres turetilir.
    #   modbus_function: 3=holding register, 4=input register,
    #                    1=coil, 2=discrete input.
    modbus_function: Mapped[int | None] = mapped_column(Integer, nullable=True)
    modbus_address: Mapped[int | None] = mapped_column(Integer, nullable=True)

    # MQTT outbound topic suffix'i. Tam topic: "<base_topic>/<device_code>/<mqtt_topic>"
    # Ornek: "telemetry/voltage/phase_a"
    mqtt_topic: Mapped[str | None] = mapped_column(String(200), nullable=True)
