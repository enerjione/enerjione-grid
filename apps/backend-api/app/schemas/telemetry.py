from datetime import datetime

from uuid import uuid4

from pydantic import BaseModel
from pydantic import Field


class TelemetryIn(BaseModel):
    message_id: str = Field(default_factory=lambda: str(uuid4()))
    correlation_id: str | None = None
    source_gateway: str | None = None
    device_code: str
    signal_key: str
    # DNP3 Group 110 (Octet String) sinyallerinde gateway numeric value yerine
    # value_string yollar; value None olur. Eski numeric sinyaller icin field
    # zorunluluk kaybolmaz cunku consumer None'i 0.0'a duser.
    value: float | None = None
    value_string: str | None = None
    # Numeric vs string ayrimini consumer'in gateway payload'unu yeniden
    # cozumlemeden yapabilmesi icin kategori bilgisi.
    signal_data_type: str | None = None
    quality: str = "good"
    # Ham DNP3 kalite bayraklari (Group 1/30/... octet). OPSIYONEL.
    #
    # BU ALANIN VARLIGI BIR SURUM ISARETIDIR — tasarimin can alici noktasi:
    #   dnp3_flags is not None  -> kalite NOKTA seviyesinde (gateway 0.5.0+)
    #   dnp3_flags is None      -> kalite CIHAZ seviyesinde (0.4.x / legacy)
    #
    # NEDEN GEREKLI: `invalid` token'i BUGUN de uretiliyor ama CIHAZ
    # seviyesinde ("tum cihaz okunamadi" — legacy dnp3_master). Yeni gateway
    # ayni kelimeyi NOKTA seviyesinde uretiyor ("bu tek olcum gecersiz").
    # Ayni kelime, iki farkli kapsam. Backend'in `invalid -> cihaz OFFLINE`
    # esmesi eski anlam icin DOGRU, yeni anlam icin FELAKET olurdu: tek bir
    # noktanin referans hatasi TUM cihazi offline gosterir, harita kirmizi
    # olur ve "son veri" sayaci donardi.
    #
    # Ayri bir `quality_scope` alani EKLENMEDI: gateway bayragi okumadan yeni
    # kaliteyi zaten uretemiyor, dolayisiyla "alani gondermeyi unutma" riski
    # yok. Tek alan hem teshis (ham bayrak) hem surum ayrimi sagliyor.
    dnp3_flags: int | None = None
    source_timestamp: datetime


class GatewayTelemetryBatch(BaseModel):
    gateway_code: str
    sequence_no: int
    sent_at: datetime
    readings: list[TelemetryIn]


class TelemetryRead(BaseModel):
    id: int
    device_id: int
    signal_key: str
    value: float | None = None
    value_string: str | None = None
    quality: str
    source_timestamp: datetime

    class Config:
        from_attributes = True


class TelemetryHistoryPoint(BaseModel):
    """Historian ham okuma noktasi (bucket=raw)."""

    signal_key: str
    value: float | None = None
    value_string: str | None = None
    quality: str
    source_timestamp: datetime


class TelemetryAggregatePoint(BaseModel):
    """Continuous aggregate ozet noktasi (bucket=1m|1h)."""

    signal_key: str
    bucket: datetime
    avg_value: float | None = None
    min_value: float | None = None
    max_value: float | None = None
    sample_count: int
