"""project_settings — UYDU bataryalari icin ayri voltaj esigi

Uydu hucresi (sat01/sat02/sat03) ile RTU'yu besleyen master hucresi ayni
voltaj araliginda calismaz. Proje ayarinda tek cift esik vardi ve uydular da
onunla olculuyordu: sahada saglam uydular ekranda SURKELI %0 gorunuyordu
(olculen ~3,05 V, master esigi 3,40 V). Bu sessiz bir yanlislik — ne hata ne
uyari uretir, yalnizca sayi yanlistir; ustelik gercekten biten bir hucreyi de
gizler, cunku zaten %0 yaziyordu.

NULL = uydular master esigini kullanmaya devam eder. Guncelleyen bir kurulumda
hicbir sey degismez; kurulumcu Proje Ayarlari'ndan uydu araligini girdiginde
devreye girer.

Revision ID: 0062
Revises: 0061
"""

from alembic import op
import sqlalchemy as sa

revision = "0062"
down_revision = "0061"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "project_settings",
        sa.Column("battery_voltage_low_sat", sa.Float(), nullable=True),
    )
    op.add_column(
        "project_settings",
        sa.Column("battery_voltage_full_sat", sa.Float(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("project_settings", "battery_voltage_full_sat")
    op.drop_column("project_settings", "battery_voltage_low_sat")
