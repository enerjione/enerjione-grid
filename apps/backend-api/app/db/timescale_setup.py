"""Historian depolama kurulumu — hypertable + saklama/sikistirma politikalari.

NEDEN AYRI BIR MODUL (migration'in icinde DEGIL)
------------------------------------------------
Bu adimlar once yalnizca migration 0007/0019/0023'un icinde yasiyordu. Migration
"bir kez kosan sema degisikligi"dir; oysa buradaki isler ORTAM'a bagli ve
sonradan dogru hale gelebilir:

  * TimescaleDB extension'i ilk boot'ta hazir olmayabilir
    (`postgres:16` imajiyla kurulmus bir volume, `shared_preload_libraries`
    yazilmamis bir ilk acilis),
  * ya da migration hic kosmayabilir.

IKINCISI TAM OLARAK YASANDI. Temiz kurulumda sema `Base.metadata.create_all`
ile modellerden kuruluyor ve alembic dogrudan `head` damgalaniyor (bkz.
`scripts/migrate_db.py`). Bu, 30 migration'in bos veritabanina tekrar
uygulanmasindan dogan cokmeyi cozdu — ama yan etkisi sessizdi:

    `create_all` YALNIZCA DUZ TABLOLARI kurar.

Hypertable'a cevirme, 90 gunluk saklama, sikistirma ve ozet katmanlari
SQLAlchemy modelinde tarif edilemez; yalnizca migration govdesinde vardi.
Dolayisiyla temiz kurulumda:

    * `telemetry_history` DUZ TABLO kaliyor,
    * saklama politikasi HIC kurulmuyor,
    * tablo sinirsiz buyuyor,
    * ve bunun tek belirtisi Sistem Durumu sayfasindaki uyari oluyor.

600 cihaz x ~193 sinyal olceginde bu tablo gunde ~26M satir buyur; belirti
disk dolana kadar ortaya cikmaz.

Cozum, kurulumu semaya DEGIL acilisa baglamak: modul idempotent, her boot'ta
kosuyor ve eksik olani tamamliyor. Zaten kuruluysa yalnizca birkac katalog
sorgusu maliyeti var.

HATA POLITIKASI — BILINCLI OLARAK "EN IYI CABA"
-----------------------------------------------
Her adim SAVEPOINT icinde kosar ve hatasi YUTULUR. Sebep: bu kod backend
boot'undan ONCE kosuyor; burada exception firlatmak backend'in HIC ayaga
kalkmamasi demek — yani sahayi karartmak.

SAVEPOINT sart, cunku hatayi yutmak tek basina yetmez: PostgreSQL'de bir
ifade patlayinca transaction "aborted" duruma gecer ve sonraki HER ifade
`InFailedSqlTransaction` ile duser. `begin_nested()` yalnizca o adimi geri
alir, dis transaction saglam kalir.

Sessiz basarisizlik riskini kapatan sey bu modul degil, yanindaki GORUNURLUK:
`app/services/historian_service.py` durumu olcup raporluyor ve Sistem Durumu
sayfasi saklama politikasi yoksa uyari gosteriyor. Onarim en-iyi-caba, tespit
ise garanti.
"""

from __future__ import annotations

import logging
from typing import Any, Callable

import sqlalchemy as sa
from sqlalchemy.engine import Connection

logger = logging.getLogger(__name__)

TABLE = "telemetry_history"
RETENTION_DAYS = 90
# 2026-08-05 SAHA OLCUMU: 7 gunluk esikle 46 chunk'in SIFIRI sikistirilmisti.
# Yuk testinde disk saatte ~8 GB buyuyordu; 7 gun boyunca hicbir sey
# sikismayacagi icin ~1,3 TB sikismamis veri birikecekti — 456 GB diske
# sigmaz. Yani esik, urunun kendi disk butcesiyle CELISIYORDU.
#
# 1 gun: TimescaleDB bu veri seklinde tipik olarak 10-20 kat sikistirir,
# yani sikismamis pencere 8 GB civarinda kalir. Sikistirilmis chunk'a
# gec gelen veri yazmak kisitlidir; cihazlar veriyi aninda gonderdigi
# icin bir gunluk pencere fazlasiyla yeterli (gateway tamponu saatler
# mertebesinde, gunler degil).
COMPRESS_AFTER_DAYS = 1
CHUNK_INTERVAL = "1 day"

# (view adi, kova araligi, policy start_offset, calisma araligi)
_AGGREGATES: tuple[tuple[str, str, str, str], ...] = (
    ("telemetry_history_1m", "1 minute", "3 hours", "1 minute"),
    ("telemetry_history_1h", "1 hour", "3 days", "1 hour"),
)

# (view adi, saklama gun, sikistirma sonrasi gun) — ozet katmanlari ham
# veriden UZUN yasar: 1 dakikalik ozet 1 yil, 1 saatlik ozet 2 yil.
_TIERS: tuple[tuple[str, int, int], ...] = (
    ("telemetry_history_1m", 365, 7),
    ("telemetry_history_1h", 730, 30),
)


def _try(bind: Connection, label: str, fn: Callable[[], Any]) -> bool:
    """Adimi SAVEPOINT icinde kostur; hata yutulur, transaction bozulmaz."""
    try:
        with bind.begin_nested():
            fn()
        return True
    except Exception as exc:  # noqa: BLE001
        logger.warning("historian kurulum adimi atlandi: %s -> %s", label, exc)
        return False


def _scalar(bind: Connection, sql: str, params: dict | None = None) -> bool:
    try:
        return bind.execute(sa.text(sql), params or {}).first() is not None
    except Exception:  # noqa: BLE001
        return False


def _table_exists(bind: Connection) -> bool:
    return _scalar(
        bind,
        "SELECT 1 FROM information_schema.tables WHERE table_name = :t",
        {"t": TABLE},
    )


def _extension_available(bind: Connection) -> bool:
    return _scalar(
        bind, "SELECT 1 FROM pg_available_extensions WHERE name = 'timescaledb'"
    )


def _extension_installed(bind: Connection) -> bool:
    return _scalar(bind, "SELECT 1 FROM pg_extension WHERE extname = 'timescaledb'")


def _is_hypertable(bind: Connection) -> bool:
    return _scalar(
        bind,
        "SELECT 1 FROM timescaledb_information.hypertables"
        " WHERE hypertable_name = :t",
        {"t": TABLE},
    )


def _has_job(bind: Connection, proc_name: str) -> bool:
    return _scalar(
        bind,
        "SELECT 1 FROM timescaledb_information.jobs"
        " WHERE proc_name = :p AND hypertable_name = :t",
        {"p": proc_name, "t": TABLE},
    )


def _cagg_exists(bind: Connection, view: str) -> bool:
    return _scalar(
        bind,
        "SELECT 1 FROM timescaledb_information.continuous_aggregates"
        " WHERE view_name = :v",
        {"v": view},
    )


def _cagg_has_job(bind: Connection, view: str, proc_name: str) -> bool:
    # CAGG'lerde `jobs.hypertable_name` materialization hypertable'ini
    # (`_materialized_hypertable_N`) gosterir, view adini DEGIL.
    return _scalar(
        bind,
        "SELECT 1 FROM timescaledb_information.jobs j"
        " JOIN timescaledb_information.continuous_aggregates c"
        "   ON j.hypertable_name = c.materialization_hypertable_name"
        " WHERE c.view_name = :v AND j.proc_name = :p",
        {"v": view, "p": proc_name},
    )


def ensure_historian_storage(bind: Connection) -> dict[str, Any]:
    """Historian depolamasini eksiksiz hale getir. Idempotent, en-iyi-caba.

    Doner: ne yapildigini ozetleyen sozluk (log ve test icin).
    """
    rapor: dict[str, Any] = {"skipped": None, "actions": []}

    def _yap(ad: str) -> None:
        rapor["actions"].append(ad)

    if not _table_exists(bind):
        # Sema henuz kurulmamis. Cagiran taraf bunu create_all/upgrade'den
        # SONRA cagirmali; yine de sessizce cikiyoruz.
        rapor["skipped"] = "table_missing"
        return rapor

    if not _extension_available(bind):
        # Vanilla postgres (gelistirici makinesi). Yapabilecegimiz bir sey yok;
        # historian_service bunu "timescaledb_unavailable" olarak raporlar.
        rapor["skipped"] = "timescaledb_unavailable"
        return rapor

    if not _extension_installed(bind):
        if not _try(
            bind,
            "CREATE EXTENSION timescaledb",
            lambda: bind.execute(sa.text("CREATE EXTENSION IF NOT EXISTS timescaledb")),
        ):
            rapor["skipped"] = "extension_install_failed"
            return rapor
        _yap("create_extension")

    # 1) Hypertable. Bu olmadan hicbir politika eklenemez.
    if not _is_hypertable(bind):
        logger.warning("%s hypertable degil — cevriliyor", TABLE)
        # migrate_data=TRUE: duz tabloda birikmis satirlar chunk'lara tasinir.
        # Temiz kurulumda tablo bos oldugu icin bu aninda biter; mevcut bir
        # kurulumda uzun surebilir ama YALNIZCA BIR KEZ olur.
        if not _try(
            bind,
            "create_hypertable",
            lambda: bind.execute(
                sa.text(
                    f"SELECT create_hypertable('{TABLE}', 'source_timestamp',"
                    f" chunk_time_interval => INTERVAL '{CHUNK_INTERVAL}',"
                    " migrate_data => TRUE, if_not_exists => TRUE)"
                )
            ),
        ):
            rapor["skipped"] = "hypertable_failed"
            return rapor
        _yap("create_hypertable")

    # 2) Saklama politikasi — ASIL MESELE. Yoksa disk dolar.
    if not _has_job(bind, "policy_retention"):
        logger.warning("saklama politikasi yok — %d gun ekleniyor", RETENTION_DAYS)
        if _try(
            bind,
            "add_retention_policy",
            lambda: bind.execute(
                sa.text(
                    f"SELECT add_retention_policy('{TABLE}',"
                    f" INTERVAL '{RETENTION_DAYS} days', if_not_exists => TRUE)"
                )
            ),
        ):
            _yap("add_retention_policy")

    # 3) Sikistirma. Once tablo ayari, sonra policy.
    if not _has_job(bind, "policy_compression"):
        _try(
            bind,
            "ALTER TABLE ... compress",
            lambda: bind.execute(
                sa.text(
                    f"ALTER TABLE {TABLE} SET ("
                    " timescaledb.compress,"
                    " timescaledb.compress_segmentby = 'device_id, signal_key',"
                    " timescaledb.compress_orderby = 'source_timestamp DESC')"
                )
            ),
        )
        if _try(
            bind,
            "add_compression_policy",
            lambda: bind.execute(
                sa.text(
                    f"SELECT add_compression_policy('{TABLE}',"
                    f" INTERVAL '{COMPRESS_AFTER_DAYS} days', if_not_exists => TRUE)"
                )
            ),
        ):
            _yap("add_compression_policy")

    # 4) Ozet katmanlari (grafik/analiz kaynagi).
    for view, bucket, start_off, sched in _AGGREGATES:
        if not _cagg_exists(bind, view):
            if _try(
                bind,
                f"CREATE MATERIALIZED VIEW {view}",
                lambda v=view, b=bucket: bind.execute(
                    sa.text(
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
                    )
                ),
            ):
                _yap(f"create_cagg:{view}")

        if not _cagg_has_job(bind, view, "policy_refresh_continuous_aggregate"):
            _try(
                bind,
                f"add_continuous_aggregate_policy {view}",
                lambda v=view, s=start_off, sc=sched, b=bucket: bind.execute(
                    sa.text(
                        f"SELECT add_continuous_aggregate_policy('{v}',"
                        f" start_offset => INTERVAL '{s}',"
                        f" end_offset => INTERVAL '{b}',"
                        f" schedule_interval => INTERVAL '{sc}',"
                        " if_not_exists => TRUE)"
                    )
                ),
            )

    # 5) Ozet katmanlarinin KENDI saklama/sikistirma politikalari. Ozetler ham
    #    veriden uzun yasar; ham veri 90 gunde silinince gecmis grafik ancak
    #    bu katmanlar sayesinde kalir.
    for view, retention_days, compress_after in _TIERS:
        if not _cagg_exists(bind, view):
            continue

        if not _cagg_has_job(bind, view, "policy_compression"):
            # compress_segmentby/orderby bazi TimescaleDB surumlerinde CAGG'de
            # kabul edilmiyor; once tam form, olmazsa sade form.
            ok = _try(
                bind,
                f"ALTER MATERIALIZED VIEW {view} SET compress (tam form)",
                lambda v=view: bind.execute(
                    sa.text(
                        f"ALTER MATERIALIZED VIEW {v} SET ("
                        " timescaledb.compress = true,"
                        " timescaledb.compress_segmentby = 'device_id, signal_key')"
                    )
                ),
            )
            if not ok:
                ok = _try(
                    bind,
                    f"ALTER MATERIALIZED VIEW {view} SET compress (sade form)",
                    lambda v=view: bind.execute(
                        sa.text(
                            f"ALTER MATERIALIZED VIEW {v} SET (timescaledb.compress = true)"
                        )
                    ),
                )
            if ok:
                _try(
                    bind,
                    f"add_compression_policy {view}",
                    lambda v=view, d=compress_after: bind.execute(
                        sa.text(
                            f"SELECT add_compression_policy('{v}',"
                            f" compress_after => INTERVAL '{d} days',"
                            " if_not_exists => TRUE)"
                        )
                    ),
                )

        if not _cagg_has_job(bind, view, "policy_retention"):
            if _try(
                bind,
                f"add_retention_policy {view}",
                lambda v=view, d=retention_days: bind.execute(
                    sa.text(
                        f"SELECT add_retention_policy('{v}',"
                        f" drop_after => INTERVAL '{d} days', if_not_exists => TRUE)"
                    )
                ),
            ):
                _yap(f"add_retention_policy:{view}")

    return rapor
