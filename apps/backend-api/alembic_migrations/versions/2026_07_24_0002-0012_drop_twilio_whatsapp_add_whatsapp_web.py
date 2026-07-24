"""drop_twilio_whatsapp_add_whatsapp_web

Revision ID: 0012
Revises: 0011
Create Date: 2026-07-24 00:00:02.000000
"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op


revision: str = "0012"
down_revision: Union[str, None] = "0011"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("ALTER TABLE notification_settings DROP COLUMN IF EXISTS sms_twilio_use_whatsapp")
    op.execute("ALTER TABLE notification_settings DROP COLUMN IF EXISTS sms_twilio_content_sid")
    op.execute("ALTER TABLE notification_settings DROP COLUMN IF EXISTS sms_twilio_content_vars")
    op.execute(
        "ALTER TABLE notification_settings ADD COLUMN IF NOT EXISTS "
        "whatsapp_web_enabled BOOLEAN NOT NULL DEFAULT FALSE"
    )


def downgrade() -> None:
    op.execute("ALTER TABLE notification_settings DROP COLUMN IF EXISTS whatsapp_web_enabled")
    op.execute(
        "ALTER TABLE notification_settings ADD COLUMN IF NOT EXISTS "
        "sms_twilio_use_whatsapp BOOLEAN NOT NULL DEFAULT FALSE"
    )
    op.execute(
        "ALTER TABLE notification_settings ADD COLUMN IF NOT EXISTS "
        "sms_twilio_content_sid VARCHAR(64) DEFAULT ''"
    )
    op.execute(
        "ALTER TABLE notification_settings ADD COLUMN IF NOT EXISTS "
        "sms_twilio_content_vars VARCHAR(2000) DEFAULT ''"
    )
