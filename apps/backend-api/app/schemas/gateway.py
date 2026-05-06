from datetime import datetime

from pydantic import BaseModel


class GatewayCreate(BaseModel):
    code: str
    name: str
    host: str
    listen_port: int
    upstream_url: str
    batch_interval_sec: int = 5
    max_devices: int = 200
    device_code_prefix: str | None = None
    token: str
    is_active: bool = True
    control_host: str = "127.0.0.1"
    control_port: int = 0


class GatewayUpdate(BaseModel):
    name: str | None = None
    host: str | None = None
    listen_port: int | None = None
    upstream_url: str | None = None
    batch_interval_sec: int | None = None
    max_devices: int | None = None
    device_code_prefix: str | None = None
    token: str | None = None
    is_active: bool | None = None
    control_host: str | None = None
    control_port: int | None = None


class GatewayRead(BaseModel):
    id: int
    code: str
    name: str
    host: str
    listen_port: int
    upstream_url: str
    batch_interval_sec: int
    max_devices: int
    device_code_prefix: str | None = None
    token: str
    is_active: bool
    last_seen_at: datetime | None = None
    control_host: str = "127.0.0.1"
    control_port: int = 0
    # Initiating mode TCP server portu icin host tarafi baslangici. Default
    # 20100; ek gateway eklenince otomatik 21100, 22100, ... olarak atanir.
    # Frontend cihaz formunda "Master IP Port" yardim metni icin kullanir.
    initiating_port_base: int = 20100

    class Config:
        from_attributes = True


class GatewayConfigDevice(BaseModel):
    code: str
    name: str
    ip_address: str
    dnp3_address: int
    dnp3_tcp_port: int
    master_address: int | None = None
    # Initiating (Direct/Initiating End Point): cihaz master'a outbound baglanir;
    # gateway TCP server modunda dinler. Listening: cihaz dinler, gateway client
    # olarak baglanir (default).
    ip_endpoint_type: str = "listening"
    # Initiating mode'da gateway'in dinleyecegi port. Backend cihaz basina
    # otomatik atar (20100..20700). Listening mode'da kullanilmaz.
    master_ip_port: int | None = None
    poll_interval_sec: int
    timeout_ms: int
    retry_count: int
    signal_profile: str


class GatewayConfigSignal(BaseModel):
    """Standart sinyal listesi - tum cihazlar icin ortak DNP3 adresleri.

    `source` alani Horstmann SN2 icin zorunlu: alarmin hangi kaynagi
    (master / sat01 / sat02) uzerinden geldigi collector'da ayirt edilir.
    """

    key: str
    label: str
    unit: str | None = None
    source: str = "master"
    dnp3_class: str = "Class 1"
    data_type: str
    dnp3_object_group: int
    dnp3_index: int
    scale: float
    offset: float
    supports_alarm: bool


class GatewayConfigResponse(BaseModel):
    gateway_code: str
    gateway_name: str
    batch_interval_sec: int
    max_devices: int
    is_active: bool
    devices: list[GatewayConfigDevice]
    signals: list[GatewayConfigSignal]
    config_version: str
