"""add_modbus_outbound

Modbus TCP outbound kanali. Iki adresleme modu:

  block : tek unit id, cihazlar register bloklarina dagilir
          (adres = base + slot*stride + offset). stride=100 ile 655 cihaz.
  unit  : her cihaz kendi unit (slave) id'sinde, ayni offset duzeni.
          Port basina 247 cihaz.

`outbound_modbus_slots` cihaz -> (unit_id, block_start) eslemesini KALICI
tutar. Adresler cihaz listesinden her seferinde yeniden turetilseydi, aradan
bir cihaz silindiginde sonraki tum cihazlarin adresi kayar ve SCADA'daki
etiketler sessizce yanlis noktayi gosterirdi.

Sinyal seviyesindeki offset'ler tabloya YAZILMAZ; katalog siralamasindan
deterministik uretilir (bkz. app/services/modbus_plan_service.py).
`signal_catalog.modbus_function` / `modbus_address` kolonlari (zaten mevcut)
opsiyonel MANUEL OVERRIDE olarak kullanilir.

Revision ID: 0016
Revises: 0015
Create Date: 2026-07-27 00:00:02.000000
"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op


revision: str = "0016"
down_revision: Union[str, None] = "0015"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


_TARGET_COLUMNS = (
    ("modbus_mode", "VARCHAR(10) NOT NULL DEFAULT 'block'"),
    ("modbus_unit_id", "INTEGER NOT NULL DEFAULT 1"),
    ("modbus_value_format", "VARCHAR(10) NOT NULL DEFAULT 'int16'"),
    ("modbus_word_order", "VARCHAR(10) NOT NULL DEFAULT 'big'"),
    ("modbus_block_stride", "INTEGER NULL"),
    ("modbus_base_address", "INTEGER NOT NULL DEFAULT 0"),
    ("modbus_allowed_peers", "VARCHAR(2000) NULL"),
)


def upgrade() -> None:
    for name, ddl in _TARGET_COLUMNS:
        op.execute(f"ALTER TABLE outbound_targets ADD COLUMN IF NOT EXISTS {name} {ddl}")

    op.execute(
        """
        CREATE TABLE IF NOT EXISTS outbound_modbus_slots (
            id          SERIAL PRIMARY KEY,
            target_id   INTEGER NOT NULL REFERENCES outbound_targets(id) ON DELETE CASCADE,
            device_id   INTEGER NOT NULL REFERENCES devices(id) ON DELETE CASCADE,
            slot_index  INTEGER NOT NULL DEFAULT 0,
            unit_id     INTEGER NOT NULL DEFAULT 1,
            block_start INTEGER NOT NULL DEFAULT 0,
            CONSTRAINT uq_modbus_slot_target_device UNIQUE (target_id, device_id)
        )
        """
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_outbound_modbus_slots_target_id "
        "ON outbound_modbus_slots (target_id)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_outbound_modbus_slots_device_id "
        "ON outbound_modbus_slots (device_id)"
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS outbound_modbus_slots")
    for name, _ddl in _TARGET_COLUMNS:
        op.execute(f"ALTER TABLE outbound_targets DROP COLUMN IF EXISTS {name}")
