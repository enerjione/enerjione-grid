from sqlalchemy import Boolean, ForeignKey, Integer, String, Text
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
    # rest | mqtt | iec104  (ileride: modbus | opcua)
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

    # Custom topic mappings — operator UI'da "X cihaz + Y sinyal -> Z topic"
    # ekleyebilir. Default template'i bypass eder.
    topic_mappings: Mapped[list["OutboundTopicMapping"]] = relationship(
        back_populates="target", cascade="all, delete-orphan"
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
