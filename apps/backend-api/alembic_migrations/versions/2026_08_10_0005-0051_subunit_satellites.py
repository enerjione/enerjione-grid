"""subunit_satellites

Revision ID: 0051
Revises: 0050
Create Date: 2026-08-10 00:00:05.000000

`devices.subunit_satellites` — Pole Master Kit setinin GERCEK uydu atamasi.

NE EKSIKTI
----------
Setin hangi uyduları tasidigi `subunit_index`ten TURETILIYORDU:
set 1 -> 1/2/3, set 2 -> 4/5/6, set 3 -> 7/8/9.

Bu en yaygin kurulum ama bir kural degil: uyduları kelepceyi takan kisi
baglar ve sira kite gore degil DIREGE gore olusur. Ikinci sete 4/5/6 yerine
2/7/9 baglanmis bir sahada sabit turetme telemetriyi YANLIS setlere yazardi
ve bu hicbir hata uretmezdi — kullanici yalnizca "bu setin akimi tuhaf"
derdi. Uydu -> faz eslemesi de ayni sekilde yanlis birikirdi.

GERIYE DONUK VERI
-----------------
Kolon NULLABLE ve varsayilansiz. NULL = "varsayilani kullan"; mevcut setler
aynen calismaya devam eder (`resolve_subunit_satellites` NULL'da set
sirasindan turetir). Buraya toplu deger YAZILMAZ: kurulumcunun onaylamadigi
bir atamayi "secilmis" gostermek olurdu — yeni olusturulan setler atamayi
acikca yazar, eskiler varsayilanda kalir.

BIJEKTIFLIK VERITABANINDA ZORLANMAZ
-----------------------------------
"Bir uydu yalnizca tek sete" kurali kit KAPSAMINDA anlamli ve JSON dizisi
uzerinde tekil indeksle ifade edilemez; dogrulama servis katmaninda
(`device_kit_service.normalize_satellites`). Sema duzeyinde zorlamak icin
uydu basina ayri satir tutan bir yan tablo gerekirdi — uc elemanli sabit bir
liste icin fazla agir.
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0051"
down_revision = "0050"
branch_labels = None
depends_on = None


def _kolonlar(bind, tablo: str) -> set[str]:  # noqa: ANN001
    return {c["name"] for c in sa.inspect(bind).get_columns(tablo)}


def upgrade() -> None:
    bind = op.get_bind()
    if "subunit_satellites" not in _kolonlar(bind, "devices"):
        op.add_column("devices", sa.Column("subunit_satellites", sa.JSON(), nullable=True))


def downgrade() -> None:
    bind = op.get_bind()
    if "subunit_satellites" in _kolonlar(bind, "devices"):
        op.drop_column("devices", "subunit_satellites")
