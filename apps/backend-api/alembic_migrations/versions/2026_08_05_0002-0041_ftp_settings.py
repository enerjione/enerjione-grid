"""FTP sunucu ayarlari — singleton tablo.

Gomulu/harici mod secimi + cihazlarin FTP ekranina girilecek kimlik bilgisi.
Parola `secrets_vault` ile sifreli saklanir (enc:v1:...), o yuzden TEXT.

Satir migration'da EKLENMEZ: ilk GET/PUT id=1 satirini olusturur
(project_settings ile ayni kalip).
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0041"
down_revision: Union[str, None] = "0040"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "ftp_settings",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("mode", sa.String(length=10), nullable=False, server_default="gomulu"),
        sa.Column("host", sa.String(length=200), nullable=True),
        sa.Column("port", sa.Integer(), nullable=False, server_default="21"),
        sa.Column("username", sa.String(length=30), nullable=False, server_default="device"),
        sa.Column("password_enc", sa.Text(), nullable=True),
        sa.Column("directory", sa.String(length=200), nullable=False, server_default="/"),
        sa.Column("poll_interval_sec", sa.Integer(), nullable=False, server_default="300"),
        sa.Column("updated_by", sa.String(length=80), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )


def downgrade() -> None:
    op.drop_table("ftp_settings")
