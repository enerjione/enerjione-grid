"""Backup endpoint'leri icin Pydantic sema'lari."""

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class BackupJobRead(BaseModel):
    id: int
    job_type: str
    status: str
    file_path: str | None = None
    size_bytes: int | None = None
    error_message: str | None = None
    created_by_username: str | None = None
    created_at: datetime
    completed_at: datetime | None = None
    # frontend'de gosterilecek dosya adi
    filename: str | None = None

    model_config = ConfigDict(from_attributes=True)


class BackupScheduleRead(BaseModel):
    enabled: bool
    interval_hours: int
    retention_count: int
    last_run_at: datetime | None = None

    model_config = ConfigDict(from_attributes=True)


class BackupScheduleUpdate(BaseModel):
    enabled: bool | None = None
    interval_hours: int | None = Field(default=None, ge=1, le=720)
    retention_count: int | None = Field(default=None, ge=1, le=365)
