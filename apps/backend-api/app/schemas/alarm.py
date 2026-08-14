from datetime import datetime

from pydantic import BaseModel


class AlarmEventRead(BaseModel):
    id: int
    #: Cihaz SILINMISSE NULL — satir gecmis olarak durur (bkz. models/alarm.py).
    #: Zorunlu `int` kaldigi surece boyle bir satiri serilestirmek pydantic
    #: dogrulama hatasi verir ve alarm ucu 500 doner; alan bu yuzden optional.
    #:
    #: CANLI UCLAR bu satiri normalde HIC dondurmez: cihaz silinirken kayit
    #: `superseded_at` ile arsivlenir, `list_alarm_events` de arsivlileri
    #: suzer. Yani NULL yalnizca arsiv kaydina dogrudan erisildiginde gorunur.
    device_id: int | None = None
    #: Silme ANINDAKI cihaz kodu/adi. `device_id` NULL oldugunda kaydin
    #: hangi cihaza ait oldugu SADECE buradan okunabilir.
    device_code: str | None = None
    device_name: str | None = None
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
    # Bu alarm gercek hat arizasi uretir mi? Frontend haritada cihazi yalniz
    # produces_fault=True ise kirmizi gosterir. Default True -> geriye uyum.
    produces_fault: bool = True
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
