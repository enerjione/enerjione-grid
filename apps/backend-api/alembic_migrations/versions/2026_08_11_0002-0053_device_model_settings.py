"""device_model_settings

Revision ID: 0053
Revises: 0052
Create Date: 2026-08-11 00:00:02.000000

`device_model_settings` — cihaz MODELINE ozel ayarlar (cihaz profili).

NE EKSIKTI
----------
Batarya esikleri (`project_settings.battery_voltage_low/full`) proje
genelinde TEK bir cifttir ve TUM cihazlara uygulanir. Kurulumda tek model
varken bu dogruydu; Pole Master Kit eklendikten sonra degil — SN 2.0'in
lityum hucresi ile kitin bataryasi ayni voltaj araliginda calismaz.

Tek esikle iki model birlikte olculunce sonuc SESSIZ bir yanlislik olur:
bir model surekli "dolu", digeri surekli "bitmek uzere" gorunur. Ne hata
kaydi ne uyari cikar — yalnizca batarya yuzdesi yanlis olur ve saha ekibi
ya gereksiz yere direge cikar ya da gercekten biten bataryayi kacirir.

GERIYE DONUK VERI
-----------------
Tablo BOS olusturulur ve satir yazilmasi zorunlu degildir. Kayit yoksa
cozum zinciri bir ust katmana duser:

    model ayari  ->  proje ayari  ->  kod varsayilani (3.40 / 3.71 V)

Yani mevcut kurulumlar bu migration'dan sonra BUGUNKU davranisla aynen
calisir; degisiklik yalnizca bir model icin satir yazildiginda baslar.
Buraya toplu varsayilan YAZILMAZ: uretici degerini bilmedigimiz bir modeli
"ayarlanmis" gostermek, ayarlanmamis olmasindan daha kotudur.

MODEL KODU ICIN FK YOK
----------------------
Model listesi kismen kodda (`BUILTIN_MODELS`) kismen sinyal katalogunda
yasar; tek bir tabloya FK verilemez. Yetim satir riski kabul edilir —
karsiligi olmayan bir model kodu yalnizca okunmaz, hicbir seyi bozmaz.
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0053"
down_revision = "0052"
branch_labels = None
depends_on = None

_TABLO = "device_model_settings"


def upgrade() -> None:
    bind = op.get_bind()
    if _TABLO in sa.inspect(bind).get_table_names():
        return
    op.create_table(
        _TABLO,
        sa.Column("model", sa.String(length=80), primary_key=True),
        sa.Column("battery_voltage_low", sa.Float(), nullable=True),
        sa.Column("battery_voltage_full", sa.Float(), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    bind = op.get_bind()
    if _TABLO in sa.inspect(bind).get_table_names():
        op.drop_table(_TABLO)
