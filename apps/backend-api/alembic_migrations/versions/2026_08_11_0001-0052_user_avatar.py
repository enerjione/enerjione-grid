"""user_avatar

Revision ID: 0052
Revises: 0051
Create Date: 2026-08-11 00:00:01.000000

`users.avatar_url` — profil fotografi (gomulu `data:` URI'si).

NEDEN DOSYA DEPOSU DEGIL
------------------------
Ayri bir dosya deposu su dort seyi beraberinde getirir: kalici bir birim
(volume), nginx'te statik bir yol, yedekleme/geri yukleme adimi ve kullanici
silinince artik dosya temizligi. Sistem on-prem calisiyor ve kullanici sayisi
onlarla olculuyor; istemci goruntuyu 192 pikselde JPEG'e cevirdigi icin tipik
boyut 8-15 KB. Bu haliyle veri kullanicinin satirinda durur, mevcut Postgres
yedegine kendiliginden girer ve hicbir ek altyapi gerektirmez.

Boyut siniri SEMADA DEGIL SERVISTE: `SelfProfileUpdateRequest` ~150 KB uzerini
reddeder. Sema tarafinda `Text` birakildi ki sinir degistiginde migration
gerekmesin.

GERIYE DONUK VERI
-----------------
Kolon NULLABLE ve varsayilansiz. NULL = fotograf yok; arayuz bas harflerden
olusan yuvarlagi (onceki davranis) gostermeye devam eder.
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0052"
down_revision = "0051"
branch_labels = None
depends_on = None


def _kolonlar(bind, tablo: str) -> set[str]:  # noqa: ANN001
    return {c["name"] for c in sa.inspect(bind).get_columns(tablo)}


def upgrade() -> None:
    bind = op.get_bind()
    if "avatar_url" not in _kolonlar(bind, "users"):
        op.add_column("users", sa.Column("avatar_url", sa.Text(), nullable=True))


def downgrade() -> None:
    bind = op.get_bind()
    if "avatar_url" in _kolonlar(bind, "users"):
        op.drop_column("users", "avatar_url")
