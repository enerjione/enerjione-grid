"""baseline_initial_schema

Revision ID: 0001
Revises:
Create Date: 2026-05-12 00:00:00.000000

Bu migration **bos** — mevcut deploylar `Base.metadata.create_all()` +
`ALTER TABLE IF NOT EXISTS` zinciriyle schema'yi olusturdugu icin bu
baseline'i degil, sadece "Alembic versiyon kaydini" temsil eder.

Mevcut bir deploy'u Alembic'e tasimak icin:
    $ alembic stamp head

Boylece `alembic_version` tablosu olusur ve `head=0001` olur; sonraki
schema degisiklikleri normal `alembic revision --autogenerate` ile turetilir.

Sifirdan yeni deploy:
    $ python -m scripts.init_db  (eski create_all)
    $ alembic stamp head

Veya ileride (create_all kaldirilirsa):
    $ alembic upgrade head
"""
from __future__ import annotations

from typing import Sequence, Union

# revision identifiers, used by Alembic.
revision: str = "0001"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Baseline — schema zaten create_all ile olusturulmus durumda.
    # Sonraki migration'lar bu revision'i parent kabul edip ek
    # ALTER TABLE / CREATE TABLE adimlarini buradan zincirleyecek.
    pass


def downgrade() -> None:
    # Baseline'in altina inilemez (DROP SCHEMA degil; bu son kademe).
    pass
