"""project_settings_toast

Revision ID: 0038
Revises: 0037
Create Date: 2026-08-03

NE EKLENIYOR
------------
`project_settings` tablosuna iki kurulum geneli UI tercihi:

  toast_position — toast bildiriminin ekrandaki kosesi. NULL ise
                   "bottom-right", yani BUGUNKU davranis.
  toast_muted    — kendiliginden gelen (alarm) toast bildirimlerini sustur.
                   NULL/FALSE ise bildirimler gorunur, yani BUGUNKU davranis.

VARSAYILANLAR MEVCUT DAVRANISI AYNEN KORUR
------------------------------------------
Iki kolon da NULLABLE ve server_default YOK. Guncelleyen bir sahada satir
zaten var; yeni kolonlar NULL doner, frontend NULL'u "bottom-right" +
"susturma kapali" olarak okur. Guncellemeden sonra hicbir kullanici hicbir
fark gormez — ayar bilincli olarak degistirilene kadar.

KOLON ZATEN VARSA ATLA — KURULUM BU YUZDEN KILITLENIYORDU
---------------------------------------------------------
`app/main.py` acilista hala `Base.metadata.create_all(bind=engine)` cagiriyor
ve alembic ONDAN SONRA kosuyor. Temiz bir veritabaninda bu kolonlar guncel
modelden zaten olusur; kosulsuz bir `op.add_column` ardindan

    psycopg2.errors.DuplicateColumn: column "toast_position"
    of relation "project_settings" already exists

ile patlar. Backend acilamaz -> healthcheck duser -> kurulum "backend-api is
unhealthy" diyerek durur ve cihaz KALICI kilitlenir; her yeniden deneme ayni
noktada patlar. 0025 tam olarak bu arizayi yasadi ve ayni inspector guard'ini
kullaniyor. Migration'in gorevi adimi degil SONUCU garanti etmektir.

MALIYET
-------
Iki nullable kolon, sabit boyutlu bir tabloya (tek satir). server_default
olmadigi icin tablo yeniden yazilmaz; islem metadata-only.
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0038"
down_revision: Union[str, None] = "0037"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_TABLO = "project_settings"


def _kolonlar() -> set[str]:
    return {s["name"] for s in sa.inspect(op.get_bind()).get_columns(_TABLO)}


def upgrade() -> None:
    # Kilit bekleme tavani: ADD COLUMN kisa sureli ACCESS EXCLUSIVE alir;
    # cakisan bir islem varsa sonsuza kadar beklemesin.
    op.execute("SET lock_timeout = '30s'")
    mevcut = _kolonlar()
    if "toast_position" not in mevcut:
        op.add_column(
            _TABLO,
            sa.Column("toast_position", sa.String(length=20), nullable=True),
        )
    if "toast_muted" not in mevcut:
        op.add_column(
            _TABLO,
            sa.Column("toast_muted", sa.Boolean(), nullable=True),
        )


def downgrade() -> None:
    # Geri alma da dayanikli: kolon yoksa hata vermek yerine atla.
    mevcut = _kolonlar()
    if "toast_muted" in mevcut:
        op.drop_column(_TABLO, "toast_muted")
    if "toast_position" in mevcut:
        op.drop_column(_TABLO, "toast_position")
