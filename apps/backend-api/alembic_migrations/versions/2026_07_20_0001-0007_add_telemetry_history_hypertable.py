"""add_telemetry_history_hypertable

Revision ID: 0007
Revises: 0006
Create Date: 2026-07-20 00:00:01.000000

Historian: okunan tum telemetriyi uzun sure saklayan TimescaleDB hypertable.

`telemetry` (canli deger, ~30dk retention) dokunulmadan kalir. Bu tablo AI
analizi + grafik + geriye donuk sorgu icin ayri arsiv:
  * hypertable, source_timestamp partition (1 gun chunk)
  * 90 gun ham retention (native policy)
  * 7 gunden eski chunk'lar compress
  * 1dk ve 1saat continuous aggregate (avg/min/max/count) -> sonsuz saklanir

TimescaleDB extension YOKSA (orn. dev'de vanilla postgres:16) migration duz
tabloyu birakip hypertable/policy adimlarini atlar (idempotent, kirilmaz). Uretimde
timescale/timescaledb imaji ile extension mevcut olur ve tam kurulum calisir.

NOT: Tablo Base.metadata.create_all (main.py) ile de yaratilir (duz tablo). Bu
migration create_hypertable(if_not_exists) ile onu hypertable'a cevirir; iki yol
cakismaz.
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "0007"
down_revision: Union[str, None] = "0006"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _timescaledb_available(bind) -> bool:
    """timescaledb extension kurulabilir/kurulu mu? Yoksa hypertable adimlari
    atlanir (vanilla postgres dev ortami)."""
    row = bind.execute(
        sa.text(
            "SELECT 1 FROM pg_available_extensions WHERE name = 'timescaledb'"
        )
    ).first()
    return row is not None


def upgrade() -> None:
    bind = op.get_bind()

    # Duz tablo — vanilla postgres'te de calisir. create_all ile ayni sema.
    op.create_table(
        "telemetry_history",
        sa.Column("device_id", sa.Integer(), sa.ForeignKey("devices.id"), primary_key=True),
        sa.Column("signal_key", sa.String(length=120), primary_key=True),
        sa.Column("source_timestamp", sa.DateTime(timezone=True), primary_key=True),
        sa.Column("value", sa.Float(), nullable=True),
        sa.Column("value_string", sa.Text(), nullable=True),
        sa.Column("quality", sa.String(length=50), server_default="good", nullable=False),
        if_not_exists=True,
    )
    op.create_index(
        "ix_telemetry_history_device_signal_ts",
        "telemetry_history",
        ["device_id", "signal_key", "source_timestamp"],
        if_not_exists=True,
    )

    if not _timescaledb_available(bind):
        # Dev / vanilla postgres: duz tablo yeterli, hypertable adimlari atlanir.
        return

    # TimescaleDB 2.x DDL — hepsi transaction-safe. Continuous aggregate'ler
    # `WITH NO DATA` ile olusturuluyor (materialization ilk policy job'unda,
    # transaction disinda olur), boylece migration transaction'i icinde
    # calisabilirler. Duz op.execute yeterli; autocommit hilesine gerek yok.
    op.execute("CREATE EXTENSION IF NOT EXISTS timescaledb")

    # Mevcut tabloyu hypertable'a cevir. migrate_data=True: create_all ile
    # gelmis satirlar varsa chunk'lara tasinir. PK zaten (device_id,
    # signal_key, source_timestamp) -> partition kolonu PK'da, kosul saglandi.
    op.execute(
        "SELECT create_hypertable("
        "'telemetry_history', 'source_timestamp',"
        " chunk_time_interval => INTERVAL '1 day',"
        " migrate_data => TRUE, if_not_exists => TRUE)"
    )

    # 90 gun ham retention.
    op.execute(
        "SELECT add_retention_policy('telemetry_history',"
        " INTERVAL '90 days', if_not_exists => TRUE)"
    )

    # Compression: 7 gunden eski chunk'lar. device_id+signal_key segment ->
    # ayni seri ardisik saklanir, sikistirma orani yuksek.
    op.execute(
        "ALTER TABLE telemetry_history SET ("
        " timescaledb.compress,"
        " timescaledb.compress_segmentby = 'device_id, signal_key',"
        " timescaledb.compress_orderby = 'source_timestamp DESC')"
    )
    op.execute(
        "SELECT add_compression_policy('telemetry_history',"
        " INTERVAL '7 days', if_not_exists => TRUE)"
    )

    # Continuous aggregate 1 dakika: numeric sinyaller icin ozet. String
    # sinyaller (value NULL) avg'de NULL kalir; sorunsuz.
    op.execute(
        "CREATE MATERIALIZED VIEW IF NOT EXISTS telemetry_history_1m"
        " WITH (timescaledb.continuous) AS"
        " SELECT device_id, signal_key,"
        "        time_bucket(INTERVAL '1 minute', source_timestamp) AS bucket,"
        "        avg(value) AS avg_value,"
        "        min(value) AS min_value,"
        "        max(value) AS max_value,"
        "        count(*)   AS sample_count"
        " FROM telemetry_history"
        " GROUP BY device_id, signal_key, bucket"
        " WITH NO DATA"
    )
    op.execute(
        "SELECT add_continuous_aggregate_policy('telemetry_history_1m',"
        " start_offset => INTERVAL '3 hours',"
        " end_offset => INTERVAL '1 minute',"
        " schedule_interval => INTERVAL '1 minute',"
        " if_not_exists => TRUE)"
    )

    # Continuous aggregate 1 saat.
    op.execute(
        "CREATE MATERIALIZED VIEW IF NOT EXISTS telemetry_history_1h"
        " WITH (timescaledb.continuous) AS"
        " SELECT device_id, signal_key,"
        "        time_bucket(INTERVAL '1 hour', source_timestamp) AS bucket,"
        "        avg(value) AS avg_value,"
        "        min(value) AS min_value,"
        "        max(value) AS max_value,"
        "        count(*)   AS sample_count"
        " FROM telemetry_history"
        " GROUP BY device_id, signal_key, bucket"
        " WITH NO DATA"
    )
    op.execute(
        "SELECT add_continuous_aggregate_policy('telemetry_history_1h',"
        " start_offset => INTERVAL '3 days',"
        " end_offset => INTERVAL '1 hour',"
        " schedule_interval => INTERVAL '1 hour',"
        " if_not_exists => TRUE)"
    )


def downgrade() -> None:
    bind = op.get_bind()

    if _timescaledb_available(bind):
        # Continuous aggregate'ler once. MATERIALIZED VIEW drop policy'leri de
        # temizler; tablo drop'u kalan retention/compression policy'lerini alir.
        op.execute("DROP MATERIALIZED VIEW IF EXISTS telemetry_history_1h")
        op.execute("DROP MATERIALIZED VIEW IF EXISTS telemetry_history_1m")

    # Tabloyu dusur (hypertable ise chunk'lariyla birlikte gider). Extension'i
    # DROP ETME — baska bir sey kullaniyor olabilir.
    op.drop_index("ix_telemetry_history_device_signal_ts", table_name="telemetry_history", if_exists=True)
    op.drop_table("telemetry_history")
