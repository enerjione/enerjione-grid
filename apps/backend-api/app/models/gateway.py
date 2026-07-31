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
    # LEGACY: token plaintext olarak saklaniyor (geriye uyumluluk). Yeni
    # gateway create'lerinde `token_hash` doldurulur; eski kayitlar batch
    # migration ile hash'lenir. validate_gateway_token() once token_hash
    # bakarsa SHA-256 ile hmac.compare_digest, yoksa eski plaintext yoluna
    # duser. Tum kayitlar hash'lendiginde token kolonu kaldirilabilir.
    token: Mapped[str] = mapped_column(String(255), index=True)
    token_hash: Mapped[str | None] = mapped_column(String(128), index=True, nullable=True)

    @property
    def has_token(self) -> bool:
        """Arayuz icin: token TANIMLI MI — degerin KENDISI degil.

        `GatewayRead` token'i artik dondurmuyor (operator telemetri enjekte
        edebiliyordu). Arayuzun tek ihtiyaci "kurulu mu?" bilgisi.
        """
        return bool((self.token or "").strip() or (self.token_hash or "").strip())

    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    last_seen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, index=True)

    # Uzaktan yonetim: kontrol paneli bu adrese HTTP istegi atar
    # (health + gelecekte /control/* endpoint'leri icin).
    control_host: Mapped[str] = mapped_column(String(255), default="127.0.0.1", nullable=False)
    control_port: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    # Cihaz komutu (DNP3 CROB) proxy'si icin gateway'e ozel Bearer token.
    # Backend `POST control_host:control_port/operate` cagirirken bu token'i
    # Authorization: Bearer olarak yollar; gateway kendi .env
    # GATEWAY_COMMAND_TOKEN ile eslestirir. Bos ise komut endpoint'i devre disi.
    command_token: Mapped[str | None] = mapped_column(String(255), nullable=True)

    # RabbitMQ icin gateway'e ozel olarak provisionlanmis kullanici/parola.
    # Backend gateway create akisinda RabbitMQ Management API uzerinden
    # otomatik yaratir; compose YAML'i indirilirken bu degerlerden AMQP URL
    # uretilir. Bos gelirse fallback olarak global rabbitmq_url kullanilir.
    rabbitmq_username: Mapped[str | None] = mapped_column(String(120), nullable=True)
    rabbitmq_password: Mapped[str | None] = mapped_column(String(255), nullable=True)

    # Initiating mode TCP server portu icin host tarafi baslangici. Aynı host'ta
    # birden fazla gateway calistirildiginda port catismasi olmamasi icin her
    # gateway'e benzersiz 1000'lik blok atanir (20100, 21100, ...).
    initiating_port_base: Mapped[int] = mapped_column(Integer, default=20100, nullable=False)
    # Bu gateway icin acilacak port sayisi (= max initiating cihaz). Listening
    # cihazlar icin port acilmaz; sadece initiating cihaz sayisi kadar port
    # gerekli. Default 0 (sadece listening, cogu kurulum). Kullanici initiating
    # cihaz ekleyecegi zaman frontend formundan artirir.
    initiating_port_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    # Operator "tum cihazlara sorgu at" tetiklerse bu sayac artar.
    # Gateway config refresh sirasinda en son gordugu degerle kıyaslayarak
    # yeni bir tetik var mi anlar; varsa Class 0+1+2+3 integrity poll yapar.
    # Counter -> tetik kaybolmaz (gateway o sirada offline olsa bile sonraki
    # config refresh'te yakalar).
    refresh_nonce: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    # Config degisikligi sayaci. Cihaz/gateway config'i her degistiginde
    # (create/update/enable/disable) +1 artar. Gateway hafif komut-poll'de
    # (1sn) bu degeri gorur; en son gordugunden buyukse config'i HEMEN ceker
    # (5dk poll'u beklemez). Counter -> gateway offline olsa bile tetik kaybolmaz.
    config_nonce: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
