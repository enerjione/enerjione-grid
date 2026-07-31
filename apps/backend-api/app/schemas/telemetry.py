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
    # GATEWAY saati — cihazi okumaya basladigi an. Cihaz basina TEK deger.
    #
    # ANLAMI DEGISMEDI ve DEGISMEYECEK. Gateway ekibi "artik cihaz zamani
    # olsun" onerdi; REDDEDILDI cunku bu alan ayni anda:
    #   * telemetry_history BIRINCIL ANAHTARININ parcasi
    #   * TimescaleDB hypertable partition kolonu
    #   * retention/disk-guard silme kriteri
    #   * continuous aggregate ekseni
    # Anlamini degistirmek bunlarin hepsini birden etkiler. Somut felaketler:
    #   - gecmise damgali satir historian INSERT'ini patlatir -> TUM telemetri
    #     akisi durur
    #   - ileriye damgali satir `telemetry` tablosunda OLUMSUZ olur; retention
    #     ve disk guard onu hic goremez
    #   - ayni saniyeye dusen "ariza gecti / ariza kalkti" cifti AYNI PK'ya
    #     duser ve ikincisi SESSIZCE kaybolur — yani B2'nin korumak istedigi
    #     veri tam da bu yuzden yok olur
    #   - RTC pili biten cihaz 2000-01-01 damgalar; olcum kabul edilir ve bir
    #     gun icinde retention tarafindan sessizce SILINIR
    source_timestamp: datetime
    # CIHAZIN kendi DNP3 olay zamani (varsa). OPSIYONEL, YENI alan.
    #
    # Neden ayri alan: yukaridaki hicbir mekanizmaya dokunmuyor. Kotu bir
    # cihaz saati burada yalnizca ANALIZ verisini bozar; depolamayi,
    # retention'i, partition'i ve dedup'i ETKILEMEZ. Deploy sirasi bozulsa
    # (yeni gateway once cikarsa) eski backend alani sessizce yok sayar —
    # yani "once backend" kuralina bagimli DEGILIZ.
    #
    # Kullanim: olay sirasi (SOE) ve ariza suresi analizi. 4G kopmasi sonrasi
    # outstation event buffer'i bosalinca tum olaylar ayni saniyede gelir ama
    # her biri KENDI olay zamanini tasir.
    device_event_at: datetime | None = None
    # Cihaz saatinin guvenilirligi: "synchronized" | "unsynchronized" |
    # "invalid". DNP3 zaman senkron bitlerinden gelir. None = gateway
    # bildirmedi (0.4.x).
    timestamp_quality: str | None = None


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
