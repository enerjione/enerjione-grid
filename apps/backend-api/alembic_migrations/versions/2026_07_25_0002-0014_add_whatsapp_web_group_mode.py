"""add_whatsapp_web_group_mode

Revision ID: 0014
Revises: 0013
Create Date: 2026-07-25 00:00:02.000000
"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op


revision: str = "0014"
down_revision: Union[str, None] = "0013"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute(
        "ALTER TABLE notification_settings ADD COLUMN IF NOT EXISTS "
        "whatsapp_web_group_mode BOOLEAN NOT NULL DEFAULT FALSE"
    )


def downgrade() -> None:
    op.execute("ALTER TABLE notification_settings DROP COLUMN IF EXISTS whatsapp_web_group_mode")
