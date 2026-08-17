"""gateways.command_delivery_token — kuyruklanmis komut duzlemi credential'i (F5A)

NEDEN AYRI ALAN
---------------
Bugun `/config` ile `/pending` ayni `GATEWAY_TOKEN` ile korunuyor. O token
sizarsa yalnizca konfigurasyon degil FIZIKSEL KOMUT duzlemi de ele gecer.
Bu alan komut duzlemini config duzleminden ayirir.

MEVCUT `command_token` NEDEN KULLANILMADI
-----------------------------------------
`gateways.command_token` legacy dogrudan `POST /operate` yoluna aittir. Onu
yeniden kullanmak iki farkli yetki alanini tek sirda birlestirir ve F5
yuzunden `/operate` yolunu istemeden canlandirabilirdi.

NULL = GECIS
------------
Mevcut kayitlar NULL ile gelir ve komut kanallari KESILMEZ: NULL olan gateway
eski davranisi surdurur (yalnizca `X-Gateway-Token`). Secret provision edilen
gateway strict moda gecer. Boylece backend rollout'u gateway rollout'undan
bagimsiz yapilabilir.
"""

from alembic import op
import sqlalchemy as sa

revision = "0070"
down_revision = "0069"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "gateways",
        sa.Column("command_delivery_token", sa.String(length=255), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("gateways", "command_delivery_token")
