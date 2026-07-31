from datetime import datetime

from sqlalchemy import DateTime, Float, ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class TelemetryHistory(Base):
    """Uzun sureli telemetri arsivi (TimescaleDB hypertable).

    `telemetry` tablosu canli-deger / kisa retention penceresi olarak kalir
    (bkz. telemetry_retention.py). Bu tablo AI analizi + grafik + geriye donuk
    sorgu icin okunan HER degeri saklar; migration 0007 bunu hypertable'a
    cevirir (source_timestamp partition), 90 gun retention + 1dk/1saat
    continuous aggregate uygular.

    Integer autoincrement id YOK: hypertable'da partition kolonu (source_
    timestamp) her unique/PK constraint'in parcasi olmak zorundadir. Dogal
    anahtar (device_id, signal_key, source_timestamp) hem PK hem idempotency:
    ayni okuma iki kez gelirse (consumer redeliver) IntegrityError -> rollback,
    duplicate yok.
    """

    __tablename__ = "telemetry_history"

    device_id: Mapped[int] = mapped_column(
        ForeignKey("devices.id"), primary_key=True
    )
    signal_key: Mapped[str] = mapped_column(String(120), primary_key=True)
    source_timestamp: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), primary_key=True
    )
    # value/value_string ayrimi telemetry ile ayni: Octet String sinyallerde
    # value NULL, metin value_string'te; numeric tiplerde value dolu.
    value: Mapped[float | None] = mapped_column(Float, nullable=True)
    value_string: Mapped[str | None] = mapped_column(Text, nullable=True)
    quality: Mapped[str] = mapped_column(String(50), default="good")

    # NOT: Burada bir zamanlar `ix_telemetry_history_device_signal_ts` adinda
    # (device_id, signal_key, source_timestamp) index'i vardi. BIREBIR AYNI
    # kolonlari ayni sirada tasiyan PRIMARY KEY zaten mevcut oldugu icin bu
    # index tamamen karsiliksizdi: hicbir sorguya hizmet etmiyor, ama her
    # INSERT'te (gunde ~26M) ikinci kez yazilip diskte ~%28 fazladan yer
    # kapliyordu. Migration 0022 mevcut kurulumlarda dusuruyor.
    #
    # Cihaz+sinyal+aralik sorgulari (DeviceDetailPage grafigi, aggregate
    # kaynagi) PK uzerinden ayni performansla karsilanir.
