from datetime import datetime, timezone

from sqlalchemy import DateTime, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class DeviceCommand(Base):
    """Cihaza gonderilecek DNP3 CROB komut kuyrugu.

    Gateway NAT arkasinda oldugundan backend gateway'e dogrudan ulasamaz;
    komut config-poll ile iletilir: backend buraya status='pending' satir yazar,
    gateway her config poll'de (~config_refresh_sec) pending komutlari ceker,
    CROB gonderir ve sonucu POST /gateways/{code}/command-results ile bildirir.

    Durum akisi: pending -> sent (gateway'e config'te iletildi) ->
    ok | failed (gateway sonuc bildirdi). expired = uzun sure sonuc gelmedi.
    """

    __tablename__ = "device_commands"

    id: Mapped[int] = mapped_column(primary_key=True, index=True)
    gateway_code: Mapped[str] = mapped_column(String(50), index=True)
    device_code: Mapped[str] = mapped_column(String(50), index=True)
    # Komut slug'i (SignalCatalog binary_output key'inin master. sonrasi) + cozulmus
    # DNP3 binary output index'i. Index queue zamaninda dondurulur ki sonradan
    # SignalCatalog degisse bile gonderilen komut sabit kalsin.
    command: Mapped[str] = mapped_column(String(80))
    dnp3_index: Mapped[int] = mapped_column(Integer)
    # Horstmann SN2 Device Profile PULSE desteklemez; yalnizca LATCH_ON/LATCH_OFF.
    # Default latch_on (pulse_on cihazda NOT_SUPPORTED / SELECT_FAIL doner).
    op_type: Mapped[str] = mapped_column(String(20), default="latch_on")
    count: Mapped[int] = mapped_column(Integer, default=1)
    on_time_ms: Mapped[int] = mapped_column(Integer, default=0)
    off_time_ms: Mapped[int] = mapped_column(Integer, default=0)

    status: Mapped[str] = mapped_column(String(20), default="pending", index=True)
    result_status: Mapped[str | None] = mapped_column(String(40), nullable=True)
    result_error: Mapped[str | None] = mapped_column(String(500), nullable=True)

    actor_username: Mapped[str | None] = mapped_column(String(150), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, index=True
    )
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
