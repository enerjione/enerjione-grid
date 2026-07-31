"""widen_hot_table_pk_to_bigint

Revision ID: 0021
Revises: 0020
Create Date: 2026-07-31 00:00:03.000000

`telemetry.id` ve `processed_messages.id` kolonlarini int4 -> int8 yapar.

NEDEN ACIL — SAYAC TASMASI SISTEMI TAMAMEN DURDURUR
----------------------------------------------------
Iki tablo da `Mapped[int] + primary_key=True` ile tanimliydi; PostgreSQL'de
bu SERIAL (int4) uretir. Tavan 2.147.483.647.

600 cihaz olceginde telemetri hacmi ~25.920.000 satir/gun
(600 cihaz x 30 sinyal degisimi/dk x 1440 dk — kaynak: telemetry_retention.py
dosya basligindaki hesap). `processed_messages` islenen HER mesaj icin bir
satir tuttugu icin AYNI hizda buyur.

    2.147.483.647 / 25.920.000 = 82,9 GUN

83. gunde `nextval()` "integer out of range" atar. Sonuc kademeli bir
yavaslama DEGIL, ani ve tam durustur:

  telemetry_consumer._persist_batch batch'i TEK commit ile yazar. INSERT
  patlayinca commit patlar -> hicbir NATS mesaji ack EDILMEZ -> JetStream
  ayni batch'i yeniden teslim eder -> yine patlar. Telemetri alimi tamamen
  durur, cihazlar "kesik" gorunur ve backlog max_ack_pending'e dayanir.

RETENTION BU SORUNU COZMEZ
--------------------------
Yaygin yanlis anlama: "retention tabloyu kucuk tutuyor, o zaman id de
tukenmez." Sequence satir SAYISINI degil, uretilen TOPLAM id adedini sayar;
DELETE sequence'i geri almaz. Tablo hep 26M satirda kalsa bile sayac ayni
hizda ilerler.

MALIYET / SURE
--------------
`ALTER COLUMN ... TYPE bigint` tabloyu ve index'lerini YENIDEN YAZAR ve bu
sirada ACCESS EXCLUSIVE kilidi tutar:

  * `telemetry`: 30 dakikalik retention penceresinde oldugu icin kucuk
    (tipik <1M satir) — saniyeler surer.
  * `processed_messages`: eski 7 gunluk TTL ile ~180M satira ulasmis
    olabilir. Bu yuzden ALTER'DAN ONCE tabloyu yeni TTL penceresine (24 saat)
    kadar BUDUYORUZ; boylece yeniden yazilacak veri ~26M satira iner.
    Budama autocommit blogunda, LIMIT'li turlar halinde yapilir (tek dev
    DELETE'in WAL patlamasi ve uzun kilidi olmasin).

Budama guvenlidir: `processed_messages` yalnizca idempotency defteridir ve
gercek redelivery penceresi ack_wait(60sn) x max_deliver(10) = 10 DAKIKA.
24 saatten eski satirlarin hicbir islevi kalmamistir. Ayrica ikinci bir
dedup katmani daha var: `telemetry_history` dogal anahtarinda
ON CONFLICT DO NOTHING.

MODEL TARAFI
------------
`app/models/telemetry.py` ve `app/models/processed_message.py` artik
`BigInteger` kullaniyor; yeni kurulumlar (Base.metadata.create_all yolu)
dogrudan BIGSERIAL ile gelir ve bu migration onlarda no-op olur.
"""
from __future__ import annotations

import logging
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


logger = logging.getLogger("alembic.runtime.migration")

revision: str = "0021"
down_revision: Union[str, None] = "0020"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


# ALTER oncesi `processed_messages` bu pencereye kadar budanir. config.py'deki
# processed_messages_retention_hours ile ayni mantik; migration runtime
# ayarlarina bagimli olmasin diye burada sabit.
_TRIM_HOURS = 24
_TRIM_BATCH = 50_000
# Tavan: 200 x 50.000 = 10M satir/tur. Daha fazlasi kalirsa ALTER yine dogru
# calisir, sadece daha uzun surer — migration'i sonsuza kadar bekletmeyiz.
_TRIM_MAX_BATCHES = 200


def _column_type(bind, table: str, column: str) -> str | None:
    row = bind.execute(
        sa.text(
            "SELECT data_type FROM information_schema.columns"
            " WHERE table_schema = current_schema()"
            "   AND table_name = :t AND column_name = :c"
        ),
        {"t": table, "c": column},
    ).first()
    return row[0] if row else None


def _table_exists(bind, table: str) -> bool:
    return (
        bind.execute(
            sa.text(
                "SELECT 1 FROM pg_class c"
                " JOIN pg_namespace n ON n.oid = c.relnamespace"
                " WHERE c.relkind IN ('r','p')"
                "   AND n.nspname = current_schema() AND c.relname = :t"
            ),
            {"t": table},
        ).first()
        is not None
    )


def _widen(table: str) -> None:
    """Kolonu ve arkasindaki sequence'i int8'e cevirir. Zaten int8 ise no-op."""
    bind = op.get_bind()
    if not _table_exists(bind, table):
        logger.info("0021: %s tablosu yok — atlandi", table)
        return
    current = _column_type(bind, table, "id")
    if current == "bigint":
        logger.info("0021: %s.id zaten bigint — atlandi", table)
        return

    op.execute(f"ALTER TABLE {table} ALTER COLUMN id TYPE BIGINT")
    # SERIAL'in arkasindaki sequence'in kendi veri tipi de int4'tur; kolonu
    # genisletmek onu genisletmez. pg_get_serial_sequence ile bulup ceviriyoruz.
    # IDENTITY kolonlarda bu fonksiyon da dogru sequence'i doner.
    op.execute(
        f"""
        DO $$
        DECLARE seq text;
        BEGIN
            seq := pg_get_serial_sequence('{table}', 'id');
            IF seq IS NOT NULL THEN
                EXECUTE format('ALTER SEQUENCE %s AS bigint', seq);
            END IF;
        END $$;
        """
    )
    logger.warning("0021: %s.id int4 -> int8 cevrildi", table)


def _trim_processed_messages() -> None:
    """ALTER oncesi eski idempotency satirlarini LIMIT'li turlarla siler.

    autocommit_block: her tur AYRI transaction'da commit edilir. Aksi halde
    tum DELETE'ler migration transaction'inda birikir ve tam da kacinmak
    istedigimiz WAL patlamasini yaratir.
    """
    bind = op.get_bind()
    if not _table_exists(bind, "processed_messages"):
        return
    if _column_type(bind, "processed_messages", "id") == "bigint":
        return  # zaten cevrilmis, budamaya gerek yok

    removed_total = 0
    with op.get_context().autocommit_block():
        conn = op.get_bind()
        for _ in range(_TRIM_MAX_BATCHES):
            result = conn.execute(
                sa.text(
                    "DELETE FROM processed_messages WHERE id IN ("
                    "  SELECT id FROM processed_messages"
                    f"  WHERE processed_at < NOW() - INTERVAL '{_TRIM_HOURS} hours'"
                    "  ORDER BY id ASC LIMIT :batch"
                    ")"
                ),
                {"batch": _TRIM_BATCH},
            )
            removed = int(result.rowcount or 0)
            removed_total += removed
            if removed < _TRIM_BATCH:
                break
    if removed_total:
        logger.warning(
            "0021: processed_messages budandi removed=%d (ALTER'in yeniden "
            "yazacagi veri kuculdu)",
            removed_total,
        )


def upgrade() -> None:
    # Kilit bekleme tavani: cakisan bir islem (pg_dump, operator psql) varsa
    # sonsuza kadar beklemek yerine hata ver. Migration basarisiz olursa
    # container yeniden dener; sonsuz kilit beklemesi ise appliance'i tuglalar.
    op.execute("SET lock_timeout = '30s'")

    _trim_processed_messages()
    _widen("telemetry")
    _widen("processed_messages")


def downgrade() -> None:
    """int8 -> int4'e DONULMEZ.

    Geri donus veri kaybi riskidir: mevcut id degerleri int4 tavanini asmis
    olabilir ve ALTER o satirlarda patlar. Ustelik geri donmek tam da
    duzeltilen arizayi (83 gunde durma) geri getirir. Zincir butunlugu icin
    fonksiyon duruyor ama bilincli olarak bos.
    """
    pass
