from typing import Literal

from pydantic import BaseModel, Field


AlarmLevel = Literal["info", "warning", "critical"]
AlarmComparator = Literal[
    "gt", "gte", "lt", "lte", "eq", "ne", "between", "outside", "boolean_true", "boolean_false"
]


class AlarmRuleBase(BaseModel):
    signal_key: str = Field(min_length=1, max_length=80)
    name: str = Field(min_length=1, max_length=160)
    description: str | None = None
    level: AlarmLevel = "warning"
    comparator: AlarmComparator = "gt"
    threshold: float = 0.0
    threshold_high: float | None = None
    hysteresis: float = 0.0
    debounce_sec: int = 0
    device_code_filter: str | None = None
    is_active: bool = True
    # Kural-bazli bildirim kanallari (web bildirimi her zaman gider).
    # Default false: kullanici acmadan email/sms/telegram gitmez.
    notify_email: bool = False
    notify_sms: bool = False
    notify_telegram: bool = False


class AlarmRuleCreate(AlarmRuleBase):
    pass


class AlarmRuleUpdate(BaseModel):
    name: str | None = None
    description: str | None = None
    level: AlarmLevel | None = None
    comparator: AlarmComparator | None = None
    threshold: float | None = None
    threshold_high: float | None = None
    hysteresis: float | None = None
    debounce_sec: int | None = None
    device_code_filter: str | None = None
    is_active: bool | None = None
    notify_email: bool | None = None
    notify_sms: bool | None = None
    notify_telegram: bool | None = None


class AlarmRuleRead(AlarmRuleBase):
    id: int

    class Config:
        from_attributes = True
