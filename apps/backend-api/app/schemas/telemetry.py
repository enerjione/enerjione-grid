from datetime import datetime

from uuid import uuid4

from pydantic import BaseModel
from pydantic import Field


class TelemetryIn(BaseModel):
    message_id: str = Field(default_factory=lambda: str(uuid4()))
    correlation_id: str | None = None
    source_gateway: str | None = None
    device_code: str
    signal_key: str
    # DNP3 Group 110 (Octet String) sinyallerinde gateway numeric value yerine
    # value_string yollar; value None olur. Eski numeric sinyaller icin field
    # zorunluluk kaybolmaz cunku consumer None'i 0.0'a duser.
    value: float | None = None
    value_string: str | None = None
    # Numeric vs string ayrimini consumer'in gateway payload'unu yeniden
    # cozumlemeden yapabilmesi icin kategori bilgisi.
    signal_data_type: str | None = None
    quality: str = "good"
    source_timestamp: datetime


class GatewayTelemetryBatch(BaseModel):
    gateway_code: str
    sequence_no: int
    sent_at: datetime
    readings: list[TelemetryIn]


class TelemetryRead(BaseModel):
    id: int
    device_id: int
    signal_key: str
    value: float | None = None
    value_string: str | None = None
    quality: str
    source_timestamp: datetime

    class Config:
        from_attributes = True
