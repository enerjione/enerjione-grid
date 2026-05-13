"""add_ops_manager_role

Revision ID: 0002
Revises: 0001
Create Date: 2026-05-13 00:00:01.000000

UserRole enum'a 'OPS_MANAGER' degerini ekler.

ops_manager: Operasyon Yoneticisi — hatlari/cihazlari/sinyalleri salt-okunur
gorur, ekip yonetimi (responsibility areas) tam yetkili, SADECE operator
rolunde kullanici olusturup silebilir, toplu bildirim (web push/email/sms)
gonderebilir.

NOT: SQLAlchemy `Enum(UserRole)` Python member adlarini DB degeri olarak
kullaniyor (mevcut 'INSTALLER' kaydi bunu kanitliyor). Bu yuzden eklenen
deger UPPERCASE 'OPS_MANAGER'.
"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op


# revision identifiers, used by Alembic.
revision: str = "0002"
down_revision: Union[str, None] = "0001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ALTER TYPE ADD VALUE transaction icinde calistirilamaz; AUTOCOMMIT'e cek.
    conn = op.get_bind()
    conn = conn.execution_options(isolation_level="AUTOCOMMIT")
    conn.exec_driver_sql(
        "ALTER TYPE userrole ADD VALUE IF NOT EXISTS 'OPS_MANAGER'"
    )


def downgrade() -> None:
    # PostgreSQL enum'dan deger silmek native olarak desteklenmez —
    # type'i drop/recreate gerekir + tum referanslar. Risk yuksek, bos birakildi.
    pass
