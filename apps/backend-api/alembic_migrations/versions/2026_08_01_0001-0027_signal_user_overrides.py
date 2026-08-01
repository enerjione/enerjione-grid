"""signal_user_overrides

Revision ID: 0027
Revises: 0026
Create Date: 2026-08-01 00:00:01.000000

`signal_catalog.user_overrides` — operatorun ELLE degistirdigi alanlarin adlari.

NEDEN GEREKLI
-------------
Backend her acilista `seed_default_signals(strict=True)` kosuyordu ve
guncelledigi `_MUTABLE_FIELDS` listesi tam da kurulumcunun arayuzden
degistirdigi alanlari iceriyor:

    label, unit, scale, offset, dnp3_index,
    iec104_type_id, iec104_ioa, iec104_ioa_offset

Yani `PATCH /signals/{key}` "kaydedildi" diyor, `signal_updated` denetim
kaydi yaziliyor, sonra ILK YENIDEN BASLATMADA degisiklik sessizce geri
aliniyordu.

    Devreye alma muhendisi SCADA icin 20 sinyalin IOA'sini duzenler ve akim
    trafosu icin scale=0.1 yapar. Gece elektrik kesintisi olur. Sabah SCADA
    YANLIS IOA'dan okur ve akim degerleri 10 KAT yanlis gorunur. Hicbir hata
    logu, hicbir alarm yok.

Seed JSON'unda alan NULL ise sonuc daha da agir: `iec104_ioa` NULL'a cekilen
sinyal IEC 104 yayinindan TAMAMEN duser.

Ayrica `strict=True` seed listesinde olmayan HER sinyali siliyordu — ama
`POST /signals` kurulumcuya sinyal yaratma izni veriyor. Acilistaki senkron
artik `strict=False`; fabrikaya donus icin ayri ve bilincli bir uc var
(`POST /signals/reset-to-defaults`), o uc bu kolonu temizler.

VERI TASIMA YOK
---------------
Kolon NULL baslar: mevcut kayitlar "hic elle degistirilmemis" sayilir. Bu
bilincli — hangi alanin gecmiste elle degistirildigini geriye donuk bilmenin
yolu yok. Kurulumcunun bu migration'dan SONRA yaptigi her duzenleme
isaretlenir ve korunur.
"""
from __future__ import annotations

import logging
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

logger = logging.getLogger("alembic.runtime.migration")

revision: str = "0027"
down_revision: Union[str, None] = "0026"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

TABLE = "signal_catalog"
COLUMN = "user_overrides"


def _has_column(bind, table: str, column: str) -> bool:
    return (
        bind.execute(
            sa.text(
                "SELECT 1 FROM information_schema.columns"
                " WHERE table_schema = current_schema()"
                "   AND table_name = :t AND column_name = :c"
            ),
            {"t": table, "c": column},
        ).first()
        is not None
    )


def upgrade() -> None:
    bind = op.get_bind()
    if _has_column(bind, TABLE, COLUMN):
        logger.info("0027: %s.%s zaten var — atlandi", TABLE, COLUMN)
        return
    op.add_column(TABLE, sa.Column(COLUMN, sa.JSON(), nullable=True))


def downgrade() -> None:
    bind = op.get_bind()
    if not _has_column(bind, TABLE, COLUMN):
        return
    # DIKKAT: bu kolonu dusurmek "hangi alanlar elle degistirildi" bilgisini
    # KALICI OLARAK kaybeder ve bir sonraki acilista seed o alanlari yeniden
    # fabrika degerine cevirir.
    op.drop_column(TABLE, COLUMN)
