"""add_produces_fault

Revision ID: 0003
Revises: 0002
Create Date: 2026-06-03 00:00:01.000000

alarm_rules ve alarm_events tablolarina `produces_fault` BOOLEAN kolonu ekler.

produces_fault: "Bu alarm gercek bir hat arizasi uretir mi?"
  True (varsayilan): alarm geldiginde cihaz haritada kirmizi gosterilir ve
    Hat Arizalari'nda FaultEvent acilir (mevcut davranis).
  False: alarm yine olusur (Alarmlar ekraninda gorunur) ama haritada ariza
    gostermez ve hat arizasi acmaz — gecici/gurultulu arizalar icin.

Default TRUE -> mevcut tum kayitlar otomatik True olur (geriye donuk uyum).

NOT: Ayni ALTER'lar main.py create_tables() icindeki idempotent bootstrap
bloguna da eklenmistir, cunku mevcut sahalar alembic_version'i zaten head'e
stamp'lemis durumda ve bu revision onlarda otomatik kosmayabilir. Iki yol da
`IF NOT EXISTS` ile idempotent; cakismaz.
"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op


# revision identifiers, used by Alembic.
revision: str = "0003"
down_revision: Union[str, None] = "0002"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute(
        "ALTER TABLE alarm_rules ADD COLUMN IF NOT EXISTS produces_fault BOOLEAN NOT NULL DEFAULT TRUE"
    )
    op.execute(
        "ALTER TABLE alarm_events ADD COLUMN IF NOT EXISTS produces_fault BOOLEAN NOT NULL DEFAULT TRUE"
    )


def downgrade() -> None:
    op.execute("ALTER TABLE alarm_events DROP COLUMN IF EXISTS produces_fault")
    op.execute("ALTER TABLE alarm_rules DROP COLUMN IF EXISTS produces_fault")
