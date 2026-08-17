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


def _kolonlar(bind) -> set[str]:
    return {c["name"] for c in sa.inspect(bind).get_columns("gateways")}


def upgrade() -> None:
    # IDEMPOTENT OLMAK ZORUNDA.
    #
    # Bu kod tabaninda iki farkli kurulum yolu var ve ikisi ayni migration'i
    # gorur:
    #   * yukseltme  -> kolon yok, alembic ekler
    #   * temiz/restore -> `create_all` kolonu MODELDEN zaten olusturur,
    #     ardindan alembic zinciri kosar
    # Ikinci yolda korumasiz `add_column` "column already exists" ile duser ve
    # RESTORE TAMAMLANAMAZ. CI'daki gercek v2.93 yedegi bunu yakaladi.
    bind = op.get_bind()
    if "command_delivery_token" not in _kolonlar(bind):
        op.add_column(
            "gateways",
            sa.Column("command_delivery_token", sa.String(length=255), nullable=True),
        )


def downgrade() -> None:
    bind = op.get_bind()
    if "command_delivery_token" in _kolonlar(bind):
        op.drop_column("gateways", "command_delivery_token")
