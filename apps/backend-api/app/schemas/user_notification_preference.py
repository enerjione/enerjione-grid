"""Kullanici bildirim tercihleri sema'lari."""

from pydantic import BaseModel, ConfigDict


class UserNotificationPreferenceRead(BaseModel):
    user_id: int
    web_enabled: bool
    email_enabled: bool
    sms_enabled: bool
    telegram_enabled: bool = False
    min_level_rank: int

    model_config = ConfigDict(from_attributes=True)


class UserNotificationPreferenceUpdate(BaseModel):
    web_enabled: bool | None = None
    email_enabled: bool | None = None
    sms_enabled: bool | None = None
    telegram_enabled: bool | None = None
    min_level_rank: int | None = None
