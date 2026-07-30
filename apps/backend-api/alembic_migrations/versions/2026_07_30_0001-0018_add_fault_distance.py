"""add_fault_distance

Ariza bolgesinin hat basindan tel mesafesi (metre). Degerler
`fault_recompute_service` tarafindan her recompute turunda yeniden yazilir,
o yuzden mevcut kayitlar icin backfill GEREKMEZ: bir sonraki alarm/clear
tetiginde dolarlar (o ana kadar NULL kalir, UI mesafe blogunu gizler).

Revision ID: 0018
Revises: 0017
Create Date: 2026-07-30 00:00:01.000000
"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op


revision: str = "0018"
down_revision: Union[str, None] = "0017"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute(
        "ALTER TABLE fault_events ADD COLUMN IF NOT EXISTS zone_start_m DOUBLE PRECISION"
    )
    op.execute(
        "ALTER TABLE fault_events ADD COLUMN IF NOT EXISTS zone_end_m DOUBLE PRECISION"
    )
    op.execute(
        "ALTER TABLE fault_events ADD COLUMN IF NOT EXISTS zone_length_m DOUBLE PRECISION"
    )


def downgrade() -> None:
    op.execute("ALTER TABLE fault_events DROP COLUMN IF EXISTS zone_length_m")
    op.execute("ALTER TABLE fault_events DROP COLUMN IF EXISTS zone_end_m")
    op.execute("ALTER TABLE fault_events DROP COLUMN IF EXISTS zone_start_m")
