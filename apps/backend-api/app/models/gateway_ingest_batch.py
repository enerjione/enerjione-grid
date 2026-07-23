from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class GatewayIngestBatch(Base):
    __tablename__ = "gateway_ingest_batches"
    __table_args__ = (UniqueConstraint("gateway_code", "sequence_no", name="uq_gateway_sequence"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    gateway_code: Mapped[str] = mapped_column(
        ForeignKey("gateways.code", ondelete="CASCADE", onupdate="CASCADE"), index=True
    )
    sequence_no: Mapped[int] = mapped_column(Integer, index=True)
    sent_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
