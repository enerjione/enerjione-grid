"""Dahili FTP sunucusunun kimligi harici sunucu alanlarindan AYRILDI.

Tek kimlik seti varken harici mod yapilandirilinca musteri sunucusunun
kullanici/parolasi DAHILI sunucuya da siziyordu: cihazlar ve kullanici bir
anda eski kimlikle giremez oluyordu (sahada 2026-08-05'te yasandi). Mod
degistirmek diger modun kimligine dokunAMAZ hale getirildi.

Veri onarimi: satir su an "gomulu" moddaysa mevcut kimlik/adres dahili
sunucuya AITTIR — yeni kolonlara kopyalanir. "harici" moddaysa mevcut
degerler musteri sunucusuna aittir; dahili taraf varsayilana doner
(device + parola yok -> ftp-server env fallback'i) ve arayuzden yeniden
girilir.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0043"
down_revision: Union[str, None] = "0042"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "ftp_settings",
        sa.Column(
            "embedded_username",
            sa.String(length=30),
            nullable=False,
            server_default="device",
        ),
    )
    op.add_column(
        "ftp_settings", sa.Column("embedded_password_enc", sa.Text(), nullable=True)
    )
    op.add_column(
        "ftp_settings", sa.Column("embedded_host", sa.String(length=200), nullable=True)
    )
    op.execute(
        sa.text(
            "UPDATE ftp_settings SET "
            "embedded_username = username, "
            "embedded_password_enc = password_enc, "
            "embedded_host = host "
            "WHERE mode = 'gomulu'"
        )
    )


def downgrade() -> None:
    op.drop_column("ftp_settings", "embedded_host")
    op.drop_column("ftp_settings", "embedded_password_enc")
    op.drop_column("ftp_settings", "embedded_username")
