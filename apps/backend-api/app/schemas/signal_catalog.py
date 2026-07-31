from typing import Literal

from pydantic import BaseModel, Field


SignalDataType = Literal[
    "analog",
    "binary",
    "counter",
    "string",
    # binary_output (G10) = DNP3 CROB komut kanali; yayinlanmaz, cihaz komutu
    # (Trigger Download, Reset...) icin dnp3_index adresi tutar.
    "binary_output",
    "analog_output",
]

SignalSource = Literal["master", "sat01", "sat02"]


class SignalCatalogBase(BaseModel):
    key: str = Field(min_length=1, max_length=120)
    model: str = Field(default="horstmann_sn_2_0", min_length=1, max_length=80)
    label: str = Field(min_length=1, max_length=200)
    unit: str | None = None
    description: str | None = None
    source: SignalSource = "master"
    dnp3_class: str = "Class 1"
    data_type: SignalDataType = "analog"
    dnp3_object_group: int = 30
    dnp3_index: int = 0
    scale: float = 1.0
    offset: float = 0.0
    supports_alarm: bool = False
    is_active: bool = True
    display_order: int = 0
    # IEC 60870-5-104 adresleme — `string` ve desteklenmeyen tiplerde NULL.
    iec104_type_id: int | None = Field(default=None, ge=0, le=255)
    # Yeni model: sinyalin mutlak IOA'si. Cihaz bazli ayrim ASDU CA ile yapilir.
    iec104_ioa: int | None = Field(default=None, ge=0, le=16_777_215)
    # Geri uyumluluk: eski deploylarda offset'ti; yeni alan dolu degilse mutlak
    # IOA olarak yorumlanir. Yeni kayitlar `iec104_ioa` doldurmali.
    iec104_ioa_offset: int | None = Field(default=None, ge=0, le=16_777_215)
    # IEC 104 yayini sinyal bazinda gecici kapatma. Default True (yayinla).
    iec104_enabled: bool = True
    # CP56Time2a zaman etiketli ASDU tipinde mi yayinlansin? Default False
    # (analog sinyaller icin gereksiz yuk). Dijital sinyallere migration ile
    # otomatik True yapilir; kullanici sinyal duzenleme formunda secebilir.
    iec104_with_timestamp: bool = False
    # Modbus outbound (Holding=3, Input=4, Coil=1, Discrete=2)
    modbus_function: int | None = Field(default=None, ge=1, le=255)
    modbus_address: int | None = Field(default=None, ge=0, le=65_535)
    # MQTT outbound topic suffix'i (cihaz koduyla birleştirilir).
    mqtt_topic: str | None = Field(default=None, max_length=200)


class SignalCatalogCreate(SignalCatalogBase):
    pass


class SignalCatalogUpdate(BaseModel):
    model: str | None = Field(default=None, min_length=1, max_length=80)
    label: str | None = None
    unit: str | None = None
    description: str | None = None
    source: SignalSource | None = None
    dnp3_class: str | None = None
    data_type: SignalDataType | None = None
    dnp3_object_group: int | None = None
    dnp3_index: int | None = None
    scale: float | None = None
    offset: float | None = None
    supports_alarm: bool | None = None
    is_active: bool | None = None
    display_order: int | None = None
    iec104_type_id: int | None = Field(default=None, ge=0, le=255)
    iec104_ioa: int | None = Field(default=None, ge=0, le=16_777_215)
    iec104_ioa_offset: int | None = Field(default=None, ge=0, le=16_777_215)
    iec104_enabled: bool | None = None
    iec104_with_timestamp: bool | None = None
    modbus_function: int | None = Field(default=None, ge=1, le=255)
    modbus_address: int | None = Field(default=None, ge=0, le=65_535)
    mqtt_topic: str | None = Field(default=None, max_length=200)


class SignalCatalogRead(SignalCatalogBase):
    id: int

    class Config:
        from_attributes = True


class SignalLiveValue(BaseModel):
    """Canli degerler ekranina donulen tek satir."""

    signal_key: str
    signal_label: str
    unit: str | None = None
    source: str = "master"
    data_type: str | None = None
    device_id: int
    device_code: str
    device_name: str
    value: float | None = None
    # DNP3 Group 110 (Octet String) sinyallerinde numeric value None olur ve
    # gercek metin bu alanda doner. Frontend data_type='string' satirlarda
    # value_string'i gosterir.
    value_string: str | None = None
    quality: str | None = None
    source_timestamp: str | None = None
    # --- CIHAZ SAATI DURUMU ----------------------------------------------
    # `quality`den AYRI alanlar. Saat kaymasi olcumu gecersiz kilmaz; ikisini
    # tek alanda birlestirmek saati bozuk bir cihazi "kalitesiz veri" gibi
    # gosterip alarmlarini bastirirdi.
    #
    # timestamp_quality: "synchronized" | "unsynchronized" | "invalid" | None.
    # None = bilgi yok (0.4.x gateway alani hic gondermiyor) — UI bu durumda
    # HICBIR uyari gostermez, cunku "bilmiyoruz" ile "saat bozuk" ayni sey
    # degildir.
    timestamp_quality: str | None = None
    # Cihazin kendi bildirdigi olay zamani. `source_timestamp` (gateway saati)
    # ile yan yana gosterilince operator kaymanin YONUNU ve BUYUKLUGUNU
    # gorebilir; ham deger saklandigi icin "2000-01-01" gibi bir damga RTC
    # pilinin bittigini dogrudan soyler.
    device_event_at: str | None = None
