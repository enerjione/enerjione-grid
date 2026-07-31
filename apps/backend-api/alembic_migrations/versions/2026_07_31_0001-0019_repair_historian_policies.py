"""repair_historian_policies

Revision ID: 0019
Revises: 0018
Create Date: 2026-07-31 00:00:01.000000

`telemetry_history` hypertable + retention/compression politikalarini ONARIR.

NEDEN GEREKLI (0007 zaten yapiyordu, ne oldu?)
-----------------------------------------------
0007 dogru yazilmis ama BIR KEZ kosar. Icinde su dal var:

    if not _timescaledb_available(bind):
        return          # duz tablo birak, hypertable/policy adimlarini atla

Bu dal alindiginda alembic yine de 0007'yi "uygulandi" olarak DAMGALAR. Sonra
kurulum TimescaleDB imajina gecse bile alembic 0007'yi TEKRAR KOSMAZ — yani:

    * `telemetry_history` DUZ TABLO olarak kalir,
    * 90 gunluk retention politikasi HIC KURULMAZ,
    * ve bu tamamen SESSIZDIR.

600 cihaz x ~193 sinyal olceginde bu tablo gunde ~26M satir buyur. Retention
yoksa disk dolana kadar kimse fark etmez; dolunca da once Postgres yazamaz
hale gelir ve telemetri akisi durur.

Bu duruma dusme yollari:
  1. Eski bir saha `postgres:16` imajiyla kuruldu (compose yorumu bunu
     "uyumlu" diye isaretliyor), sonra timescale imajina gecildi.
  2. Ilk boot'ta extension henuz kullanilabilir degildi
     (shared_preload_libraries yazilmamis bir volume).

BU MIGRATION IDEMPOTENT VE TEKRAR EDILEBILIR
---------------------------------------------
Her adim "zaten varsa dokunma" mantigiyla yazildi; ayni sistemde tekrar
kosmasi zararsizdir. Boylece ileride benzer bir durumda yalnizca
`down_revision` zincirine yeni bir revision eklemek yeterli olur.

HATA POLITIKASI — BILINCLI OLARAK "EN IYI CABA"
-----------------------------------------------
Timescale adimlarinin her biri try/except ile sariliyor ve hata YUTULUYOR
(uyari log'u ile). Sebep: bu migration container CMD'sinde
`python -m scripts.migrate_db` ile boot'tan ONCE kosuyor. Burada exception
firlatmak backend'in HIC AYAGA KALKMAMASI demek — yani saha karariyor.

"Sessiz basarisizlik" riskini kapatan sey bu migration degil, yanindaki
GORUNURLUK: `app/services/historian_service.py` durumu raporluyor ve Sistem
Durumu sayfasi retention yoksa uyari gosteriyor. Yani onarim en-iyi-caba,
tespit ise garanti.

NOT: Bu dosyadaki hypertable/policy SQL'leri TimescaleDB gerektirdigi icin
yerel dogrulamada (vanilla postgres) yalnizca "extension yok" dali
kosturulabildi. Timescale dallari 0007 ile AYNI cagrilari kullaniyor
(create_hypertable / add_retention_policy / add_compression_policy), yani
sahada calistigi bilinen ifadeler.
"""
from __future__ import annotations

import logging
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

logger = logging.getLogger("alembic.runtime.migration")

revision: str = "0019"
down_revision: Union[str, None] = "0018"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


TABLE = "telemetry_history"
RETENTION_DAYS = 90
COMPRESS_AFTER_DAYS = 7


def _timescaledb_available(bind) -> bool:
    row = bind.execute(
        sa.text("SELECT 1 FROM pg_available_extensions WHERE name = 'timescaledb'")
    ).first()
    return row is not None


def _timescaledb_installed(bind) -> bool:
    row = bind.execute(
        sa.text("SELECT 1 FROM pg_extension WHERE extname = 'timescaledb'")
    ).first()
    return row is not None


def _is_hypertable(bind) -> bool:
    """`telemetry_history` hypertable mi?

    `timescaledb_information.hypertables` TimescaleDB 2.x boyunca kararli bir
    goruntudur. Extension kurulu degilse sorgu patlar; caller once
    `_timescaledb_installed` kontrol ediyor.
    """
    try:
        row = bind.execute(
            sa.text(
                "SELECT 1 FROM timescaledb_information.hypertables"
                " WHERE hypertable_name = :t"
            ),
            {"t": TABLE},
        ).first()
        return row is not None
    except Exception:  # noqa: BLE001
        return False


def _has_job(bind, proc_name: str) -> bool:
    """Bu tablo icin verilen tipte bir arka plan job'u var mi?

    proc_name ornekleri: 'policy_retention', 'policy_compression'.
    """
    try:
        row = bind.execute(
            sa.text(
                "SELECT 1 FROM timescaledb_information.jobs"
                " WHERE proc_name = :p AND hypertable_name = :t"
            ),
            {"p": proc_name, "t": TABLE},
        ).first()
        return row is not None
    except Exception:  # noqa: BLE001
        return False


def _try(label: str, fn) -> bool:
    """Adimi SAVEPOINT icinde kosturur; hata firlatirsa YUTAR ve uyari loglar.

    Bkz. dosya basindaki "HATA POLITIKASI": burada raise etmek backend'in
    boot etmemesi demek.

    SAVEPOINT NEDEN SART
    --------------------
    Hatayi yutmak TEK BASINA YETMIYORDU. PostgreSQL'de bir ifade patlayinca
    transaction "aborted" duruma gecer ve o transaction'daki SONRAKI HER ifade
    `InFailedSqlTransaction` ile duser — alembic'in kendi
    `UPDATE alembic_version` ifadesi DAHIL. env.py tum migration'lari tek
    transaction'da kosturdugu icin sonuc sudur:

        CREATE EXTENSION timescaledb patlar (postgres:16'dan devralinmis
        volume'de shared_preload_libraries yok)
        -> `_try` hatayi yutar ve "atlandi" der
        -> ama transaction ARTIK BOZUK
        -> 0020-0023 ve alembic_version guncellemesi de patlar
        -> `scripts.migrate_db` exception firlatir
        -> backend container CMD basarisiz -> SONSUZ CRASH-LOOP

    Yani "en iyi caba" politikasi, tam da onarmak icin yazildigi sahalari
    tugluyordu. `begin_nested()` bir SAVEPOINT acar; adim patlarsa yalnizca o
    savepoint'e geri donulur ve dis transaction SAGLAM kalir.
    """
    bind = op.get_bind()
    try:
        with bind.begin_nested():
            fn()
        return True
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "0019 historian onarim adimi basarisiz (atlaniyor): %s -> %s",
            label,
            exc,
        )
        return False


def upgrade() -> None:
    bind = op.get_bind()

    # Tablo hic yoksa yapacak bir sey yok (0007 onu yaratmis olmali).
    exists = bind.execute(
        sa.text("SELECT 1 FROM information_schema.tables WHERE table_name = :t"),
        {"t": TABLE},
    ).first()
    if exists is None:
        logger.warning("0019: %s tablosu yok — onarim atlandi", TABLE)
        return

    if not _timescaledb_available(bind):
        # Vanilla postgres (dev). Hicbir sey yapamayiz; historian_service bu
        # durumu "timescaledb_unavailable" olarak raporlayacak.
        logger.info("0019: timescaledb yok — historian duz tablo olarak kaliyor")
        return

    if not _timescaledb_installed(bind):
        if not _try("CREATE EXTENSION timescaledb", lambda: op.execute(
            "CREATE EXTENSION IF NOT EXISTS timescaledb"
        )):
            return

    # 1) Hypertable'a cevir (0007 atlamis olabilir).
    if not _is_hypertable(bind):
        logger.warning(
            "0019: %s hypertable DEGIL (0007 timescaledb'siz kosmus olmali) — "
            "cevriliyor",
            TABLE,
        )
        # migrate_data=TRUE: duz tabloda birikmis satirlar chunk'lara tasinir.
        # Buyuk tabloda uzun surebilir ama bir kez olur.
        if not _try("create_hypertable", lambda: op.execute(
            f"SELECT create_hypertable('{TABLE}', 'source_timestamp',"
            " chunk_time_interval => INTERVAL '1 day',"
            " migrate_data => TRUE, if_not_exists => TRUE)"
        )):
            # Hypertable olmadan policy eklenemez; burada birakiyoruz.
            return
    else:
        logger.info("0019: %s zaten hypertable", TABLE)

    # 2) Retention politikasi — ASIL MESELE. Yoksa disk dolar.
    if not _has_job(bind, "policy_retention"):
        logger.warning("0019: retention politikasi YOK — %d gun ekleniyor", RETENTION_DAYS)
        _try("add_retention_policy", lambda: op.execute(
            f"SELECT add_retention_policy('{TABLE}',"
            f" INTERVAL '{RETENTION_DAYS} days', if_not_exists => TRUE)"
        ))
    else:
        logger.info("0019: retention politikasi mevcut")

    # 3) Compression. Once tablo ayarlari, sonra policy.
    if not _has_job(bind, "policy_compression"):
        _try("ALTER TABLE ... compress", lambda: op.execute(
            f"ALTER TABLE {TABLE} SET ("
            " timescaledb.compress,"
            " timescaledb.compress_segmentby = 'device_id, signal_key',"
            " timescaledb.compress_orderby = 'source_timestamp DESC')"
        ))
        _try("add_compression_policy", lambda: op.execute(
            f"SELECT add_compression_policy('{TABLE}',"
            f" INTERVAL '{COMPRESS_AFTER_DAYS} days', if_not_exists => TRUE)"
        ))

    # 4) Continuous aggregate'ler (grafik/analiz kaynagi). CREATE ... IF NOT
    #    EXISTS oldugu icin varsa no-op; policy'leri de if_not_exists.
    for view, bucket, start_off, sched in (
        ("telemetry_history_1m", "1 minute", "3 hours", "1 minute"),
        ("telemetry_history_1h", "1 hour", "3 days", "1 hour"),
    ):
        _try(f"CREATE MATERIALIZED VIEW {view}", lambda v=view, b=bucket: op.execute(
            f"CREATE MATERIALIZED VIEW IF NOT EXISTS {v}"
            " WITH (timescaledb.continuous) AS"
            " SELECT device_id, signal_key,"
            f"        time_bucket(INTERVAL '{b}', source_timestamp) AS bucket,"
            "        avg(value) AS avg_value,"
            "        min(value) AS min_value,"
            "        max(value) AS max_value,"
            "        count(*)   AS sample_count"
            f" FROM {TABLE}"
            " GROUP BY device_id, signal_key, bucket"
            " WITH NO DATA"
        ))
        _try(
            f"add_continuous_aggregate_policy {view}",
            lambda v=view, s=start_off, sc=sched, b=bucket: op.execute(
                f"SELECT add_continuous_aggregate_policy('{v}',"
                f" start_offset => INTERVAL '{s}',"
                f" end_offset => INTERVAL '{b}',"
                f" schedule_interval => INTERVAL '{sc}',"
                " if_not_exists => TRUE)"
            ),
        )


def downgrade() -> None:
    """Onarim migration'i — geri alinacak bir sema degisikligi YOK.

    Politikalari kaldirmak veri kaybina (retention'in silecegi veri degil,
    tersine: politikayi kaldirmak diski doldurur) veya gereksiz riske yol
    acar. 0007'nin downgrade'i tabloyu tamamen dusuruyor; onarim adiminin
    ayrica geri alinmasi anlamsiz.
    """
    pass
