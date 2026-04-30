from datetime import datetime

from pydantic import BaseModel


class InternalAlarmIngest(BaseModel):
    device_id: int | None = None
    device_code: str | None = None
    level: str = "critical"
    title: str
    description: str
    source_timestamp: datetime | None = None
    message_id: str | None = None
    correlation_id: str | None = None
    source_gateway: str | None = None


class InternalAlarmClear(BaseModel):
    """Alarm-service'in 'kosul artik karsilanmiyor' bildirimi.

    Backend'deki acik (reset=False) alarmlardan eslesenleri reset=True yapar
    ki UI'da 'Normale Donen - Onay Bekliyor' bolumune dussun.
    Eslesme: device + (rule_id varsa rule_id, yoksa title).
    """

    device_id: int | None = None
    device_code: str | None = None
    rule_id: int | None = None
    title: str | None = None
    source_timestamp: datetime | None = None
    source_gateway: str | None = None
