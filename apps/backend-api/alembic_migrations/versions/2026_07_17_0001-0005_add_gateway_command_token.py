"""add_gateway_command_token

Revision ID: 0005
Revises: 0004
Create Date: 2026-07-17 00:00:01.000000

Cihaz DNP3 komut (CROB) proxy'si icin gateway'e ozel Bearer token kolonu.
Backend `POST control_host:control_port/operate` cagirirken bu token'i Bearer
olarak yollar; gateway kendi .env GATEWAY_COMMAND_TOKEN ile eslestirir.

NOT: Ayni ALTER main.py create_tables() idempotent bootstrap blogunda da var
(ADD COLUMN IF NOT EXISTS). Iki yol da idempotent; cakismaz.
"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op


revision: str = "0005"
down_revision: Union[str, None] = "0004"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute(
        "ALTER TABLE gateways ADD COLUMN IF NOT EXISTS command_token VARCHAR(255)"
    )


def downgrade() -> None:
    op.execute("ALTER TABLE gateways DROP COLUMN IF EXISTS command_token")
