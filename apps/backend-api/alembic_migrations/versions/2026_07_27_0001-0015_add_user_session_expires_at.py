"""add_user_session_expires_at

`user_sessions` tablosunda oturumun ne zaman gecersizlesecegi tutulmuyordu;
sadece `revoked_at IS NULL` filtresi vardi. Bu yuzden token'i coktan expire
olmus (ama logout edilmemis) her login satiri 'Aktif Oturumlar' sayfasinda
sonsuza kadar aktif gorunuyordu — ayni kullanici 5-10 kez listelenmis gibi.

`expires_at` eklendi; login sirasinda JWT exp degeri yazilir. Liste sorgusu
bu ani gecen satirlari filtreler. Eski satirlarda deger NULL kalir; backfill
icin login_at + 7 gun (remember-me ust siniri) varsayilir, boylece tarihi
kayitlar da makul bir noktada listeden dusar.

Revision ID: 0015
Revises: 0014
Create Date: 2026-07-27 00:00:01.000000
"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op


revision: str = "0015"
down_revision: Union[str, None] = "0014"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute(
        "ALTER TABLE user_sessions ADD COLUMN IF NOT EXISTS "
        "expires_at TIMESTAMPTZ NULL"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_user_sessions_expires_at "
        "ON user_sessions (expires_at)"
    )
    # Backfill: mevcut satirlarda exp bilinmiyor. remember-me ust siniri olan
    # 7 gunu varsayiyoruz — gercek TTL bundan kisaysa satir zaten revoke
    # edilmemis olsa bile en fazla 7 gun sonra listeden duser.
    op.execute(
        "UPDATE user_sessions SET expires_at = login_at + INTERVAL '7 days' "
        "WHERE expires_at IS NULL"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_user_sessions_expires_at")
    op.execute("ALTER TABLE user_sessions DROP COLUMN IF EXISTS expires_at")
