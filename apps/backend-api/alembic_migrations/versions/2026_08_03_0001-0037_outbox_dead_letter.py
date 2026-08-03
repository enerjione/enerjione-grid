"""outbox_dead_letter

Revision ID: 0037
Revises: 0036
Create Date: 2026-08-03

SAHA OLCUMU (2026-08-03, 100 cihaz / 176 sinyal)
------------------------------------------------
`outbox_events` 1.7 GB / 2.32M satir ve EN ESKI SATIR 36 DAKIKALIK — ayarli
saklama penceresi 15 dakika oldugu halde. Iki ayri kusur var:

  1. Purge tavani uretimin ALTINDAydi (10.000 satir / 10sn = 1.000 satir/sn,
     uretim ~1.074 satir/sn). Bu kusur kod tarafinda cozuldu (purge artik
     RetentionWorker'in batch'li altyapisinda).
  2. DEAD-LETTER KAVRAMI YOKTU. Bir satirin yayini kalici olarak patliyorsa
     flush dongusu ayni batch'i sonsuza kadar yeniden deniyordu
     (head-of-line block) ve `published=False` satir HIC silinmedigi icin
     tablo sinirsiz buyuyordu. Bu migration ikinci kusuru kapatiyor.

NE EKLENIYOR
------------
  attempts       — kac kez yayin denendi (tavan asilinca dead-letter).
  dead_letter_at — damga. Dolu ise satir flush sorgusundan DUSER.
  last_error     — son hata metni; dead-letter satirin TEK teshis kaniti.

KISMI INDEKS YUKLEMI NEDEN GENISLIYOR
-------------------------------------
Dead-letter satirlar hala `published=False`. Eski yuklem (`published IS
FALSE`) onlari indekste TUTARDI ve flush sorgusu `ORDER BY id ASC` ile her
turda once onlari gorurdu — yani tikanma, dead-letter damgasina RAGMEN
surerdi. Yuklem `AND dead_letter_at IS NULL` ile genisletiliyor.

YUKLEM BICIMI KRITIK: `IS FALSE` / `IS NULL`, `= false` DEGIL. Kismi indeksin
yuklemi sorgunun yuklemiyle AYNI BICIMDE yazilmali; aksi halde planlayici
esdegerligi KANITLAYAMAZ ve indeksi hic kullanmaz. Bu tuzaga 0031'de gercekten
dusuldu ve duzeltmeye calistigi sorguyu 64 kat yavaslatti (bkz. 0031
docstring'indeki olcum tablosu).

CONCURRENTLY KULLANILMIYOR — 0031 ILE AYNI GEREKCE
--------------------------------------------------
Alembic migration'lari tek transaction icinde kosuyor ve CREATE/DROP INDEX
CONCURRENTLY transaction blogunda CALISMAZ. Ustelik migration servis
BASLAMADAN ONCE kosuyor (bkz. Dockerfile CMD): backend ayakta olmadigi icin
tabloya bu sirada INSERT gelmiyor, yani ACCESS EXCLUSIVE lock ingest'i
kilitlemiyor. 0031'de ayni tabloda olculen sureler CREATE 255 ms, DROP 5-74 ms.

`attempts` NOT NULL + SABIT server_default ile ekleniyor: PostgreSQL 11+
bunu metadata-only islem olarak yapar, 2.3M satirlik tablo YENIDEN YAZILMAZ.
(Hedef surum 16.)

GERI ALINABILIR: downgrade kolonlari dusurur ve kismi indeksi ESKI yuklemiyle
geri kurar.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0037"
down_revision = "0036"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "outbox_events",
        sa.Column(
            "attempts", sa.Integer(), nullable=False, server_default=sa.text("0")
        ),
    )
    op.add_column(
        "outbox_events",
        sa.Column("dead_letter_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column("outbox_events", sa.Column("last_error", sa.Text(), nullable=True))

    # SIRA ONEMLI: once YENI indeks kurulur, sonra eskisi dusurulur (0031 ile
    # ayni gerekce — arada kalan anda flush sorgusu destegini kaybetmesin).
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_outbox_events_unpublished_v2 "
        "ON outbox_events (id) "
        "WHERE published IS FALSE AND dead_letter_at IS NULL"
    )
    op.execute("DROP INDEX IF EXISTS ix_outbox_events_unpublished")
    op.execute(
        "ALTER INDEX ix_outbox_events_unpublished_v2 "
        "RENAME TO ix_outbox_events_unpublished"
    )

    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_outbox_events_dead_letter "
        "ON outbox_events (dead_letter_at) WHERE dead_letter_at IS NOT NULL"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_outbox_events_dead_letter")
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_outbox_events_unpublished_v1 "
        "ON outbox_events (id) WHERE published IS FALSE"
    )
    op.execute("DROP INDEX IF EXISTS ix_outbox_events_unpublished")
    op.execute(
        "ALTER INDEX ix_outbox_events_unpublished_v1 "
        "RENAME TO ix_outbox_events_unpublished"
    )
    op.drop_column("outbox_events", "last_error")
    op.drop_column("outbox_events", "dead_letter_at")
    op.drop_column("outbox_events", "attempts")
