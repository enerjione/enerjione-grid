from typing import Literal

from pydantic import BaseModel, Field

IpEndpointType = Literal["initiating", "listening"]


class Dnp3ExtendedSettings(BaseModel):
    """Uç birimdeki (gateway/collector) DNP3 oturum parametreleri; merkez sadece saklar ve gösterir."""

    ip_endpoint_type: IpEndpointType = "listening"
    master_ip_address: str = ""
    master_ip_port: int = Field(default=20002, ge=1, le=65535)
    master_address: int = Field(default=100, ge=0, le=65535)
    unsolicited_reporting: bool = True
    unsolicited_on_startup: bool = True
    unsolicited_class_mask_id: int = Field(default=7, ge=0, le=255)
    link_status_period_min: int = Field(default=0, ge=0)
    enable_self_address: bool = False
    validate_source_address: bool = False
    session_timeout_listening_sec: int = Field(default=60, ge=1, le=86400)
    socket_listening_timeout_sec: int = Field(default=600, ge=1, le=86400)


def merge_dnp3_extended(stored: dict | None) -> Dnp3ExtendedSettings:
    base = Dnp3ExtendedSettings().model_dump()
    if not stored:
        return Dnp3ExtendedSettings.model_validate(base)
    if not isinstance(stored, dict):
        return Dnp3ExtendedSettings.model_validate(base)
    clean = {k: v for k, v in stored.items() if k not in ("tls_dnp3",)}
    base.update({k: v for k, v in clean.items() if k in base})
    base["ip_endpoint_type"] = "listening"
    return Dnp3ExtendedSettings.model_validate(base)
