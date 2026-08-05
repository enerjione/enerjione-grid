"""FTP varsayilan dizini Smart Navigator 2.0 standardina cekildi.

Cihazin FTP ekranindaki fabrika degeri /SN20/FOTA/ — bizim varsayilanimiz
"/" idi ve iki taraf farkli dizine bakinca "komut gitti, bir sey olmadi"
uretiyordu. Yalnizca hala eski varsayilanda ("/") duran satir guncellenir;
kullanicinin bilerek sectigi farkli bir dizine DOKUNULMAZ.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0042"
down_revision: Union[str, None] = "0041"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_STANDART = "/SN20/FOTA/"


def upgrade() -> None:
    op.alter_column(
        "ftp_settings",
        "directory",
        server_default=_STANDART,
        existing_type=sa.String(length=200),
        existing_nullable=False,
    )
    op.execute(
        sa.text("UPDATE ftp_settings SET directory = :yeni WHERE directory = '/'")
        .bindparams(yeni=_STANDART)
    )


def downgrade() -> None:
    op.alter_column(
        "ftp_settings",
        "directory",
        server_default="/",
        existing_type=sa.String(length=200),
        existing_nullable=False,
    )
