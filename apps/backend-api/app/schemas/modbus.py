"""Modbus adres plani sema'lari.

Ayni yapi iki yerde kullanilir:
  * Web arayuzu  -> `/outbound-targets/{id}/modbus-plan` (adres tablosu + CSV)
  * modbus-outbound worker -> `/internal/modbus-plans` (yayinlayacagi noktalar)
Tek kaynak, tek format: SCADA'ya verilen liste ile sunucunun yayinladigi adres
her zaman ayni.
"""

from __future__ import annotations

from pydantic import BaseModel, Field


class ModbusLayoutSummary(BaseModel):
    """Cihaz basina yer ihtiyaci."""

    register_words: int
    discrete_bits: int
    coil_bits: int
    analog_count: int
    counter_count: int
    binary_count: int
    binary_output_count: int
    excluded_string_count: int
    # int16 tavanina sigmadigi icin olcegi genisletilen sinyal sayisi
    # (bkz. modbus_plan_service.resolve_int16_scale).
    rescaled_count: int = 0


class ModbusCapacity(BaseModel):
    mode: str
    stride: int
    max_devices: int
    device_count: int
    remaining: int
    # Cihazin tum register'lari tek Modbus okumasina (125 register) siginiyor mu?
    single_read_per_device: bool
    # address_space | bit_space | unit_id_range
    limit_reason: str
    # Kapasiteye SIGMAYAN cihazlar. Servis bunlari hesapliyordu ama SEMADA
    # ALAN YOKTU: pydantic fazla anahtarlari sessizce dusurdugu icin uyari
    # ne arayuze ne worker'a ulasiyordu — `remaining: 0` tek basina "tam"
    # gibi okunuyor, "N cihaz SCADA'ya hic yayinlanmiyor" bilgisi hicbir
    # yerde gorunmuyordu (bkz. tests/test_modbus_capacity_overflow.py).
    dropped_device_count: int = 0
    dropped_device_codes: list[str] = Field(default_factory=list)


class ModbusDeviceSlotRead(BaseModel):
    device_id: int
    device_code: str
    device_name: str
    slot_index: int
    unit_id: int
    block_start: int


class ModbusPlanPoint(BaseModel):
    device_code: str
    device_name: str
    signal_key: str
    label: str
    source: str
    data_type: str
    unit: str | None = None
    unit_id: int
    # 1=coil, 2=discrete input, 3=holding register (4=input register aynasi)
    function: int
    address: int
    word_count: int
    scale: float
    offset: float
    # Katalogdaki manuel adres override'i ile mi geldi?
    manual: bool = False
    # Olcek int16 tavani icin katalogdakinden genisletildi mi?
    rescaled: bool = False


class ModbusPlanRead(BaseModel):
    target_id: int
    target_name: str
    mode: str
    value_format: str
    word_order: str
    unit_id: int
    base_address: int
    stride: int
    listen_host: str
    listen_port: int
    is_active: bool
    allowed_peers: list[str] = Field(default_factory=list)
    summary: ModbusLayoutSummary
    capacity: ModbusCapacity
    devices: list[ModbusDeviceSlotRead] = Field(default_factory=list)
    points: list[ModbusPlanPoint] = Field(default_factory=list)
