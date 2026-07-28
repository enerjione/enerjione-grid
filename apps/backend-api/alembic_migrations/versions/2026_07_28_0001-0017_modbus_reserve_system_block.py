"""modbus_reserve_system_block

Modbus adres plani: ilk 100 register (0..99) artik SISTEM METRIKLERI icin
rezerve. Cihaz bloklari 100'un katlarindan baslar:

    cihaz 1 -> 100..199   cihaz 2 -> 200..299   cihaz 3 -> 300..399

Boylece Sinyaller sayfasindan bir sinyale offset 50 verildiginde o sinyal
1. cihazda 150, 2. cihazda 250, 3. cihazda 350 adresinden yayinlanir.

Yapilan:
  * `outbound_targets.modbus_base_address` sunucu varsayilani 0 -> 100
  * base_address'i HALA 0 olan mevcut Modbus hedefleri 100'e cekilir.
    (0 = "hic dokunulmamis varsayilan" anlamina geliyordu; operator bilerek
    0 sectiyse bu deger yine 100'e gider — adresleme duzeni tek tip kalsin
    diye bilincli tercih. Farkli bir base isteyen hedef arayuzden tekrar
    ayarlanabilir.)

Revision ID: 0017
Revises: 0016
Create Date: 2026-07-28 00:00:01.000000
"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op


revision: str = "0017"
down_revision: Union[str, None] = "0016"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute(
        "ALTER TABLE outbound_targets "
        "ALTER COLUMN modbus_base_address SET DEFAULT 100"
    )
    op.execute(
        "UPDATE outbound_targets SET modbus_base_address = 100 "
        "WHERE protocol = 'modbus' AND modbus_base_address = 0"
    )


def downgrade() -> None:
    op.execute(
        "ALTER TABLE outbound_targets "
        "ALTER COLUMN modbus_base_address SET DEFAULT 0"
    )
