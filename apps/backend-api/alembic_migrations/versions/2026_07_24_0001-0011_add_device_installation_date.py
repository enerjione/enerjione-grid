"""add_device_installation_date

Revision ID: 0011
Revises: 0010
Create Date: 2026-07-24 00:00:01.000000
"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op


revision: str = "0011"
down_revision: Union[str, None] = "0010"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("ALTER TABLE devices ADD COLUMN IF NOT EXISTS installation_date DATE")


def downgrade() -> None:
    op.execute("ALTER TABLE devices DROP COLUMN IF EXISTS installation_date")
