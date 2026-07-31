"""add_gateway_health

Revision ID: 0024
Revises: 0023
Create Date: 2026-07-31 00:00:06.000000

Gateway'in kendi bildirdigi saglik durumu icin tablo (heartbeat).

NEDEN GEREKLI
-------------
Saha gateway'i NAT arkasinda: `WORKER_HEALTH_HOST` varsayilani 127.0.0.1 ve
compose portu localhost'a bagliyor. Backend gateway'in `/health` ucuna
ULASAMIYOR. Sonuc: gateway'in outbox'i dolsa, dead-letter birikse, cihazlarin
%80'i kopuk olsa bile bu bilgi saha PC'sinin localhost'unda kaliyor ve cati
panelinde hicbir sey gorunmuyor — tam bir kor nokta.

Bilgi artik gateway'in ZATEN saniyede bir attigi komut-poll istegine binerek
geliyor (ek istek maliyeti yok).

NEDEN `gateways` TABLOSUNA KOLON EKLEMEDIK
-------------------------------------------
`/gateways/{code}/pending` 1 Hz cagriliyor ve zaten her cagrida
`gateways.last_seen_at` icin UPDATE + COMMIT atiyor. Saglik JSON'unu da o
satira yazmak saniyede bir guncellenen SICAK bir satiri daha da sisirirdi:
her UPDATE yeni satir surumu uretir (MVCC), autovacuum'un ana musterisi olur
ve `gateways` index'leri surekli yeniden yazilir. Ayri tabloda gateway basina
tek satir tutulup upsert ediliyor.

GERIYE UYUM
-----------
Tablo BOS baslar. Eski gateway'ler (0.4.x) saglik gondermez; onlar icin satir
hic olusmaz ve bu NORMAL kabul edilir. "Saglik verisi yok" bir ALARM SEBEBI
DEGILDIR — aksi halde mevcut filonun tamami ilk gunden alarm uretirdi.
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "0024"
down_revision: Union[str, None] = "0023"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "gateway_health",
        # Gateway basina TEK satir — upsert edilir.
        sa.Column("gateway_code", sa.String(length=50), primary_key=True),
        sa.Column("status", sa.String(length=20), nullable=False, server_default="unknown"),
        sa.Column("issues", sa.String(length=1000), nullable=True),
        sa.Column("outbox_pending", sa.Integer(), nullable=True),
        sa.Column("outbox_dead_letter", sa.Integer(), nullable=True),
        sa.Column("devices_total", sa.Integer(), nullable=True),
        sa.Column("devices_online", sa.Integer(), nullable=True),
        sa.Column("devices_recovering", sa.Integer(), nullable=True),
        sa.Column("devices_lost", sa.Integer(), nullable=True),
        sa.Column("uptime_sec", sa.Integer(), nullable=True),
        sa.Column("gateway_version", sa.String(length=40), nullable=True),
        # Ham govde: ileride gateway yeni alan eklerse kaybolmasin.
        sa.Column("raw_json", sa.Text(), nullable=True),
        # BACKEND saati — gateway saatine guvenilmez (RTC pili bitince
        # saha cihazi 2000-01-01'e donebiliyor).
        sa.Column("reported_at", sa.DateTime(timezone=True), nullable=False),
        if_not_exists=True,
    )
    op.create_index(
        "ix_gateway_health_reported_at",
        "gateway_health",
        ["reported_at"],
        if_not_exists=True,
    )


def downgrade() -> None:
    op.drop_index(
        "ix_gateway_health_reported_at", table_name="gateway_health", if_exists=True
    )
    op.drop_table("gateway_health", if_exists=True)
