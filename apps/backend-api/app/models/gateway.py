from datetime import datetime

from sqlalchemy import Boolean, DateTime, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class Gateway(Base):
    __tablename__ = "gateways"

    id: Mapped[int] = mapped_column(primary_key=True)
    code: Mapped[str] = mapped_column(String(50), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(255))
    host: Mapped[str] = mapped_column(String(120))
    listen_port: Mapped[int] = mapped_column(Integer)
    upstream_url: Mapped[str] = mapped_column(String(500), default="https://central.example.com/api/v1/telemetry/gateway")
    batch_interval_sec: Mapped[int] = mapped_column(Integer, default=5)
    max_devices: Mapped[int] = mapped_column(Integer, default=200)
    device_code_prefix: Mapped[str | None] = mapped_column(String(80), nullable=True)
    token: Mapped[str] = mapped_column(String(255), index=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    last_seen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, index=True)

    # Uzaktan yonetim: kontrol paneli bu adrese HTTP istegi atar
    # (health + gelecekte /control/* endpoint'leri icin).
    control_host: Mapped[str] = mapped_column(String(255), default="127.0.0.1", nullable=False)
    control_port: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    # RabbitMQ icin gateway'e ozel olarak provisionlanmis kullanici/parola.
    # Backend gateway create akisinda RabbitMQ Management API uzerinden
    # otomatik yaratir; compose YAML'i indirilirken bu degerlerden AMQP URL
    # uretilir. Bos gelirse fallback olarak global rabbitmq_url kullanilir.
    rabbitmq_username: Mapped[str | None] = mapped_column(String(120), nullable=True)
    rabbitmq_password: Mapped[str | None] = mapped_column(String(255), nullable=True)

    # Initiating mode cihazlar icin gateway'e ozel TCP server port araligi
    # baslangici. Aynı host'ta birden fazla gateway calistirildiginda port
    # catismasi olmamasi icin her gateway'e benzersiz 1000'lik bir aralik
    # atanir: 20100, 21100, 22100, 23100, ... (ilk gateway 20100-21099, ikinci
    # 21100-22099, vb.). 600 cihaz/aralik kapasitesi (her gateway'in icindeki
    # idx + base = port). Frontend "Yeni gateway ekle" akisinda otomatik
    # atanir; eski gateway'ler default 20100 kullanir (geriye uyumluluk).
    initiating_port_base: Mapped[int] = mapped_column(
        Integer,
        default=20100,
        nullable=False,
    )
