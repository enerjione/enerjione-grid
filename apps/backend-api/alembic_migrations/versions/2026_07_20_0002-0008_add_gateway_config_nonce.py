"""add_gateway_config_nonce

Revision ID: 0008
Revises: 0007
Create Date: 2026-07-20 00:00:02.000000

Gateway.config_nonce — config degisikligi sayaci. Cihaz/gateway config'i
degistiginde artar; gateway hafif komut-poll'de bu degeri gorup config'i
hemen ceker (5dk poll'u beklemeden). Komut kanali config-poll'den ayrildi.

NOT: Kolon Base.metadata.create_all (main.py) ile de yaratilir; bu migration
Alembic zincirini korur ve mevcut sahalarda idempotent ADD COLUMN saglar.
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "0008"
down_revision: Union[str, None] = "0007"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # IF NOT EXISTS: create_all mevcut sahada kolonu zaten yaratmis olabilir.
    op.execute(
        "ALTER TABLE gateways ADD COLUMN IF NOT EXISTS config_nonce INTEGER NOT NULL DEFAULT 0"
    )


def downgrade() -> None:
    op.execute("ALTER TABLE gateways DROP COLUMN IF EXISTS config_nonce")
