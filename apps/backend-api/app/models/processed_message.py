from datetime import datetime

from sqlalchemy import BigInteger, DateTime, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class ProcessedMessage(Base):
    __tablename__ = "processed_messages"

    # BIGINT ZORUNLU — bkz. models/telemetry.py'deki ayni gerekce. Bu tabloya
    # islenen HER telemetri mesaji icin bir satir girer, yani telemetry ile
    # AYNI hizda (~26M/gun) buyur ve int4 tavani ~83 gunde tukenir.
    # with_variant gerekcesi icin bkz. models/telemetry.py.
    id: Mapped[int] = mapped_column(
        BigInteger().with_variant(Integer, "sqlite"), primary_key=True
    )
    # consumer_name / message_id uzerinde AYRI index YOK — bilincli.
    # Bu tablo HER ZAMAN ikisi BIRLIKTE sorgulanir (idempotency_service.py ve
    # telemetry_consumer.py; tek basina sorgulayan cagri yok), ve bunu
    # `uq_processed_message_consumer_msg` bilesik UNIQUE index'i karsilar.
    # Ayri index'ler gunde ~26M gereksiz index yazimi + ~13 GB bosa disk
    # demekti; migration 0022 mevcut kurulumlarda dusuruyor.
    consumer_name: Mapped[str] = mapped_column(String(80))
    message_id: Mapped[str] = mapped_column(String(120))
    # processed_at index'i KALIR: retention purge'u (`processed_at < cutoff`)
    # bunun uzerinde yurur.
    processed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
