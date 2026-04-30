from datetime import datetime

from pydantic import BaseModel


class AlarmEventRead(BaseModel):
    id: int
    device_id: int
    level: str
    title: str
    description: str
    # Sinyal anahtari — kaynagi (master/sat01/sat02) frontend prefix'ten turetir.
    signal_key: str | None = None
    assigned_to: str | None = None
    acknowledged: bool = False
    reset: bool = False
    acknowledged_at: datetime | None = None
    reset_at: datetime | None = None
    created_at: datetime

    class Config:
        from_attributes = True


class AlarmAssignRequest(BaseModel):
    assigned_to: str | None = None


class AlarmCommentCreate(BaseModel):
    comment: str


class AlarmCommentRead(BaseModel):
    id: int
    alarm_event_id: int
    author_username: str
    comment: str
    created_at: datetime

    class Config:
        from_attributes = True
