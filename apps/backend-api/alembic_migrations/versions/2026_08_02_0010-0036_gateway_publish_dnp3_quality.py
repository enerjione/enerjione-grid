"""gateway_publish_dnp3_quality

Revision ID: 0036
Revises: 0035
Create Date: 2026-08-02

DNP3 KALITE BAYRAKLARI — GATEWAY BAZINDA ANAHTAR
------------------------------------------------
Gateway bugun her okumayi `quality: "good"` olarak yayinliyor. Outstation bir
noktayi `REFERENCE_ERR` ile raporladiginda (orn. CT referansi kaybolmus,
`value=0.0`) bu bilgi SCADA'ya "gecerli olcum" olarak gidiyor: hat akimi 0 A
sanilir, "hat enerjisiz" yorumu yapilir.

Gateway tarafi bu kaliteleri (`invalid` / `restart` / `forced`) uretebiliyor
ama `GATEWAY_PUBLISH_DNP3_QUALITY` bayragiyla KAPALI tutuluyordu. Backend
tarafi v2.28.0'dan beri hazir.

NEDEN FILO GENELI DEGIL, GATEWAY BAZINDA
----------------------------------------
Acmak SAHA DAVRANISINI DEGISTIRIR: kotu kaliteli okumalar alarm
degerlendirmesinden bloke olmaya baslar (alarm durumu DONAR, acilmaz ve
kapanmaz). Bu dogru davranis, ama tum filoda birden acmak kontrolsuz olurdu.
Tek bir gateway'de denenip yayilabilsin diye kolon gateway tablosunda.

VARSAYILAN FALSE — mevcut kurulumlarin davranisi degismiyor. Server default
de `false`: migration'dan sonra eski satirlar NULL kalmasin.

GERI ALINABILIR: downgrade kolonu dusurur, gateway'ler eski davranisa doner
(compose yeniden uretilene kadar container'da eski deger kalir; kapatmak icin
gateway ayarlarindan kapatip kurulumu tazelemek yeterli).
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0036"
down_revision = "0035"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "gateways",
        sa.Column(
            "publish_dnp3_quality",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
    )


def downgrade() -> None:
    op.drop_column("gateways", "publish_dnp3_quality")
