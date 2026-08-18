from datetime import datetime, timezone

from sqlalchemy import (
    BigInteger,
    DateTime,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class UnknownDeviceTelemetry(Base):
    """Cihazi HENUZ TANIMLI OLMAYAN telemetri okumasinin dayanikli karantinasi.

    NEDEN VAR
    ---------
    Eski davranis (bkz. `telemetry_consumer._persist_batch`) bilinmeyen cihaz
    gelince yalnizca uyari logluyor, `processed_messages`'a dedup satiri yazip
    mesaji ack ediyordu. Yani payload KALICI OLARAK kayboluyordu: JetStream
    mesaji ack'lendigi icin bir daha gelmiyor, dedup satiri da olasi bir
    yeniden teslimi yutuyordu. Cihaz sisteme birkac dakika sonra eklendiginde
    o aradaki tum olcumler geri getirilemezdi.

    Bu tablo o payload'u tutar; cihaz eklendikten sonra
    `unknown_device_replay` ayni is mantigiyla normal telemetri yoluna basar.

    BU BIR KESIF/PROVISION MEKANIZMASI DEGILDIR
    -------------------------------------------
    Buraya dusen kayit hicbir zaman Device/SignalCatalog/profil URETMEZ.
    Yalnizca veri dayanikliligi saglar; cihazi kurmak operatorun isidir.

    BOZUK PAYLOAD BURAYA GIRMEZ
    ---------------------------
    Parse edilemeyen JSON ve `TelemetryIn` dogrulamasindan gecemeyen mesaj
    mevcut DLQ yoluna gider. Bu tablo YALNIZCA "payload gecerli ama cihaz
    tanimli degil" durumu icindir; ikisini karistirmak DLQ'yu bilinmeyen
    cihaz gurultusuyle doldururdu.
    """

    __tablename__ = "unknown_device_telemetry"

    __table_args__ = (
        # IDEMPOTENCY VE YARIS KORUMASI AYNI KISITTAN GELIR.
        #
        # Ayni JetStream mesaji yeniden teslim edilirse (ack oncesi crash,
        # ack_wait asimi) ikinci bir satir OLUSMAMALI. Iki consumer ayni
        # mesaji ayni anda gorurse de tek satir kalmali. Ikisini de bu
        # bilesik UNIQUE + `ON CONFLICT DO UPDATE` karsilar; uygulama
        # seviyesinde "once SELECT sonra INSERT" yapmak yaris penceresi
        # birakirdi.
        UniqueConstraint(
            "consumer_name", "dedup_key", name="uq_unknown_telemetry_consumer_dedup"
        ),
        # Replay sorgusu: "su cihazin bekleyen kayitlari" (opsiyonel gateway
        # suzgeciyle). Bekleyen kayit sayisi bir cihaz icin binlerce olabilir.
        Index(
            "ix_unknown_telemetry_replay",
            "status",
            "device_code",
            "gateway_code",
        ),
        # Retention taramasi ve `oldest_pending_age` metrigi bunun uzerinde
        # yurur.
        Index("ix_unknown_telemetry_status_first_seen", "status", "first_seen_at"),
    )

    # BIGINT: bilinmeyen bir gateway yanlis kod uretirse bu tablo telemetri
    # hiziyla (~1 Hz x cihaz sayisi) buyuyebilir. int4 tavanina cikmak
    # istemiyoruz; kapasite siniri ayrica `unknown_telemetry_max_rows` ile
    # zorlaniyor ama tur secimi onun arizasina bagli kalmamali.
    id: Mapped[int] = mapped_column(
        BigInteger().with_variant(Integer, "sqlite"), primary_key=True
    )

    # `processed_messages` ile AYNI namespace: hangi consumer'in gozunden
    # bilinmiyordu. Replay, telemetri dedup'unu bu ad uzerinden kontrol eder.
    consumer_name: Mapped[str] = mapped_column(String(80))

    # TEKILLESTIRME ANAHTARI — `message_id` ile AYNI SEY DEGIL.
    #
    # Payload `message_id` tasiyorsa o kullanilir. Tasimiyorsa consumer her
    # teslimde YENI bir uuid4 uretir (bkz. `_persist_batch` parse adimi) ve o
    # deger yeniden teslimde DEGISIR — dedup anahtari olarak ise yaramaz.
    # O durumda broker kimligi (`js:<stream>:<sequence>`) kullanilir; stream
    # sequence yeniden teslimde SABIT kalir.
    dedup_key: Mapped[str] = mapped_column(String(200))

    # Payload'un kendi message_id'si (uretilmis olabilir). Replay sirasinda
    # `processed_messages` kontrolu ve yazilan dedup satiri bunu kullanir.
    message_id: Mapped[str] = mapped_column(String(120))

    # Telemetriyi ureten gateway. Replay izolasyonunun temeli: kod sonradan
    # BASKA bir gateway'in cihazi olarak tanimlanirsa bu kayit replay
    # EDILMEZ (bkz. unknown_device_replay).
    gateway_code: Mapped[str | None] = mapped_column(String(50), nullable=True)
    device_code: Mapped[str] = mapped_column(String(50), index=True)

    # Broker baglami — teshis icin. Gereksiz broker-ici alan tutulmuyor;
    # yalnizca mesaji stream'de yeniden bulmaya yeten kadari.
    subject: Mapped[str | None] = mapped_column(String(255), nullable=True)
    stream_sequence: Mapped[int | None] = mapped_column(
        BigInteger().with_variant(Integer, "sqlite"), nullable=True
    )

    # Ham payload (JSON metni). Replay bunu `TelemetryIn`'e geri cozer.
    payload_json: Mapped[str] = mapped_column(Text)

    signal_key: Mapped[str | None] = mapped_column(String(120), nullable=True)
    source_timestamp: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    # Neden karantinada: bugun tek deger var (`device_not_found`) ama alan
    # acik birakildi — ileride "cihaz pasif" gibi bir ayrim eklenirse satir
    # semasi degismesin.
    reason: Mapped[str] = mapped_column(String(40), default="device_not_found")
    # pending -> replayed. `failed` YOK: replay basarisiz olursa kayit
    # `pending` KALIR ve hata `last_replay_error`a yazilir; terminal bir
    # `failed` durumu payload'i olu kategoriye alip operatorun gozunden
    # kacirirdi.
    status: Mapped[str] = mapped_column(String(20), default="pending", index=True)

    seen_count: Mapped[int] = mapped_column(Integer, default=1)
    first_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow
    )
    last_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow
    )

    replayed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    replay_attempts: Mapped[int] = mapped_column(Integer, default=0)
    last_replay_error: Mapped[str | None] = mapped_column(String(500), nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, onupdate=_utcnow
    )
