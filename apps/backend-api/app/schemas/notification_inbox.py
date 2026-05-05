"""Bildirim merkezi (zil ikonu) icin schema'lar.

`schemas/notification.py` SMTP/SMS notification SETTINGS icin (farkli amac);
karistirmamak adina inbox icin ayri dosyada tutuyoruz.
"""
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class NotificationRead(BaseModel):
    id: int
    recipient_username: str | None = None
    category: str
    severity: str
    title: str
    body: str | None = None
    actor_username: str | None = None
    link: str | None = None
    metadata_json: str | None = None
    is_read: bool
    read_at: datetime | None = None
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


class NotificationUnreadCount(BaseModel):
    unread: int = Field(ge=0)


class NotificationMarkResult(BaseModel):
    ok: bool
    affected: int = 0
