"""add_segment_device_orientation

Revision ID: 0004
Revises: 0003
Create Date: 2026-07-16 00:00:01.000000

Grid topoloji import + harita duzenleme icin eksik kolonlari ekler.

- poles.pole_type: pole / transformer / breaker sembol secimi
- line_segments.device_position_t: cihaz slot icindeki 0..1 konumu
- line_segments.device_orientation: FCI yesil/kirmizi kurulum yonu
- lines.branched_from_pole_id: bransman hattin bagli oldugu parent direk

NOT: Ayni ALTER'lar main.py create_tables() idempotent bootstrap blogunda da
var (ADD COLUMN IF NOT EXISTS). Iki yol da idempotent; cakismaz.
"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op


revision: str = "0004"
down_revision: Union[str, None] = "0003"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute(
        "ALTER TABLE poles ADD COLUMN IF NOT EXISTS pole_type VARCHAR(20) NOT NULL DEFAULT 'pole'"
    )
    op.execute("ALTER TABLE line_segments DROP CONSTRAINT IF EXISTS uq_segment_endpoints")
    op.execute(
        "ALTER TABLE line_segments ADD COLUMN IF NOT EXISTS device_position_t DOUBLE PRECISION"
    )
    op.execute(
        "ALTER TABLE line_segments ADD COLUMN IF NOT EXISTS device_orientation VARCHAR(20)"
    )
    op.execute(
        "ALTER TABLE lines ADD COLUMN IF NOT EXISTS branched_from_pole_id INTEGER "
        "REFERENCES poles(id) ON DELETE SET NULL"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_lines_branched_from_pole_id "
        "ON lines (branched_from_pole_id)"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS idx_lines_branched_from_pole_id")
    op.execute("ALTER TABLE lines DROP COLUMN IF EXISTS branched_from_pole_id")
    op.execute("ALTER TABLE line_segments DROP COLUMN IF EXISTS device_orientation")
    op.execute("ALTER TABLE line_segments DROP COLUMN IF EXISTS device_position_t")
    op.execute("ALTER TABLE poles DROP COLUMN IF EXISTS pole_type")
