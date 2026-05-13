from pydantic import BaseModel, Field


class OutboundTopicMappingBase(BaseModel):
    topic: str = Field(min_length=1, max_length=500)
    device_codes: str = ""
    signal_keys: str = ""
    qos: int | None = Field(default=None, ge=0, le=2)
    retain: bool | None = None
    is_active: bool = True


class OutboundTopicMappingCreate(OutboundTopicMappingBase):
    pass


class OutboundTopicMappingUpdate(BaseModel):
    topic: str | None = Field(default=None, min_length=1, max_length=500)
    device_codes: str | None = None
    signal_keys: str | None = None
    qos: int | None = Field(default=None, ge=0, le=2)
    retain: bool | None = None
    is_active: bool | None = None


class OutboundTopicMappingRead(OutboundTopicMappingBase):
    id: int
    target_id: int

    class Config:
        from_attributes = True


class OutboundTargetCreate(BaseModel):
    name: str
    # rest | mqtt | iec104 (ileride modbus/opcua)
    protocol: str
    endpoint: str = ""
    topic: str | None = None
    event_filter: str = "all"
    auth_header: str | None = None
    auth_token: str | None = None
    qos: int = 0
    retain: bool = False
    is_active: bool = True
    # IEC 104 server parametreleri (rest/mqtt icin bos birakilir):
    listen_host: str | None = None
    listen_port: int | None = Field(default=None, ge=1, le=65535)
    iec104_common_address: int | None = Field(default=None, ge=0, le=65535)
    iec104_ioa_device_stride: int | None = Field(default=None, ge=1, le=1_000_000)
    iec104_allowed_peers: str | None = None
    # MQTT-specific (protocol='mqtt' icin):
    mqtt_port: int | None = Field(default=None, ge=1, le=65535)
    mqtt_username: str | None = None
    mqtt_password: str | None = None
    mqtt_client_id: str | None = None
    mqtt_tls_enabled: bool = False
    mqtt_tls_insecure: bool = False
    mqtt_tls_ca_path: str | None = None
    mqtt_tls_cert_path: str | None = None
    mqtt_tls_key_path: str | None = None
    mqtt_keepalive_sec: int = Field(default=60, ge=5, le=3600)
    mqtt_connect_timeout_sec: int = Field(default=10, ge=1, le=120)
    mqtt_publish_interval_sec: int = Field(default=10, ge=0, le=3600)
    mqtt_topic_template: str | None = None
    mqtt_topic_prefix: str = "e1"
    mqtt_customer_id: str | None = None


class OutboundTargetUpdate(BaseModel):
    endpoint: str | None = None
    topic: str | None = None
    event_filter: str | None = None
    auth_header: str | None = None
    auth_token: str | None = None
    qos: int | None = None
    retain: bool | None = None
    is_active: bool | None = None
    listen_host: str | None = None
    listen_port: int | None = Field(default=None, ge=1, le=65535)
    iec104_common_address: int | None = Field(default=None, ge=0, le=65535)
    iec104_ioa_device_stride: int | None = Field(default=None, ge=1, le=1_000_000)
    iec104_allowed_peers: str | None = None
    mqtt_port: int | None = Field(default=None, ge=1, le=65535)
    mqtt_username: str | None = None
    mqtt_password: str | None = None
    mqtt_client_id: str | None = None
    mqtt_tls_enabled: bool | None = None
    mqtt_tls_insecure: bool | None = None
    mqtt_tls_ca_path: str | None = None
    mqtt_tls_cert_path: str | None = None
    mqtt_tls_key_path: str | None = None
    mqtt_keepalive_sec: int | None = Field(default=None, ge=5, le=3600)
    mqtt_connect_timeout_sec: int | None = Field(default=None, ge=1, le=120)
    mqtt_publish_interval_sec: int | None = Field(default=None, ge=0, le=3600)
    mqtt_topic_template: str | None = None
    mqtt_topic_prefix: str | None = None
    mqtt_customer_id: str | None = None


class OutboundTargetRead(BaseModel):
    id: int
    name: str
    protocol: str
    endpoint: str
    topic: str | None = None
    event_filter: str
    auth_header: str | None = None
    auth_token: str | None = None
    qos: int
    retain: bool
    is_active: bool
    listen_host: str | None = None
    listen_port: int | None = None
    iec104_common_address: int | None = None
    iec104_ioa_device_stride: int | None = None
    iec104_allowed_peers: str | None = None
    mqtt_port: int | None = None
    mqtt_username: str | None = None
    mqtt_password: str | None = None
    mqtt_client_id: str | None = None
    mqtt_tls_enabled: bool = False
    mqtt_tls_insecure: bool = False
    mqtt_tls_ca_path: str | None = None
    mqtt_tls_cert_path: str | None = None
    mqtt_tls_key_path: str | None = None
    mqtt_keepalive_sec: int = 60
    mqtt_connect_timeout_sec: int = 10
    mqtt_publish_interval_sec: int = 10
    mqtt_topic_template: str | None = None
    mqtt_topic_prefix: str = "e1"
    mqtt_customer_id: str | None = None
    # Mapping listesi UI'da target detayi cekildiginde gelir.
    topic_mappings: list[OutboundTopicMappingRead] = []

    class Config:
        from_attributes = True
