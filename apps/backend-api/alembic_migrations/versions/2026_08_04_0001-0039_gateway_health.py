"""gateway_health tablosu — gateway heartbeat'inin kalici karsiligi.

Model (`app/models/gateway_health.py`) ve onu yazan servis
(`gateway_health_service.record_health`) 1.0.x'ten beri vardi ama migration
HIC uretilmemisti. Sahada sonucu agirdi: gateway `X-E1-Gateway-Health`
basligini gonderdiginde INSERT `UndefinedTable` ile patliyor, paylasilan
request transaction'i "aborted" kaliyor ve AYNI transaction'daki komut
sorgusu da dusuyordu -> `GET /pending` 500 -> gateway basligi 10 dakika
birakiyordu (v1.0.1 savunmasi). Yani komutlar akiyordu ama gateway sagligi
ve cihaz-link durumu backend'e HIC ulasmiyordu.

Tablo BUYUMEZ: `gateway_code` birincil anahtardir, servis upsert eder
(gateway basina TEK satir). Bu yuzden retention gerekmez. `reported_at`
indeksi staleness watchdog'un "en son ne zaman haber aldik" taramasi icin.

NOT: gorev tanimindaki `device_link_states` bir TABLO degildir —
`gateway_health_service.device_link_states()` fonksiyonu `raw_json`'dan
turetir; ayri migration gerekmez.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0039"
down_revision: Union[str, None] = "0038"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "gateway_health",
        sa.Column("gateway_code", sa.String(length=50), primary_key=True),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("issues", sa.String(length=1000), nullable=True),
        sa.Column("outbox_pending", sa.Integer(), nullable=True),
        sa.Column("outbox_dead_letter", sa.Integer(), nullable=True),
        sa.Column("devices_total", sa.Integer(), nullable=True),
        sa.Column("devices_online", sa.Integer(), nullable=True),
        sa.Column("devices_recovering", sa.Integer(), nullable=True),
        sa.Column("devices_lost", sa.Integer(), nullable=True),
        sa.Column("uptime_sec", sa.Integer(), nullable=True),
        sa.Column("gateway_version", sa.String(length=40), nullable=True),
        sa.Column("raw_json", sa.Text(), nullable=True),
        sa.Column("reported_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index(
        "ix_gateway_health_reported_at", "gateway_health", ["reported_at"]
    )


def downgrade() -> None:
    op.drop_index("ix_gateway_health_reported_at", table_name="gateway_health")
    op.drop_table("gateway_health")
