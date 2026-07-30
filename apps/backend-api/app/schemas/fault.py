"""Fault (ariza) sema'lari — frontend "Hat Arizalari" sayfasi icin."""

from datetime import datetime

from pydantic import BaseModel, ConfigDict


class FaultEventRead(BaseModel):
    id: int
    line_id: int
    line_name: str
    region_id: int
    region_name: str

    last_red_device_id: int
    last_red_device_code: str | None = None
    last_red_device_name: str | None = None
    first_green_device_id: int | None = None
    first_green_device_code: str | None = None
    first_green_device_name: str | None = None

    from_pole_id: int
    to_pole_id: int
    from_pole_seq: int | None = None
    to_pole_seq: int | None = None

    # Tel mesafesi (metre) — hat basindan olculur, kus ucusu degil hat boyunca.
    # NULL: topoloji/koordinat eksik ya da kayit henuz recompute'tan gecmedi.
    zone_start_m: float | None = None
    zone_end_m: float | None = None
    zone_length_m: float | None = None

    status: str
    opened_at: datetime
    resolved_at: datetime | None = None
    closed_at: datetime | None = None
    note: str | None = None

    assigned_to_username: str | None = None
    assigned_at: datetime | None = None
    assigned_to_full_name: str | None = None

    comment_count: int = 0

    model_config = ConfigDict(from_attributes=True)


class FaultEventNoteUpdate(BaseModel):
    note: str | None = None


class FaultEventAssignUpdate(BaseModel):
    assigned_to_username: str | None = None


class FaultEventStatusUpdate(BaseModel):
    status: str  # "in_progress" | "closed" | (manuel olarak)


class FaultCommentCreate(BaseModel):
    body: str


class FaultCommentRead(BaseModel):
    id: int
    fault_id: int
    author_username: str
    body: str
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)
