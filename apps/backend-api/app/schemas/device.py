from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.models.enums import CommunicationStatus
from app.schemas.dnp3_extended import Dnp3ExtendedSettings, merge_dnp3_extended


class DeviceScalarBase(BaseModel):
    code: str
    name: str
    description: str | None = None
    model: str = "horstmann_sn_2_0"
    gateway_code: str | None = None
    ip_address: str
    dnp3_outstation_port: int = Field(default=20001, ge=1, le=65535)
    dnp3_address: int = 1
    poll_interval_sec: int = 5
    timeout_ms: int = 3000
    retry_count: int = 2
    signal_profile: str = "horstmann_sn2_fixed"
    latitude: float
    longitude: float
    # IEC 60870-5-104 ASDU Common Address. NULL ise outbound target'in
    # default CA'si kullanilir. 0..65534 araliginda; 65535 broadcast icin
    # rezerve edildiginden cihaza atanmamasi tavsiye edilir.
    iec104_common_address: int | None = Field(default=None, ge=0, le=65534)


class DeviceCreate(DeviceScalarBase):
    dnp3_extended: Dnp3ExtendedSettings | None = None


class DeviceUpdate(BaseModel):
    name: str | None = None
    description: str | None = None
    model: str | None = None
    gateway_code: str | None = None
    ip_address: str | None = None
    dnp3_outstation_port: int | None = Field(default=None, ge=1, le=65535)
    dnp3_address: int | None = None
    poll_interval_sec: int | None = None
    timeout_ms: int | None = None
    retry_count: int | None = None
    latitude: float | None = None
    longitude: float | None = None
    dnp3_extended: Dnp3ExtendedSettings | None = None
    iec104_common_address: int | None = Field(default=None, ge=0, le=65534)


class DeviceRead(DeviceScalarBase):
    id: int
    battery_percent: float
    communication_status: CommunicationStatus
    alarm_active: bool
    last_update_at: datetime | None
    dnp3_extended: Dnp3ExtendedSettings

    @field_validator("dnp3_extended", mode="before")
    @classmethod
    def _merge_dnp3_extended(cls, v: object) -> Dnp3ExtendedSettings:
        if v is None or v == {}:
            return merge_dnp3_extended(None)
        if isinstance(v, Dnp3ExtendedSettings):
            return v
        if isinstance(v, dict):
            return merge_dnp3_extended(v)
        return merge_dnp3_extended(None)

    model_config = ConfigDict(from_attributes=True)
