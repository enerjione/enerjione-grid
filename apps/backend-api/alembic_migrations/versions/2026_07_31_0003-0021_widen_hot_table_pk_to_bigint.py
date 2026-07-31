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
    olabilir. Bu yuzden ALTER'DAN ONCE tablo BOSALTILIR (TRUNCATE) ve ALTER
    bos tabloda kosar; sonra son 1 saatin satirlari geri yazilir. Ayrintili
    gerekce icin asagidaki "SAHA CIHAZINI ACILAMAZ HALE GETIREBILIYORDU"
    bolumune bakin.

Bosaltma guvenlidir: `processed_messages` yalnizca idempotency defteridir ve
gercek redelivery penceresi ack_wait(60sn) x max_deliver(10) = 10 DAKIKA.
Geri yazilan 1 saat, o pencerenin 6 katidir. Ayrica ikinci bir dedup katmani
daha var: `telemetry_history` dogal anahtarinda ON CONFLICT DO NOTHING.

MODEL TARAFI
------------
`app/models/telemetry.py` ve `app/models/processed_message.py` artik
`BigInteger` kullaniyor; yeni kurulumlar (Base.metadata.create_all yolu)
dogrudan BIGSERIAL ile gelir ve bu migration onlarda no-op olur.

BU MIGRATION SAHA CIHAZINI ACILAMAZ HALE GETIREBILIYORDU
---------------------------------------------------------
Ilk hali `processed_messages`'i ALTER'dan once BUDUYORDU ama budama
10M satirla (200 tur x 50.000) TAVANLIYDI. Eski 7 gunluk TTL ile ~180M
satira ulasmis bir tabloda bu, ~170M satir kalmasi demekti:

  (a) Budamanin urettigi olu satirlar autovacuum'u tetikler; autovacuum'un
      ShareUpdateExclusiveLock'i ALTER'in ACCESS EXCLUSIVE talebiyle
      catisir -> 30 sn'de lock_timeout -> exception -> `migrate_db` patlar
      -> backend CRASH-LOOP. Autovacuum saatlerce surebilir ve her yeniden
      baslatma ayni 10M'lik budamayi bastan yapar.
  (b) Kilit alinsa bile ~170M satirlik tablo+UNIQUE index yeniden yazimi
      diskte tablonun ~2 kati bos alan ister. Bu dalin varlik sebebi zaten
      diskin dolmasiysa ENOSPC ile patlar; yine boot engellenir.

COZUM: DELETE DEGIL, TRUNCATE + GERI YAZMA
-------------------------------------------
Budamayi "tavansiz DELETE dongusu" yapmak yetmezdi: 170M satirlik batch'li
DELETE hem saatler surer, hem WAL uretir, hem de olu satirlar VACUUM'a kadar
diskte kalir — yani (b) daha da kotulesir.

Bunun yerine tabloyu SIFIRLIYORUZ ve yalnizca son 1 SAATI geri yaziyoruz:

    CREATE TEMP TABLE _pm_keep AS SELECT * ... WHERE processed_at >= now()-1h
    TRUNCATE processed_messages          -- O(1), diski ANINDA geri verir
    ALTER ... TYPE BIGINT                -- artik BOS tabloda: aninda
    INSERT INTO processed_messages SELECT * FROM _pm_keep

Neden guvenli: `processed_messages` yalnizca idempotency defteridir ve
gercek redelivery penceresi ack_wait(60sn) x max_deliver(10) = 10 DAKIKA.
1 saat, o pencerenin 6 katidir. Ustelik ikinci bir dedup katmani daha var:
`telemetry_history` dogal anahtarinda ON CONFLICT DO NOTHING.

Bu sirayla (a) da cozulur: DELETE olmadigi icin olu satir yigini ve onun
tetikledigi autovacuum firtinasi hic olusmaz.

KILIT CAKISMASI: YUTMA, TEKRAR DENE
------------------------------------
Worker container'lari (tag-engine, alarm-service) backend ile PARALEL
basladigi icin ALTER/TRUNCATE aninda tabloya dokunuyor olabilirler. Bu
gecici bir durum; `_with_lock_retry` savepoint icinde birkac kez tekrar
dener. Savepoint SART — bkz. 0019/0023: yakalanan bir hata savepoint'siz
transaction'i "aborted" birakir ve sonraki HER ifade (alembic'in kendi
`UPDATE alembic_version`'i dahil) duser.

Tum denemeler tukenirse hata BILEREK firlatilir, yutulmaz:
alembic basarili bir upgrade'i DAMGALAR ve bir daha kosmaz. Yutsaydik
sayac genisletme HIC yapilmayacak, cihaz ~83 gun sorunsuz calisip sonra
telemetri alimini TAMAMEN durduracakti — sessizce. Tablo artik TRUNCATE
sayesinde bos oldugu icin yeniden deneme ucuz ve kendi kendini cozer.

NOT — `shutil.disk_usage` ILE BOS ALAN KONTROLU YAPILMADI: bu kod
backend container'inda kosuyor, veritabani AYRI bir container'da. Backend'in
gordugu dosya sistemi postgres-data volume'u DEGILDIR; oradan okunan "bos
alan" yaniltici olur. Dogru koruma, yeniden yazilacak veriyi kucultmektir —
yukarida yapilan tam olarak budur.
"""
from __future__ import annotations

import logging
import time
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


logger = logging.getLogger("alembic.runtime.migration")

revision: str = "0021"
down_revision: Union[str, None] = "0020"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


# TRUNCATE sonrasi geri yazilacak pencere. Gercek redelivery penceresi
# ack_wait(60sn) x max_deliver(10) = 10 DAKIKA; 1 saat onun 6 kati.
# Daha genis tutmak ALTER'in yeniden yazacagi veriyi buyutur ve tam da
# kacinmaya calistigimiz seyi geri getirir.
_KEEP_INTERVAL = "1 hour"

# Kilit cakismasi gecicidir (paralel baslayan worker container'lari). Toplam
# bekleme ~6 x (30 sn lock_timeout + 10 sn uyku) = en kotu ~4 dakika.
_LOCK_RETRY_ATTEMPTS = 6
_LOCK_RETRY_SLEEP_SEC = 10.0

# PostgreSQL: lock_timeout asilinca SQLSTATE 55P03 (lock_not_available).
# statement_timeout'un 57014'u ile karistirma; onu tekrar denemek istemeyiz.
_SQLSTATE_LOCK_NOT_AVAILABLE = "55P03"


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


def _is_lock_timeout(exc: BaseException) -> bool:
    """Hata "kilit alinamadi" mi, yoksa gercek bir sema/veri hatasi mi?

    Yalnizca ilkini tekrar denemek istiyoruz; bozuk bir ALTER'i alti kez
    denemek sadece boot'u geciktirir.
    """
    orig = getattr(exc, "orig", None)
    if getattr(orig, "pgcode", None) == _SQLSTATE_LOCK_NOT_AVAILABLE:
        return True
    return "lock timeout" in str(exc).lower()


def _with_lock_retry(label: str, fn) -> None:
    """Adimi SAVEPOINT icinde kosturur; kilit cakismasinda tekrar dener.

    SAVEPOINT SART: basarisiz bir ifade transaction'i "aborted" birakir ve
    sonraki HER ifade `InFailedSqlTransaction` ile duser — alembic'in kendi
    `UPDATE alembic_version` ifadesi dahil (bkz. 0019/0023 ayni tuzak).
    Savepoint olmadan "tekrar deneme" fikri ilk denemede olurdu.

    Denemeler tukenirse hata FIRLATILIR — bkz. dosya basligi: yutmak,
    sayac genisletmesinin hic yapilmamasi ve ~83 gun sonra telemetri
    aliminin sessizce durmasi demek.
    """
    bind = op.get_bind()
    for attempt in range(1, _LOCK_RETRY_ATTEMPTS + 1):
        try:
            with bind.begin_nested():
                fn()
            return
        except Exception as exc:  # noqa: BLE001
            if attempt >= _LOCK_RETRY_ATTEMPTS or not _is_lock_timeout(exc):
                raise
            logger.warning(
                "0021: %s kilit bekleyemedi (deneme %d/%d) — %.0f sn sonra "
                "tekrar. Paralel baslayan bir worker tabloyu kullaniyor "
                "olabilir.",
                label,
                attempt,
                _LOCK_RETRY_ATTEMPTS,
                _LOCK_RETRY_SLEEP_SEC,
            )
            time.sleep(_LOCK_RETRY_SLEEP_SEC)


def _widen_sql(table: str) -> None:
    """Kolonu ve arkasindaki sequence'i int8'e ceviren ham SQL."""
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


def _widen(table: str) -> None:
    """Kucuk tablolar icin: dogrudan genislet (kilit tekrar denemeli)."""
    bind = op.get_bind()
    if not _table_exists(bind, table):
        logger.info("0021: %s tablosu yok — atlandi", table)
        return
    if _column_type(bind, table, "id") == "bigint":
        logger.info("0021: %s.id zaten bigint — atlandi", table)
        return
    _with_lock_retry(f"{table} genisletme", lambda: _widen_sql(table))


def _shrink_and_widen_processed_messages() -> None:
    """`processed_messages`'i BOSALTIP genisletir, sonra son 1 saati geri yazar.

    Sira onemli: ALTER bos tabloda kosarsa yeniden yazacak veri kalmaz.

        1) son 1 saatin satirlari gecici tabloya alinir
        2) TRUNCATE — O(1), diski ANINDA geri verir (DELETE vermez)
        3) ALTER ... TYPE BIGINT — bos tabloda aninda
        4) gecici tablodan geri yazilir

    Neden DELETE degil: ~180M satirlik bir tabloda batch'li DELETE saatler
    surer, WAL'i sisirir ve olu satirlar VACUUM'a kadar diskte kalir; ustelik
    tetikledigi autovacuum ALTER'in ACCESS EXCLUSIVE kilidiyle catisip
    migration'i dusurur (bkz. dosya basligi).

    Tumu TEK savepoint icinde: adim 2 ile 4 arasinda hata olursa tablo yarim
    bosalmis halde kalmaz, eski haline doner.
    """
    bind = op.get_bind()
    if not _table_exists(bind, "processed_messages"):
        logger.info("0021: processed_messages tablosu yok — atlandi")
        return
    if _column_type(bind, "processed_messages", "id") == "bigint":
        logger.info("0021: processed_messages.id zaten bigint — atlandi")
        return

    before = bind.execute(
        sa.text("SELECT count(*) FROM processed_messages")
    ).scalar_one()

    def _step() -> None:
        # ON COMMIT DROP: migration transaction'i bittiginde gecici tablo
        # kendiliginden gider; savepoint'e donulurse zaten hic olusmamis olur.
        op.execute(
            "CREATE TEMP TABLE _pm_keep ON COMMIT DROP AS"
            " SELECT * FROM processed_messages"
            f" WHERE processed_at >= NOW() - INTERVAL '{_KEEP_INTERVAL}'"
        )
        op.execute("TRUNCATE TABLE processed_messages")
        _widen_sql("processed_messages")
        op.execute("INSERT INTO processed_messages SELECT * FROM _pm_keep")

    _with_lock_retry("processed_messages bosalt+genislet", _step)

    after = bind.execute(
        sa.text("SELECT count(*) FROM processed_messages")
    ).scalar_one()
    logger.warning(
        "0021: processed_messages %d -> %d satir (son %s korundu; gercek "
        "redelivery penceresi 10 dakika)",
        before,
        after,
        _KEEP_INTERVAL,
    )


def upgrade() -> None:
    # Kilit bekleme tavani: cakisan bir islem (paralel baslayan worker,
    # pg_dump, operator psql) varsa sonsuza kadar beklemek yerine hata ver.
    # Hata `_with_lock_retry` tarafindan yakalanip tekrar denenir.
    #
    # `SET` (SET LOCAL degil) transaction sonuna kadar gecerlidir ve
    # savepoint'lerden ONCE calistigi icin geri alinmaz.
    op.execute("SET lock_timeout = '30s'")

    # telemetry: 30 dakikalik pencerede oldugu icin kucuk — dogrudan genislet.
    _widen("telemetry")
    # processed_messages: buyuyebilen tablo — once bosalt, sonra genislet.
    _shrink_and_widen_processed_messages()


def downgrade() -> None:
    """int8 -> int4'e DONULMEZ.

    Geri donus veri kaybi riskidir: mevcut id degerleri int4 tavanini asmis
    olabilir ve ALTER o satirlarda patlar. Ustelik geri donmek tam da
    duzeltilen arizayi (83 gunde durma) geri getirir. Zincir butunlugu icin
    fonksiyon duruyor ama bilincli olarak bos.
    """
    pass
