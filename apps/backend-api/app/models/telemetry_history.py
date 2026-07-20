from datetime import datetime

from sqlalchemy import DateTime, Float, ForeignKey, Index, String, Text
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

    # Cihaz+sinyal+aralik sorgusu icin (DeviceDetailPage grafik, aggregate
    # kaynagi). Hypertable chunk'lari uzerinde bu index her chunk'a uygulanir.
    __table_args__ = (
        Index(
            "ix_telemetry_history_device_signal_ts",
            "device_id",
            "signal_key",
            "source_timestamp",
        ),
    )
