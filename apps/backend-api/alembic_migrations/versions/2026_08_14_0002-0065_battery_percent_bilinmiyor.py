"""`devices.battery_percent` NULL olabilir: "bilinmiyor" ile "dolu" ayrilir.

SORUN (denetim 2026-08-13)
--------------------------
Kolon NOT NULL ve varsayilani 100.0 idi. Sonuc: hic batarya telemetrisi
gondermemis (ya da bu olcumu hic desteklemeyen) bir cihaz, sisteme
eklendigi andan itibaren HER ekranda DOLU batarya gosteriyordu. Bu bir
"yesil yalan": sistem bilmedigi bir seyi "iyi" diye sunuyor. Sahada
batarya takibi yapan operator, hic veri gelmeyen cihazi saglikli sanar.

Artik NULL = "cihaz henuz bildirmedi"; arayuz bunu "—" olarak gosterir.

MEVCUT SATIRLAR
---------------
Gecmise donuk olarak "bu 100 gercek mi, varsayilan mi?" sorusunun cevabi
YOK. Bu yuzden yalnizca HIC telemetri almamis cihazlar (`last_update_at IS
NULL`) NULL'a cekilir — onlarin batarya bildirmis olmasi mumkun degildir,
yani bu guvenli ve kayipsiz bir cikarim. Telemetri almis cihazlarin degeri
DOKUNULMADAN birakilir; bir sonraki batarya okumasi zaten uzerine yazar.

Revision ID: 0065
Revises: 0064
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0065"
down_revision = "0064"
branch_labels = None
depends_on = None


def upgrade() -> None:
    baglanti = op.get_bind()
    inspector = sa.inspect(baglanti)
    if not inspector.has_table("devices"):
        return
    kolonlar = {c["name"]: c for c in inspector.get_columns("devices")}
    if "battery_percent" not in kolonlar:
        return

    op.alter_column(
        "devices",
        "battery_percent",
        existing_type=sa.Float(),
        nullable=True,
        server_default=None,
    )
    # Hic telemetri almamis cihazlarda 100.0 KESINLIKLE varsayilandir.
    op.execute(
        sa.text(
            "UPDATE devices SET battery_percent = NULL "
            "WHERE last_update_at IS NULL AND battery_percent IS NOT NULL"
        )
    )


def downgrade() -> None:
    baglanti = op.get_bind()
    inspector = sa.inspect(baglanti)
    if not inspector.has_table("devices"):
        return
    # NOT NULL'a donebilmek icin NULL'lari eski varsayilana cekmek ZORUNLU.
    # Bu, "bilinmiyor" bilgisini geri donusu olmayacak sekilde kaybeder —
    # downgrade zaten bu bilgiyi tasiyamayan bir semaya gidiyor.
    op.execute(
        sa.text("UPDATE devices SET battery_percent = 100.0 WHERE battery_percent IS NULL")
    )
    op.alter_column(
        "devices",
        "battery_percent",
        existing_type=sa.Float(),
        nullable=False,
        server_default=sa.text("100.0"),
    )
