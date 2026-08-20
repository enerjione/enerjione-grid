"""gateway_updates — gateway yazilim guncellemesinin backend tarafindaki durumu.

NEDEN YENI TABLO (alternatifler elendi)
---------------------------------------
* `gateways`'e kolon: o satir `/pending` ucunda SANIYEDE BIR UPDATE
  ediliyor; her UPDATE satirin tamamini yeniden yaziyor (MVCC). Nadiren
  degisen ama genis bir veriyi sicak satira koymak, saniyede bir daha fazla
  byte yazmak demekti. `gateway_health` ayni gerekceyle ayrilmisti.
* `system_events`: denetim orada KALIYOR, ama "su an hangi durumda" sorusu
  liste ekraninda gateway BASINA cevaplanmali. Olaydan turetmek her istekte
  tarama + durum yeniden insasi demek; ustelik olay kaydi retention'a tabi
  (2 yil FIFO) ve budandiginda durum sessizce kaybolurdu.
* `gateway_health`: o satirin sahibi GATEWAY (kendi heartbeat'i, upsert ile
  eziliyor). Buradaki veri BACKEND'in denetim izidir; bir heartbeat'in onu
  ezmesi kabul edilemez.

Gateway basina TEK satir, upsert. Gecmis olay kaydinda.

Revision ID: 0073
Revises: 0072
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0073"
down_revision: Union[str, None] = "0072"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ZATEN VARSA ATLA. Temiz kurulum ile yukseltilen kurulum ayni yerden
    # gecmiyor (bkz. 0072 legacy sema materyalizasyonu); bu tablo ileride
    # create_all ile de olusmus olabilir. Var olani yeniden yaratmaya
    # calismak yukseltmeyi kirardi.
    bind = op.get_bind()
    if sa.inspect(bind).has_table("gateway_updates"):
        return

    op.create_table(
        "gateway_updates",
        sa.Column("gateway_code", sa.String(length=50), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False, server_default="idle"),
        sa.Column("from_version", sa.String(length=40), nullable=True),
        sa.Column("from_image", sa.String(length=400), nullable=True),
        sa.Column("to_version", sa.String(length=40), nullable=True),
        sa.Column("to_image", sa.String(length=400), nullable=True),
        sa.Column("expected_digest", sa.String(length=80), nullable=True),
        sa.Column("request_id", sa.String(length=64), nullable=True),
        sa.Column("started_by", sa.String(length=120), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column(
            "is_rollback", sa.Boolean(), nullable=False, server_default=sa.false()
        ),
        sa.PrimaryKeyConstraint("gateway_code"),
    )
    # Liste ekrani "guncellemesi devam eden var mi" diye suzuyor.
    op.create_index(
        "ix_gateway_updates_status", "gateway_updates", ["status"], unique=False
    )


def downgrade() -> None:
    bind = op.get_bind()
    if not sa.inspect(bind).has_table("gateway_updates"):
        return
    op.drop_index("ix_gateway_updates_status", table_name="gateway_updates")
    op.drop_table("gateway_updates")
