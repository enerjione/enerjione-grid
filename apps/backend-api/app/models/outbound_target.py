from sqlalchemy import Boolean, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base


# MQTT topic template — operator UI'da degisiklik olmazsa bu kullanilir.
# Variables: {prefix} {customer} {device} {source} {datatype} {signal}
# prefix      -> mqtt_topic_prefix (default 'e1')
# customer    -> mqtt_customer_id (operator UI'da girer; bos ise 'default')
# device      -> Device.code (DEV-001 vb.)
# source      -> signal_key prefix (master / sat01 / sat02)
# datatype    -> SignalCatalog.data_type kategori (analog / binary / counter / string)
# signal      -> signal_key (master.voltage_a)
DEFAULT_MQTT_TOPIC_TEMPLATE = "{prefix}/{customer}/{device}/{source}/{datatype}/telemetry"


class OutboundTarget(Base):
    __tablename__ = "outbound_targets"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(120), unique=True, index=True)
    # rest | mqtt | iec104 | modbus  (ileride: opcua)
    protocol: Mapped[str] = mapped_column(String(20), index=True)
    # REST: base URL; MQTT: broker hostname; IEC 104: kullanilmiyor (listen_host/port).
    endpoint: Mapped[str] = mapped_column(String(500), default="")
    # MQTT: LEGACY tek-topic field. Yeni davranis: bos birakilirsa per-device
    # topic mqtt_topic_template'den uretilir. Geri uyumluluk icin destekleniyor.
    topic: Mapped[str | None] = mapped_column(String(255), nullable=True)
    event_filter: Mapped[str] = mapped_column(String(40), default="all", index=True)  # all | telemetry | alarm
    # REST icin (header adi + degeri); MQTT icin Authorization yerine
    # mqtt_username/mqtt_password kullanilir.
    auth_header: Mapped[str | None] = mapped_column(String(255), nullable=True)
    auth_token: Mapped[str | None] = mapped_column(String(255), nullable=True)
    qos: Mapped[int] = mapped_column(Integer, default=0)
    retain: Mapped[bool] = mapped_column(Boolean, default=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, index=True)

    # ---- MQTT-specific (protocol='mqtt' icin geçerli) ----
    # Broker port (TCP 1883 / TLS 8883 default). None ise paho default kullanir.
    mqtt_port: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # Auth (broker username/password). Bos ise anonim baglanir.
    mqtt_username: Mapped[str | None] = mapped_column(String(255), nullable=True)
    mqtt_password: Mapped[str | None] = mapped_column(String(500), nullable=True)
    # Broker'da unique client tanimlayicisi. Bos ise auto: 'e1-{name}-{hex}'.
    mqtt_client_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    # TLS bayraklari. tls_enabled=true ise ssl baglanti.
    mqtt_tls_enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    # tls_insecure=true -> hostname/cert validasyonu yapilmaz (self-signed dev).
    mqtt_tls_insecure: Mapped[bool] = mapped_column(Boolean, default=False)
    # CA cert + client cert/key dosya yollari (host'ta dosya olarak). Bos ise sistem
    # ca-certificates kullanilir.
    mqtt_tls_ca_path: Mapped[str | None] = mapped_column(String(500), nullable=True)
    mqtt_tls_cert_path: Mapped[str | None] = mapped_column(String(500), nullable=True)
    mqtt_tls_key_path: Mapped[str | None] = mapped_column(String(500), nullable=True)
    # Connection lifecycle (saniye)
    mqtt_keepalive_sec: Mapped[int] = mapped_column(Integer, default=60)
    mqtt_connect_timeout_sec: Mapped[int] = mapped_column(Integer, default=10)
    # Per-target publish periyodu. Bu pencerede biriken son snapshot publish
    # edilir. 0 = anlik publish (eski davranis).
    mqtt_publish_interval_sec: Mapped[int] = mapped_column(Integer, default=10)
    # Topic template — DEFAULT_MQTT_TOPIC_TEMPLATE override. Bos ise default.
    mqtt_topic_template: Mapped[str | None] = mapped_column(String(500), nullable=True)
    # 'Customer' identifier (topic'te {customer} placeholder'i icin).
    mqtt_topic_prefix: Mapped[str] = mapped_column(String(60), default="e1")
    mqtt_customer_id: Mapped[str | None] = mapped_column(String(120), nullable=True)

    # IEC 60870-5-104 sunucu parametreleri. protocol='iec104' icin zorunlu;
    # digerlerinde yok sayilir.
    #   listen_host               : Server'in baglanacagi ag arayuzu (0.0.0.0 = tum).
    #   listen_port               : IEC 104 varsayilan TCP portu 2404.
    #   iec104_common_address     : Default ASDU Common Address. Cihazlarin
    #                               `iec104_common_address` alani NULL ise bu
    #                               kullanilir. Cihaza ozel CA atandiginda tek
    #                               TCP oturumunda farkli CA'lara ait ASDU'lar
    #                               birlikte yayinlanir.
    #   iec104_ioa_device_stride  : DEPRECATED. Eski "device_index * stride +
    #                               signal_offset" modelinin kalintisi; yeni
    #                               kayitlar bos birakmali. Sadece eski
    #                               deploylar icin geri uyumluluk.
    listen_host: Mapped[str | None] = mapped_column(String(255), nullable=True)
    listen_port: Mapped[int | None] = mapped_column(Integer, nullable=True)
    iec104_common_address: Mapped[int | None] = mapped_column(Integer, nullable=True)
    iec104_ioa_device_stride: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # IP allowlist — virgulle ayrilmis IP'ler. Bos string veya NULL = serbest
    # (her IP baglanabilir). Dolu ise sadece listedeki IP'lerden TCP kabul edilir.
    iec104_allowed_peers: Mapped[str | None] = mapped_column(String(2000), nullable=True)

    # ---- Modbus TCP sunucu parametreleri (protocol='modbus') ----
    # Adresleme iki modda calisir:
    #
    #   mode='block' (1. yol) — TEK unit id, cihazlar adres bloklarina dagilir.
    #     Cihaz N'in register adresi: modbus_base_address + N*stride + offset
    #     Ornek stride=100: DEV-001 -> 0-99, DEV-002 -> 100-199, ...
    #     Kapasite: 65536/stride cihaz (stride=100 -> 655).
    #
    #   mode='unit' (2. yol) — her cihaz kendi Modbus unit (slave) id'sinde,
    #     hepsi AYNI offset duzenini kullanir (0'dan baslar). SCADA'da her
    #     cihaz ayri slave gibi gorunur. Kapasite: port basina 247 cihaz.
    #
    # Hangi modda olursa olsun cihaz -> (unit_id, block_start) esleme
    # `outbound_modbus_slots` tablosunda KALICI tutulur; boylece araya cihaz
    # eklenip cikarildiginda mevcut adresler KAYMAZ (SCADA tarafi bozulmaz).
    modbus_mode: Mapped[str] = mapped_column(String(10), default="block")
    # block modunda tum cihazlarin paylastigi unit id (SCADA'nin sordugu slave).
    modbus_unit_id: Mapped[int] = mapped_column(Integer, default=1)
    # Analog deger formati: int16 (scale/offset ile olceklenir) | float32 (2 word).
    modbus_value_format: Mapped[str] = mapped_column(String(10), default="int16")
    # float32'de register sirasi: big = ABCD (yuksek word once), little = CDAB.
    # Cok sayida SCADA/PLC word-swap bekler; bu yuzden ayar olarak duruyor.
    modbus_word_order: Mapped[str] = mapped_column(String(10), default="big")
    # block modunda cihaz basina ayrilan register sayisi. NULL = otomatik
    # (int16 -> 100, float32 -> 200). 100 secilirse cihazin tum register'lari
    # TEK Modbus okumasinda alinir (FC3/FC4 istek basina 125 register siniri).
    modbus_block_stride: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # Ilk cihazin blogunun basladigi adres (0 tabanli).
    modbus_base_address: Mapped[int] = mapped_column(Integer, default=0)
    # IP allowlist (CSV). Modbus'ta kimlik dogrulama YOKTUR; salt-okunur olsa
    # bile veriyi kimin gorebilecegi yalnizca bununla sinirlanir.
    modbus_allowed_peers: Mapped[str | None] = mapped_column(String(2000), nullable=True)

    # Custom topic mappings — operator UI'da "X cihaz + Y sinyal -> Z topic"
    # ekleyebilir. Default template'i bypass eder.
    topic_mappings: Mapped[list["OutboundTopicMapping"]] = relationship(
        back_populates="target", cascade="all, delete-orphan"
    )
    modbus_slots: Mapped[list["OutboundModbusSlot"]] = relationship(
        back_populates="target", cascade="all, delete-orphan"
    )


class OutboundModbusSlot(Base):
    """Bir Modbus hedefinde cihaz -> (unit id, blok baslangici) kalici eslemesi.

    NEDEN KALICI: adresler cihaz listesinden her seferinde yeniden turetilseydi,
    aradan bir cihaz silindiginde sonraki tum cihazlarin adresi kayardi ve
    SCADA'daki tum etiketler sessizce yanlis noktayi gosterirdi. Slot bir kez
    atanir ve cihaz silinene kadar sabit kalir; bosalan slot yeni cihaza
    verilebilir (planlayici en kucuk bos slotu secer).
    """

    __tablename__ = "outbound_modbus_slots"

    id: Mapped[int] = mapped_column(primary_key=True)
    target_id: Mapped[int] = mapped_column(
        ForeignKey("outbound_targets.id", ondelete="CASCADE"), index=True
    )
    device_id: Mapped[int] = mapped_column(
        ForeignKey("devices.id", ondelete="CASCADE"), index=True
    )
    # block modunda: kacinci blok (0,1,2...). unit modunda: kullanilmaz (0).
    slot_index: Mapped[int] = mapped_column(Integer, default=0)
    # SCADA'nin sordugu Modbus unit (slave) id. block modunda hepsi ayni,
    # unit modunda cihaz basina farkli (1..247).
    unit_id: Mapped[int] = mapped_column(Integer, default=1)
    # Register/bit bloklarinin baslangic adresi. unit modunda 0.
    block_start: Mapped[int] = mapped_column(Integer, default=0)

    target: Mapped["OutboundTarget"] = relationship(back_populates="modbus_slots")

    __table_args__ = (
        UniqueConstraint("target_id", "device_id", name="uq_modbus_slot_target_device"),
    )


class OutboundTopicMapping(Base):
    """MQTT outbound target icin custom topic mapping.

    Operator UI: "Custom Topic Mapping" modal'inda her satir bir mapping.
      - topic: tam topic string (template degil, sabit veya kismi degisken)
      - device_codes: virgulle ayrilmis cihaz kodlari (bos = tum cihazlar)
      - signal_keys: virgulle ayrilmis sinyal anahtarlari (bos = tum sinyaller)

    Resolver: bir telemetry reading geldiginde once mapping listesinde match
    aranir; eslesme bulunursa o topic'e publish edilir, default template
    devre disi. Birden fazla mapping ayni reading'i yakalarsa hepsine publish.
    """
    __tablename__ = "outbound_topic_mappings"

    id: Mapped[int] = mapped_column(primary_key=True)
    target_id: Mapped[int] = mapped_column(
        ForeignKey("outbound_targets.id", ondelete="CASCADE"), index=True
    )
    # MQTT topic — sabit string (operator yazar). Template variable'larini
    # icerebilir: {device} {source} {signal} runtime resolve edilir.
    topic: Mapped[str] = mapped_column(String(500))
    # Hedef cihazlar (CSV). Bos string = tum cihazlar.
    device_codes: Mapped[str] = mapped_column(Text, default="")
    # Hedef sinyaller (CSV). Bos string = tum sinyaller.
    signal_keys: Mapped[str] = mapped_column(Text, default="")
    # QoS / retain override — bos ise target default.
    qos: Mapped[int | None] = mapped_column(Integer, nullable=True)
    retain: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)

    target: Mapped["OutboundTarget"] = relationship(back_populates="topic_mappings")
