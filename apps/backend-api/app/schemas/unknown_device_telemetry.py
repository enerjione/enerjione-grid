from datetime import datetime

from pydantic import BaseModel, Field


class UnknownTelemetryRead(BaseModel):
    """Karantinadaki tek kayit. `payload_json` BILEREK yok — liste ucu ham
    payload'lari tasimamali (yanit boyutu ve gereksiz veri yayilimi)."""

    id: int
    device_code: str
    gateway_code: str | None = None
    signal_key: str | None = None
    message_id: str
    subject: str | None = None
    reason: str
    status: str
    seen_count: int
    first_seen_at: datetime
    last_seen_at: datetime
    source_timestamp: datetime | None = None
    replayed_at: datetime | None = None
    replay_attempts: int
    last_replay_error: str | None = None

    class Config:
        from_attributes = True


class UnknownTelemetryDeviceSummary(BaseModel):
    device_code: str
    gateway_code: str | None = None
    pending: int
    oldest_pending_at: datetime | None = None
    device_exists: bool


class UnknownTelemetrySummary(BaseModel):
    """Operator ekrani icin ozet: hangi kod kac olcum biriktirdi ve o kod
    artik tanimli mi (yani replay edilebilir mi)."""

    pending_total: int
    replayed_total: int
    rows_total: int
    max_rows: int
    capacity_full: bool
    oldest_pending_age_sec: float | None = None
    devices: list[UnknownTelemetryDeviceSummary]


class ReplayRequest(BaseModel):
    """Replay filtreleri.

    `device_code` verilmezse bekleyen TUM kayitlar denenir; cihazi hala
    tanimsiz olanlar dokunulmadan pending kalir.
    """

    device_code: str | None = None
    gateway_code: str | None = None
    limit: int = Field(default=500, ge=1, le=5000)


class ReplayResponse(BaseModel):
    requested: int
    replayed: int
    skipped_already_processed: int
    still_pending: int
    errors: dict[str, int]
