from typing import Literal

from pydantic import BaseModel, Field


SignalDataType = Literal[
    "analog",
    "binary",
    "counter",
    "string",
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
    iec104_ioa_offset: int | None = Field(default=None, ge=0, le=16_777_215)


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
    iec104_ioa_offset: int | None = Field(default=None, ge=0, le=16_777_215)


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
    device_id: int
    device_code: str
    device_name: str
    value: float | None = None
    quality: str | None = None
    source_timestamp: str | None = None
