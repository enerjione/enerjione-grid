"""smtp_from_name

Revision ID: 0056
Revises: 0055
Create Date: 2026-08-12 00:00:02.000000

`notification_settings.smtp_from_name` — gonderen adinin ACIK alani.

NE EKSIKTI
----------
Mektuplarda gorunen ad Proje Ayarlari'ndan TURETILIYORDU:

    site_title -> project_name -> customer_name -> "EnerjiOne Grid"

Sirasi da adlari da sezgiye tersti. Zinciri kazanan alanin arayuzdeki adi
"Tarayici Sekme Basligi"; kullanici oraya tarayici sekmesi icin bir sey
yaziyor, o metin sessizce MUSTERIYE GIDEN mektubun markasi oluyordu. Ekranda
bunu soyleyen hicbir sey yoktu — sahada "mailde neden bu yaziyor" sorusu
tam olarak buradan cikti. Ustelik bu ise ozel olarak konulmus "Proje Adi"
alani, sekme basliginin arkasinda kaliyordu.

CO ZUM
------
Gonderen adi artik mail ayarlarinda kendi alanidir ve DOLUYSA her zaman o
kullanilir. Hem `From` basligindaki gorunen ad hem mektup basligindaki marka
ayni degerden gelir; iki ayri alan birakmak ayni karisikligi yeniden
uretirdi.

GERIYE UYUM
-----------
Varsayilan BOS. Bos oldugunda eski zincir yedek olarak calismaya devam eder,
yani mevcut kurulumlarda gonderen adi bu surumle DEGISMEZ — kullanici alani
doldurana kadar hicbir mektup baska turlu gorunmez.
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0056"
down_revision = "0055"
branch_labels = None
depends_on = None

_TABLO = "notification_settings"
_KOLON = "smtp_from_name"


def upgrade() -> None:
    op.add_column(
        _TABLO,
        sa.Column(_KOLON, sa.String(length=200), nullable=False, server_default=""),
    )


def downgrade() -> None:
    op.drop_column(_TABLO, _KOLON)
