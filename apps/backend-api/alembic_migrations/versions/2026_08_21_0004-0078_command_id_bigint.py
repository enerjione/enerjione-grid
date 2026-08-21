"""device_commands.id int4 -> int8 (+ FK) — restore'a dayanikli komut kimligi icin.

NEDEN
-----
Gateway defterinde ve backend'de AYNI tamsayi kimlik (39-42) farkli tarihli,
farkli komutlar icin tekrar kullanildi. Gateway dogru davrandi (defterinde o
kimligi gorup fiziksel islemi TEKRARLAMADI) ama backend yeni komut icin
baska bir teslim jetonu bekledigi icin `token_mismatch` uretti; komut
`failed` oldu.

Kok neden KIMLIK KAYNAGIDIR: `device_commands.id` bir PostgreSQL SERIAL'iydi
ve sequence'in degeri VERITABANININ ICINDE yasiyor. Veritabani daha eski bir
ana alindiginda sequence de o ana doner ve DAGITILMIS kimlikler yeniden
uretilir. Gateway defteri ise baska bir makinede durur ve geri gitmez.

Cozum `command_identity.yeni_kimlik()`: `epoch_ms * 1000 + rastgele(0..999)`.
Bugun ~1.79e15 uretiyor — int4 tavani 2.147.483.647'ye SIGMAZ. Bu migration
o yeri acar.

NEDEN 63-BIT DEGIL
------------------
Arayuz kimligi `number` olarak tasiyor ve JavaScript 2^53 uzerinde tamsayi
hassasiyetini KAYBEDER. Uretici bilerek 2^53 altinda kaliyor; kolon yine de
BIGINT olmali cunku int4 zaten yetmiyor.

MIGRATION 0021 DESENI
---------------------
0021 ayni isi `telemetry` ve `processed_messages` icin yapti ve bir tuzagi
belgeledi: SERIAL'in ARKASINDAKI SEQUENCE'IN KENDI TIPI DE int4'tur; kolonu
genisletmek onu genisletmez. `pg_get_serial_sequence` ile bulunup ayrica
cevrilir. `device_commands` o migration'in kapsaminda DEGILDI.

FK BIRLIKTE GITMELI
-------------------
`device_config_applications.command_id` bu kolona isaret ediyor ve int4.
Ayni surumde genisletilmezse, yeni kimlik tasiyan bir komuta baglanmaya
calisan niyet kaydi "integer out of range" ile patlardi.

VERI KORUNUR
------------
Mevcut satirlarin kimlikleri DEGISMEZ. `ALTER COLUMN TYPE BIGINT` genisletme
yonundedir ve kayipsizdir; eski kucuk kimlikler aynen okunmaya devam eder.

Revision ID: 0078
Revises: 0077
"""

import logging
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0078"
down_revision: Union[str, None] = "0077"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

logger = logging.getLogger("alembic.0078")

#: (tablo, kolon) — genisletilecek yerler. FK ONCE daraltilamaz, o yuzden
#: genisletme sirasi onemsiz; ikisi de ayni islemde.
HEDEFLER = (
    ("device_commands", "id"),
    ("device_config_applications", "command_id"),
)


def _kolon_tipi(bind, tablo: str, kolon: str) -> str | None:
    ins = sa.inspect(bind)
    if not ins.has_table(tablo):
        return None
    for k in ins.get_columns(tablo):
        if k["name"] == kolon:
            return str(k["type"]).lower()
    return None


def upgrade() -> None:
    bind = op.get_bind()
    # SQLite'ta INTEGER PRIMARY KEY ZATEN 64-bit'tir ve `ALTER COLUMN TYPE`
    # desteklenmez. Testler SQLite'ta kosuyor; orada yapilacak bir sey yok.
    if bind.dialect.name != "postgresql":
        logger.info("0078: %s lehcesi — genisletme gerekmiyor", bind.dialect.name)
        return

    for tablo, kolon in HEDEFLER:
        tip = _kolon_tipi(bind, tablo, kolon)
        if tip is None:
            logger.info("0078: %s tablosu yok — atlandi", tablo)
            continue
        if "bigint" in tip:
            logger.info("0078: %s.%s zaten bigint — atlandi", tablo, kolon)
            continue
        op.execute(f"ALTER TABLE {tablo} ALTER COLUMN {kolon} TYPE BIGINT")
        logger.warning("0078: %s.%s int4 -> int8 cevrildi", tablo, kolon)

    # SEQUENCE AYRICA CEVRILIR (0021'in belgeledigi tuzak): SERIAL'in
    # arkasindaki sequence'in kendi veri tipi de int4'tur ve kolonu
    # genisletmek onu genisletmez.
    op.execute(
        """
        DO $$
        DECLARE seq text;
        BEGIN
            seq := pg_get_serial_sequence('device_commands', 'id');
            IF seq IS NOT NULL THEN
                EXECUTE format('ALTER SEQUENCE %s AS bigint', seq);
            END IF;
        END $$;
        """
    )


def downgrade() -> None:
    """int8 -> int4.

    DIKKAT: yeni uretici ~1.79e15 kimlik yaziyor ve bunlar int4'e SIGMAZ.
    Geri alma yalnizca tabloda int4 tavanini asan kimlik YOKKEN guvenlidir;
    varsa PostgreSQL zaten "integer out of range" ile reddeder ve bu DOGRU
    davranistir — sessizce veri kirpmaktansa geri alma basarisiz olmali.
    """
    bind = op.get_bind()
    if bind.dialect.name != "postgresql":
        return
    for tablo, kolon in reversed(HEDEFLER):
        if _kolon_tipi(bind, tablo, kolon) is None:
            continue
        op.execute(f"ALTER TABLE {tablo} ALTER COLUMN {kolon} TYPE INTEGER")
    op.execute(
        """
        DO $$
        DECLARE seq text;
        BEGIN
            seq := pg_get_serial_sequence('device_commands', 'id');
            IF seq IS NOT NULL THEN
                EXECUTE format('ALTER SEQUENCE %s AS integer', seq);
            END IF;
        END $$;
        """
    )
