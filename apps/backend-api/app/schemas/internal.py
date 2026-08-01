from datetime import datetime

from pydantic import BaseModel, Field


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
    # Sinyal anahtari — kaynak (master/sat01/sat02) bilgisini frontend prefix'ten
    # turetir; alarm-service zaten payload'a koyar.
    signal_key: str | None = None
    # Tetikleyen olcum degeri (alarm kuralinin esigi gectigi anki deger).
    # Bildirim panelinde "Akim 142 A (esik 100 A)" gibi detayli gosterim icin.
    value: float | None = None
    # Sinyalin metinsel karsiligi (DNP3 Group 110 / Octet String). value None
    # gelirse degerin metni burada olur (orn. "trip status: opened").
    value_string: str | None = None
    # Alarm kuralinin esik degeri (varsa). Frontend "deger A esikten yuksek/duşuk"
    # mesajini olusturmak icin kullanir.
    threshold: float | None = None
    # Esige gore karsilastirma operatoru ("gt", "lt", "eq", "ne"). Frontend
    # ">", "<", "=", "!=" sembollerine cevirir.
    operator: str | None = None
    # Bu alarmi ureten kuralin produces_fault degeri. True (default): cihaz
    # haritada arizali gosterilir + Hat Arizasi acilir. False: yalniz Alarmlar
    # ekraninda gorunur. Eski alarm-service surumu bu alani gondermezse default
    # True devreye girer (geriye donuk uyum).
    produces_fault: bool = True


class InternalAlarmClear(BaseModel):
    """Alarm-service'in 'kosul artik karsilanmiyor' bildirimi.

    Backend'deki acik (reset=False) alarmlardan eslesenleri reset=True yapar
    ki UI'da 'Normale Donen - Onay Bekliyor' bolumune dussun.
    Eslesme: device + signal_key (varsa) + title.
    """

    device_id: int | None = None
    device_code: str | None = None
    rule_id: int | None = None
    title: str | None = None
    # Yanlis sinyalin alarmini kapatmamak icin clear cagrilarinda hangi sinyalin
    # normale dondugu da gonderilir. Eski cagrilar icin opsiyonel.
    signal_key: str | None = None
    source_timestamp: datetime | None = None
    source_gateway: str | None = None


class InternalCommandRequest(BaseModel):
    """Dis protokolun (IEC 104) kuyruga almak istedigi cihaz komutu.

    `command` serbest metin DEGIL: backend `PROTOCOL_ALLOWED_SLUGS` ile
    dogruluyor. Burada da tip/uzunluk siniri var ki gecersiz girdi servise
    hic ulasmasin.
    """

    device_code: str = Field(min_length=1, max_length=50)
    command: str = Field(min_length=1, max_length=80)
    #: Komutun geldigi protokol ("iec104"). Audit'te kapiyi ayirt eder.
    origin: str = Field(min_length=1, max_length=20)
    #: Komutu gonderen master'in adresi. Adli inceleme icin audit'e yazilir.
    peer: str = Field(min_length=1, max_length=80)


class InternalCommandQueued(BaseModel):
    id: int
    status: str
    command: str
    device_code: str


class InternalCommandStatus(BaseModel):
    id: int
    status: str
    result_status: str | None = None
    result_error: str | None = None
