"""repair_gateway_schema

Revision ID: 0010
Revises: 0009
Create Date: 2026-07-23 00:00:01.000000

Daha once startup'ta `stamp head` kullanilan sahalarda migration calismadan version
head olabiliyordu. Gateway sorgularinin bekledigi kolonlari idempotent tamamlar ve
cihaz/gateway bagini DB seviyesinde korur.
"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op


revision: str = "0010"
down_revision: Union[str, None] = "0009"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute(
        "ALTER TABLE gateways ADD COLUMN IF NOT EXISTS config_nonce "
        "INTEGER NOT NULL DEFAULT 0"
    )
    op.execute(
        "ALTER TABLE gateways ADD COLUMN IF NOT EXISTS refresh_nonce "
        "INTEGER NOT NULL DEFAULT 0"
    )
    op.execute(
        "ALTER TABLE gateways ADD COLUMN IF NOT EXISTS token_hash VARCHAR(128)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_gateways_token_hash ON gateways (token_hash)"
    )
    op.execute(
        "ALTER TABLE gateways ADD COLUMN IF NOT EXISTS initiating_port_base "
        "INTEGER NOT NULL DEFAULT 20100"
    )
    op.execute(
        "ALTER TABLE gateways ADD COLUMN IF NOT EXISTS initiating_port_count "
        "INTEGER NOT NULL DEFAULT 0"
    )
    # 0007/0009 migration'lari yanlis head stamp nedeniyle atlanmis olabilir.
    # Modelin bekledigi historian tablosunu duz PostgreSQL tablo olarak garanti et;
    # TimescaleDB hypertable/policy update.sh ensure adiminda tamamlanir.
    op.execute(
        "CREATE TABLE IF NOT EXISTS telemetry_history ("
        "device_id INTEGER NOT NULL REFERENCES devices(id), "
        "signal_key VARCHAR(120) NOT NULL, "
        "source_timestamp TIMESTAMPTZ NOT NULL, "
        "value DOUBLE PRECISION, value_string TEXT, "
        "quality VARCHAR(50) NOT NULL DEFAULT 'good', "
        "PRIMARY KEY (device_id, signal_key, source_timestamp))"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_telemetry_history_device_signal_ts "
        "ON telemetry_history (device_id, signal_key, source_timestamp)"
    )
    op.execute("ALTER TABLE devices ALTER COLUMN poll_interval_sec SET DEFAULT 2")

    # Kullanici cihazini silme: gecersiz/bos eski baglari atanmis degil durumuna al.
    op.execute("UPDATE devices SET gateway_code = NULL WHERE btrim(gateway_code) = ''")
    op.execute(
        "UPDATE devices AS d SET gateway_code = NULL "
        "WHERE d.gateway_code IS NOT NULL "
        "AND NOT EXISTS (SELECT 1 FROM gateways AS g WHERE g.code = d.gateway_code)"
    )
    # Ingest batch tarihce degil, idempotency state'i; olmayan gateway kalintisini sil.
    op.execute(
        "DELETE FROM gateway_ingest_batches AS b "
        "WHERE NOT EXISTS (SELECT 1 FROM gateways AS g WHERE g.code = b.gateway_code)"
    )

    op.execute(
        "DO $$ BEGIN "
        "IF NOT EXISTS (SELECT 1 FROM pg_constraint "
        "WHERE conrelid = 'devices'::regclass AND contype = 'f' "
        "AND pg_get_constraintdef(oid) LIKE 'FOREIGN KEY (gateway_code)%') THEN "
        "ALTER TABLE devices ADD CONSTRAINT fk_devices_gateway_code "
        "FOREIGN KEY (gateway_code) REFERENCES gateways(code) "
        "ON UPDATE CASCADE ON DELETE RESTRICT; "
        "END IF; END $$"
    )
    op.execute(
        "DO $$ BEGIN "
        "IF NOT EXISTS (SELECT 1 FROM pg_constraint "
        "WHERE conrelid = 'gateway_ingest_batches'::regclass AND contype = 'f' "
        "AND pg_get_constraintdef(oid) LIKE 'FOREIGN KEY (gateway_code)%') THEN "
        "ALTER TABLE gateway_ingest_batches ADD CONSTRAINT fk_gateway_ingest_batches_gateway_code "
        "FOREIGN KEY (gateway_code) REFERENCES gateways(code) "
        "ON UPDATE CASCADE ON DELETE CASCADE; "
        "END IF; END $$"
    )


def downgrade() -> None:
    op.execute(
        "ALTER TABLE gateway_ingest_batches "
        "DROP CONSTRAINT IF EXISTS fk_gateway_ingest_batches_gateway_code"
    )
    op.execute(
        "ALTER TABLE devices DROP CONSTRAINT IF EXISTS fk_devices_gateway_code"
    )
