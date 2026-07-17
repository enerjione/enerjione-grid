"""add_device_commands

Revision ID: 0006
Revises: 0005
Create Date: 2026-07-17 00:00:02.000000

Cihaz DNP3 CROB komut kuyrugu (config-poll ile iletim). Gateway NAT arkasinda
oldugundan backend gateway'e ulasamaz; komut buraya pending yazilir, gateway
config poll'de ceker, sonucu POST /gateways/{code}/command-results ile bildirir.

NOT: Tablo Base.metadata.create_all (main.py) ile de yaratilir; bu migration
Alembic zincirini korur ve mevcut sahalarda idempotent CREATE saglar.
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "0006"
down_revision: Union[str, None] = "0005"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "device_commands",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("gateway_code", sa.String(length=50), nullable=False),
        sa.Column("device_code", sa.String(length=50), nullable=False),
        sa.Column("command", sa.String(length=80), nullable=False),
        sa.Column("dnp3_index", sa.Integer(), nullable=False),
        sa.Column("op_type", sa.String(length=20), nullable=False, server_default="pulse_on"),
        sa.Column("count", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("on_time_ms", sa.Integer(), nullable=False, server_default="100"),
        sa.Column("off_time_ms", sa.Integer(), nullable=False, server_default="100"),
        sa.Column("status", sa.String(length=20), nullable=False, server_default="pending"),
        sa.Column("result_status", sa.String(length=40), nullable=True),
        sa.Column("result_error", sa.String(length=500), nullable=True),
        sa.Column("actor_username", sa.String(length=150), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("sent_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_device_commands_gateway_code", "device_commands", ["gateway_code"])
    op.create_index("ix_device_commands_device_code", "device_commands", ["device_code"])
    op.create_index("ix_device_commands_status", "device_commands", ["status"])
    op.create_index("ix_device_commands_created_at", "device_commands", ["created_at"])


def downgrade() -> None:
    op.drop_index("ix_device_commands_created_at", table_name="device_commands")
    op.drop_index("ix_device_commands_status", table_name="device_commands")
    op.drop_index("ix_device_commands_device_code", table_name="device_commands")
    op.drop_index("ix_device_commands_gateway_code", table_name="device_commands")
    op.drop_table("device_commands")
