"""add_whatsapp_web_alarm_dispatch

Revision ID: 0013
Revises: 0012
Create Date: 2026-07-25 00:00:01.000000
"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op


revision: str = "0013"
down_revision: Union[str, None] = "0012"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute(
        "ALTER TABLE notification_settings ADD COLUMN IF NOT EXISTS "
        "whatsapp_web_group_jids VARCHAR(2000) NOT NULL DEFAULT ''"
    )
    op.execute(
        "ALTER TABLE alarm_rules ADD COLUMN IF NOT EXISTS "
        "notify_whatsapp_web BOOLEAN NOT NULL DEFAULT FALSE"
    )
    op.execute(
        "ALTER TABLE user_notification_preferences ADD COLUMN IF NOT EXISTS "
        "whatsapp_web_enabled BOOLEAN NOT NULL DEFAULT FALSE"
    )


def downgrade() -> None:
    op.execute("ALTER TABLE notification_settings DROP COLUMN IF EXISTS whatsapp_web_group_jids")
    op.execute("ALTER TABLE alarm_rules DROP COLUMN IF EXISTS notify_whatsapp_web")
    op.execute("ALTER TABLE user_notification_preferences DROP COLUMN IF EXISTS whatsapp_web_enabled")
