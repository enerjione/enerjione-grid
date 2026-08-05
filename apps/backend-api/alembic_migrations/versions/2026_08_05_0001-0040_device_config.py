"""Cihaz yapilandirma sablonlari ve surumleri.

Horstmann SN2.0 `<seri>_Configuration.csv` dosyalarinin kalici karsiligi.

`raw` sutunlari `LargeBinary` — `Text` DEGIL. Dosyanin sonunda CSV olmayan
baytlar (muhtemel saglama toplami) var ve henuz cozulmedi; metin olarak
saklamak, herhangi bir kod cozme ya da satir sonu normalizasyonu o kuyrugu
bozar ve dosya cihaz tarafindan gecersiz sayilirdi.

Surumler append-only tasarlandi: "geri al" eskiyi geri yazmaz, YENI bir surum
yaratir. Bu yuzden `device_id + version` benzersizdir — es zamanli iki kayit
denemesinde ikincisi kisitta patlar; sessizce ustune yazip bir surumu
kaybetmekten iyidir.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0040"
down_revision: Union[str, None] = "0039"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "device_config_templates",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("device_model", sa.String(length=80), nullable=False),
        sa.Column("raw", sa.LargeBinary(), nullable=False),
        sa.Column("source_filename", sa.String(length=200), nullable=True),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("is_default", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("created_by", sa.String(length=80), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_device_config_templates_id"), "device_config_templates", ["id"]
    )
    op.create_index(
        op.f("ix_device_config_templates_device_model"),
        "device_config_templates",
        ["device_model"],
    )

    op.create_table(
        "device_config_versions",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("device_id", sa.Integer(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("raw", sa.LargeBinary(), nullable=False),
        sa.Column("source", sa.String(length=24), nullable=False),
        sa.Column("template_id", sa.Integer(), nullable=True),
        sa.Column("applied_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("created_by", sa.String(length=80), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["device_id"], ["devices.id"], ondelete="CASCADE"),
        # Sablon silinirse surum KALMALI: baytlar zaten kopyalanmistir, bu alan
        # yalnizca koken bilgisidir. CASCADE olsaydi bir sablonu silmek cihaz
        # gecmisini de silerdi.
        sa.ForeignKeyConstraint(
            ["template_id"], ["device_config_templates.id"], ondelete="SET NULL"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("device_id", "version", name="uq_device_config_version"),
    )
    op.create_index(
        op.f("ix_device_config_versions_id"), "device_config_versions", ["id"]
    )
    op.create_index(
        op.f("ix_device_config_versions_device_id"),
        "device_config_versions",
        ["device_id"],
    )
    op.create_index(
        op.f("ix_device_config_versions_created_at"),
        "device_config_versions",
        ["created_at"],
    )


def downgrade() -> None:
    op.drop_table("device_config_versions")
    op.drop_table("device_config_templates")
