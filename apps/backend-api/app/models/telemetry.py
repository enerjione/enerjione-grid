from datetime import datetime

from sqlalchemy import BigInteger, DateTime, Float, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class Telemetry(Base):
    __tablename__ = "telemetry"

    # BIGINT ZORUNLU — int4 degil. 600 cihaz olceginde bu tabloya gunde ~26M
    # satir girer; int4 tavani (2.147.483.647) ~83 GUNDE tukenir ve o an
    # nextval() 'integer out of range' atar. Telemetry consumer batch'i TEK
    # commit ettigi icin (telemetry_consumer.py) commit patlar, hicbir mesaj
    # ack edilmez ve telemetri alimi TAMAMEN durur.
    # DIKKAT: retention bunu COZMEZ — satirlari silmek sequence'i geri almaz.
    # Migration 0021 mevcut kurulumlari int4 -> int8'e cevirir.
    #
    # with_variant(Integer, "sqlite"): SQLite'ta yalnizca TAM olarak
    # `INTEGER PRIMARY KEY` rowid takma adidir ve otomatik artar; `BIGINT
    # PRIMARY KEY` artmaz ve testlerde NOT NULL hatasi verir. Uretim
    # (PostgreSQL) BIGSERIAL alir, testler INTEGER ile calisir.
    id: Mapped[int] = mapped_column(
        BigInteger().with_variant(Integer, "sqlite"), primary_key=True
    )
    # device_id / signal_key uzerinde AYRI index YOK — bilincli.
    # `idx_telemetry_device_signal_ts` (device_id, signal_key,
    # source_timestamp DESC) bilesik index'i mevcut tum sorgulari karsiliyor:
    # kodda signal_key TEK BASINA filtrelenen bir sorgu yok, hepsi device_id
    # ile birlikte geliyor (public.py, signals.py, alarm_reconciliation.py).
    # Ayri index'ler gunde ~26M gereksiz index yazimi uretiyordu; migration
    # 0022 dusuruyor.
    # ILERIDE: yalnizca signal_key ile filtreleyen bir sorgu eklenirse o zaman
    # ayri bir index gerekir — bu tablo 30 dakikalik pencerede kaldigi icin
    # seq scan de kabul edilebilir maliyettedir.
    device_id: Mapped[int] = mapped_column(ForeignKey("devices.id"))
    signal_key: Mapped[str] = mapped_column(String(120))
    # DNP3 Group 110 (Octet String) sinyallerinde gateway numeric value yerine
    # value_string yollar; bu satirlarda value NULL olur. Numeric tipler icin
    # value her zaman dolu gelir.
    value: Mapped[float | None] = mapped_column(Float, nullable=True)
    value_string: Mapped[str | None] = mapped_column(Text, nullable=True)
    quality: Mapped[str] = mapped_column(String(50), default="good")
    source_timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True))
