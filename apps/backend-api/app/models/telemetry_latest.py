from datetime import datetime

from sqlalchemy import DateTime, Float, ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class TelemetryLatest(Base):
    """Her (cihaz, sinyal) icin SON deger — tek satir, uzerine yazilir.

    NEDEN VAR
    ---------
    `/signals/live` bu tablodan ONCE `telemetry` uzerinde `DISTINCT ON` ile
    calisiyordu. 600 cihaz olceginde bunun bedeli dogrusal degil:

      * `telemetry` 30 dakikalik pencerede ~2,16M satir tutar. `DISTINCT ON`
        PostgreSQL'de skip-scan DEGILDIR: pencerenin TAMAMI okunup 12.000
        satira indirgenir. Koddaki "<50 ms / index-only scan" iddiasi 200
        cihazda dogruydu, 600'de degil.
      * Anasayfa bu ucu `device_codes` VERMEDEN cagiriyor ve WS koptugunda
        periyot 30 sn'den 5 sn'ye dusuyor — yani baglanti bozuldugunda yuk
        6 KATLANIYOR.
      * Istek basina ~311 MB bellek tepesi olusuyor ve container tavani 2 GB;
        3-5 esizamanli anasayfa backend-api'yi OOM'a goturuyordu.

    Burada okuma O(gorunur cihaz x sinyal) ve PK uzerinden gider; pencere
    boyutundan BAGIMSIZDIR.

    ESKI DEGER YENIYI EZMEZ
    -----------------------
    Upsert `source_timestamp` karsilastirmasi ile yapilir. NATS en-az-bir-kez
    teslim garantisi verdigi icin ayni mesaj yeniden dagitilabilir ve
    sira DISINDA gelebilir; kosulsuz `DO UPDATE` bayat bir okumayi "son deger"
    yapardi. Bu tablo canli ekranin ve WS yayininin kaynagi oldugundan sonucu
    dogrudan operatore yansirdi.
    """

    __tablename__ = "telemetry_latest"

    # Bilesik birincil anahtar: cihaz basina sinyal basina TEK satir.
    device_id: Mapped[int] = mapped_column(
        ForeignKey("devices.id", ondelete="CASCADE"), primary_key=True
    )
    signal_key: Mapped[str] = mapped_column(String(120), primary_key=True)

    value: Mapped[float | None] = mapped_column(Float, nullable=True)
    value_string: Mapped[str | None] = mapped_column(Text, nullable=True)
    quality: Mapped[str] = mapped_column(String(50), default="good")
    source_timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True))

    # Cihaz saati teshisi — `telemetry` ile ayni ikili (bkz. migration 0026).
    timestamp_quality: Mapped[str | None] = mapped_column(String(20), nullable=True)
    device_event_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    # Satirin ne zaman yazildigi (backend saati). Tazelik gostergesi:
    # `source_timestamp` cihazdan/gateway'den gelir ve bozuk olabilir; bu
    # kolon "sistem bu degeri ne zaman gordu" sorusunu yanitlar.
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
