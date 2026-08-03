"""Outbox — broker'a yayinlanmayi bekleyen olaylar.

INDEKS SECIMI OLCUMLE YAPILDI (2026-08-01, saha test cihazi)
------------------------------------------------------------
Tablo bir saatlik yuk testinde 326.027 satira / 272 MB'a cikti ve
veritabaninin EN BUYUK tablosu oldu (telemetrinin kendisi 65 MB).
`pg_stat_user_indexes` o anda sunu soyluyordu:

    ix_outbox_events_dedup_key    27 MB   330.691 tarama  <- dedup, gerekli
    outbox_events_pkey            14 MB   333.586 tarama  <- gerekli
    ix_outbox_events_created_at   14 MB          0 tarama <- KALDIRILDI
    ix_outbox_events_published_at 14 MB        737 tarama <- purge, gerekli
    ix_outbox_events_published   4.5 MB      2.372 tarama <- KISMI'ya cevrildi
    ix_outbox_events_topic       4.0 MB          0 tarama <- KALDIRILDI

Hic taranmayan iki indeks 18 MB yer kapliyordu ve HER INSERT'te
guncelleniyordu; saniyede ~90 ekleme oldugu icin bu bedava degil.

`published` uzerindeki indeks BOOLEAN idi: satirlarin neredeyse tamami
`true` oldugu icin secicilik yoktu, ama flush sorgusu (`WHERE published IS
false ORDER BY id LIMIT n`) tam da AZINLIKTAKI satirlari ariyor. Kismi
indekse cevrildi -> indeks yalnizca yayinlanmamis satirlari tutar, yani
normalde birkac KB kalir ve flush maliyeti tablo boyutundan BAGIMSIZ olur.

DEAD-LETTER (2026-08-03)
------------------------
Kismi indeksin yuklemine `dead_letter_at IS NULL` de eklendi. Gerekce:
dead-letter satirlar hala `published=False` oldugu icin eski yuklemle
indekste KALIRLARDI ve flush sorgusu (`ORDER BY id ASC`) her turda once
onlari gorurdu — yani tikanma, dead-letter damgasina RAGMEN surerdi.
Yeni yuklemle damgalanan satir indeksten de sorgudan da duser.
"""

from datetime import datetime

from sqlalchemy import Boolean, DateTime, Index, Integer, String, Text, and_
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class OutboxEvent(Base):
    __tablename__ = "outbox_events"

    id: Mapped[int] = mapped_column(primary_key=True)
    # `topic` uzerinde indeks YOK: tum satirlar tek konu tasidigi icin
    # secicilik sifirdi ve olcumde hic taranmadi.
    topic: Mapped[str] = mapped_column(String(120))
    dedup_key: Mapped[str] = mapped_column(String(120), unique=True, index=True)
    payload_json: Mapped[str] = mapped_column(Text)
    # Kismi indeks icin ayri `index=True` YOK — bkz. __table_args__.
    published: Mapped[bool] = mapped_column(Boolean, default=False)
    # `created_at` uzerinde indeks YOK: purge `published_at`e gore calisiyor,
    # bu sutun olcumde hic taranmadi.
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    published_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, index=True
    )
    # Kac kez yayinlanmaya calisildi. Tavan (outbox_max_publish_attempts)
    # asilinca satir dead-letter'a dusurulur.
    attempts: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    # Dolu ise satir KALICI basarisiz sayilir: flush sorgusundan ve kismi
    # indeksten duser, kendi (daha uzun) saklama penceresiyle temizlenir.
    dead_letter_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    # Son hata metni — dead-letter satirin TEK teshis kaniti.
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)

    __table_args__ = (
        # Flush sorgusunun tek dayanagi. KISMI: yalnizca yayinlanmamis VE
        # dead-letter'a dusmemis satirlar. `id` de indekste, cunku sorgu
        # `ORDER BY id ASC LIMIT n` yapiyor — boylece siralama icin ayrica
        # heap'e gidilmez.
        Index(
            "ix_outbox_events_unpublished",
            "id",
            postgresql_where=and_(published.is_(False), dead_letter_at.is_(None)),
        ),
        # Dead-letter purge'unun dayanagi (published_at indeksinin karsiligi).
        # KISMI: damgali satirlar azinliktir, tam indeks her INSERT'te bedel
        # cikarirdi.
        Index(
            "ix_outbox_events_dead_letter",
            "dead_letter_at",
            postgresql_where=dead_letter_at.is_not(None),
        ),
    )
