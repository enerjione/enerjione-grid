"""outbox_index_diet

Revision ID: 0031
Revises: 0030
Create Date: 2026-08-01

OLCUM (saha test cihazi, 2 cekirdek, 8 cihaz, ~90 ekleme/sn)
------------------------------------------------------------
`outbox_events` bir saatte 326.027 satira / 272 MB'a cikti ve veritabaninin
EN BUYUK tablosu oldu — telemetrinin kendisi 65 MB. Postgres bostayken bile
CPU'nun ~%25'ini yiyordu.

`pg_stat_user_indexes` (ayni an):

    ix_outbox_events_dedup_key    27 MB   330.691 tarama
    outbox_events_pkey            14 MB   333.586 tarama
    ix_outbox_events_created_at   14 MB          0 tarama   <-- HIC
    ix_outbox_events_published_at 14 MB        737 tarama
    ix_outbox_events_published   4.5 MB      2.372 tarama
    ix_outbox_events_topic       4.0 MB          0 tarama   <-- HIC

BU MIGRATION NE YAPIYOR
-----------------------
1. `ix_outbox_events_created_at` ve `ix_outbox_events_topic` DUSURULUYOR.
   326 bin ekleme boyunca sifir tarama aldilar; buna karsilik her INSERT
   ikisini de guncelliyordu. `topic` zaten tek degerli (tum satirlar
   "telemetry.raw_received"), `created_at` icin de sorgulayan yok — purge
   `published_at` uzerinden calisiyor.

2. `ix_outbox_events_published` (boolean btree) yerine KISMI indeks:
   `ix_outbox_events_unpublished (id) WHERE published = false`.

   Gerekce: satirlarin neredeyse tamami `published=true`, yani boolean
   indeksin secicilik degeri yoktu ve tablo boyunca buyuyordu. Oysa onu
   kullanan TEK sorgu flush'un kendisi:

       SELECT ... WHERE published IS false ORDER BY id ASC LIMIT n

   Kismi indeks yalnizca yayinlanmamis satirlari tutar — normal isleyiste
   bu birkac yuz satir demek. Yani flush maliyeti tablonun toplam boyutundan
   BAGIMSIZ hale gelir; `id` de indekste oldugu icin siralama icin heap'e
   gidilmez.

GERI ALINABILIR: downgrade eski indeksleri aynen geri kurar.

YUKLEM BICIMI KRITIK: `IS FALSE`, `= false` DEGIL
--------------------------------------------------
Kismi indeksin yuklemi, sorgunun yuklemiyle AYNI BICIMDE yazilmali. Flush
sorgusu SQLAlchemy'de `published.is_(False)` ile kuruluyor ve `published IS
false` uretiyor. Indeks `= false` ile kurulursa planlayici ikisinin esdeger
oldugunu KANITLAYAMAZ ve indeksi hic kullanmaz.

Bu, cihazda olculdu (329.702 satirlik gercek tablo, ayni sorgu):

    boolean tam indeks (eski)            93 buffer     0,589 ms
    kismi indeks, WHERE published = false 25.490 buffer 37,7  ms   <-- SEQ SCAN
    kismi indeks, WHERE published IS FALSE     1 buffer  0,012 ms  <-- dogru

Yani yanlis bicim, duzeltmeye calistigi sorgunun kendisini 64 kat
YAVASLATIYORDU. Sorgu her 0,3 saniyede bir kosuyor.

NOT: CONCURRENTLY KULLANILMIYOR. Alembic migration'lari tek transaction
icinde kosuyor ve CREATE/DROP INDEX CONCURRENTLY transaction blogunda
CALISMAZ. Bu tablo icin kabul edilebilir: cihazda olculen sureler
CREATE 255 ms, DROP'lar 5-74 ms; migration zaten servis baslamadan ONCE,
uvicorn'dan once kosuyor (bkz. Dockerfile CMD).
"""

from alembic import op

revision = "0031"
down_revision = "0030"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # 1) Hic taranmayan iki indeks.
    op.execute("DROP INDEX IF EXISTS ix_outbox_events_created_at")
    op.execute("DROP INDEX IF EXISTS ix_outbox_events_topic")

    # 2) Boolean indeks -> kismi indeks.
    #
    # SIRA ONEMLI: once YENI indeks kurulur, sonra eskisi dusurulur. Tersi
    # olsaydi ikisi arasindaki anda flush sorgusu destegini kaybedip seq scan
    # yapardi; 326 bin satirlik bir tabloda bu her 0.3 saniyede bir tam
    # tarama demekti.
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_outbox_events_unpublished "
        "ON outbox_events (id) WHERE published IS FALSE"
    )
    op.execute("DROP INDEX IF EXISTS ix_outbox_events_published")


def downgrade() -> None:
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_outbox_events_published "
        "ON outbox_events (published)"
    )
    op.execute("DROP INDEX IF EXISTS ix_outbox_events_unpublished")
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_outbox_events_topic ON outbox_events (topic)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_outbox_events_created_at "
        "ON outbox_events (created_at)"
    )
