"""telemetry_latest

Revision ID: 0028
Revises: 0027
Create Date: 2026-08-01 00:00:02.000000

Her (cihaz, sinyal) icin SON degeri tutan tablo.

NEDEN
-----
`/signals/live` bu tablodan once `telemetry` uzerinde `DISTINCT ON` ile
calisiyordu. 600 cihaz olceginde bedeli dogrusal degil:

  * `telemetry` 30 dakikalik pencerede ~2,16M satir tutar. `DISTINCT ON`
    PostgreSQL'de skip-scan DEGILDIR: pencerenin TAMAMI okunup 12.000 satira
    indirgenir.
  * Anasayfa bu ucu `device_codes` VERMEDEN cagiriyor ve WS koptugunda periyot
    30 sn'den 5 sn'ye dusuyor — baglanti bozuldugunda yuk 6 KATLANIYOR.
  * Istek basina ~311 MB bellek tepesi; container tavani 2 GB. Uc-bes
    esizamanli anasayfa backend-api'yi OOM'a goturuyordu.

Yeni tabloda okuma PK uzerinden ve pencere boyutundan BAGIMSIZ.

BACKFILL
--------
Tablo bos baslarsa canli deger ekrani, cihazlar yeni telemetri yollayana
kadar (cihaz basina ~10 sn, ama sessiz sinyaller icin cok daha uzun) BOS
gorunurdu. Bu yuzden mevcut `telemetry` penceresinden tek seferlik dolduruyoruz.
Kaynak tablo 30 dakikalik pencerede oldugu icin bu ucuz bir islemdir.

DIKKAT — `_try` YOK (bilincli)
------------------------------
0019/0023'teki "en iyi caba" politikasi burada UYGULANMIYOR: bu tablo
olusmazsa `/signals/live` calismaz ve arizanin sessiz kalmasi, boot'un
durmasindan daha kotu olur. Sema degisikligi ortamdan bagimsiz (TimescaleDB
gerektirmez), yani basarisiz olmasi icin gercek bir sebep yok.
"""
from __future__ import annotations

import logging
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

logger = logging.getLogger("alembic.runtime.migration")

revision: str = "0028"
down_revision: Union[str, None] = "0027"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

TABLE = "telemetry_latest"


def _table_exists(bind, table: str) -> bool:
    return (
        bind.execute(
            sa.text(
                "SELECT 1 FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace"
                " WHERE c.relkind IN ('r','p') AND n.nspname = current_schema()"
                "   AND c.relname = :t"
            ),
            {"t": table},
        ).first()
        is not None
    )


def upgrade() -> None:
    bind = op.get_bind()
    if _table_exists(bind, TABLE):
        logger.info("0028: %s zaten var — atlandi", TABLE)
        return

    op.create_table(
        TABLE,
        sa.Column(
            "device_id",
            sa.Integer(),
            sa.ForeignKey("devices.id", ondelete="CASCADE"),
            primary_key=True,
            nullable=False,
        ),
        sa.Column("signal_key", sa.String(length=120), primary_key=True, nullable=False),
        sa.Column("value", sa.Float(), nullable=True),
        sa.Column("value_string", sa.Text(), nullable=True),
        sa.Column("quality", sa.String(length=50), nullable=False, server_default="good"),
        sa.Column("source_timestamp", sa.DateTime(timezone=True), nullable=False),
        sa.Column("timestamp_quality", sa.String(length=20), nullable=True),
        sa.Column("device_event_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
    )

    # --- BACKFILL: mevcut telemetri penceresinden son degerleri al -----------
    # Tablo bos baslarsa canli ekran, sessiz sinyaller icin uzun sure bos
    # gorunurdu. Kaynak 30 dakikalik pencere oldugu icin bu ucuz.
    if not _table_exists(bind, "telemetry"):
        logger.warning("0028: telemetry tablosu yok — backfill atlandi")
        return

    sonuc = bind.execute(
        sa.text(
            f"""
            INSERT INTO {TABLE} (
                device_id, signal_key, value, value_string, quality,
                source_timestamp, timestamp_quality, device_event_at, updated_at
            )
            SELECT DISTINCT ON (device_id, signal_key)
                   device_id, signal_key, value, value_string, quality,
                   source_timestamp, timestamp_quality, device_event_at, now()
              FROM telemetry
             ORDER BY device_id, signal_key, id DESC
            ON CONFLICT (device_id, signal_key) DO NOTHING
            """
        )
    )
    logger.warning("0028: %s backfill satir=%s", TABLE, sonuc.rowcount)


def downgrade() -> None:
    bind = op.get_bind()
    if not _table_exists(bind, TABLE):
        return
    # Veri kaybi YOK: bu tablo `telemetry`den turetilmis bir onbellektir.
    op.drop_table(TABLE)
