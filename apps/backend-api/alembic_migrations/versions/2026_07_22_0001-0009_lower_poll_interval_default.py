"""lower_poll_interval_default

Revision ID: 0009
Revises: 0008
Create Date: 2026-07-22 00:00:01.000000

Cihaz telemetri gecikmesini azaltmak icin varsayilan poll_interval_sec
degerini 5 -> 2 saniyeye dusurur.

- Kolon DEFAULT'u 2 yapilir (yeni cihazlar).
- Mevcut cihazlardan poll_interval_sec = 5 (eski default) olanlar 2'ye cekilir.
  Operatorun elle farkli bir deger (orn. 10) verdigi cihazlara DOKUNULMAZ.

Gerekce: gateway her cihazi ancak poll_interval_sec kadar surede bir backend'e
iletiyordu; 5 sn frontend'de tipik gecikmenin ana kaynagiydi. 2 sn hem daha
guncel veri hem gateway cycle'ini tikamayacak kadar makul.

NOT: Kolon default degisimi main.py bootstrap'inda tekrarlanmaz; bu tek seferlik
veri guncellemesidir. IF/WHERE ile idempotent (tekrar kosarsa zarar vermez).
"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op


# revision identifiers, used by Alembic.
revision: str = "0009"
down_revision: Union[str, None] = "0008"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Yeni cihazlar icin kolon default'u.
    op.execute("ALTER TABLE devices ALTER COLUMN poll_interval_sec SET DEFAULT 2")
    # Eski default (5) ile kalmis mevcut cihazlari hizlandir; elle ayarlananlara dokunma.
    op.execute("UPDATE devices SET poll_interval_sec = 2 WHERE poll_interval_sec = 5")


def downgrade() -> None:
    op.execute("ALTER TABLE devices ALTER COLUMN poll_interval_sec SET DEFAULT 5")
    op.execute("UPDATE devices SET poll_interval_sec = 5 WHERE poll_interval_sec = 2")
