from pydantic import BaseModel, Field


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

    class Config:
        from_attributes = True
