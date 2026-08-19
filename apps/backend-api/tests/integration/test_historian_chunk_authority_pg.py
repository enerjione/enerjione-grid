"""Historian chunk araligi — TEK OTORITE `timescale_setup`.

YASANAN AYRISMA (saha, 2026-08-19)
----------------------------------
Uc yerde uc farkli gercek vardi:

    timescale_setup.py : CHUNK_INTERVAL = "1 day"
    update.sh          : set_chunk_time_interval(... INTERVAL '1 hour')
    canli sistem       : 1 hour

Canli deger DOGRUYDU: migration 0030 bunu olcumle 1 gunden 1 saate cekti
(600 cihazda tek gunluk chunk ~17 GB eder, `shared_buffers` 1 GB'tir).
Yanlis olan `timescale_setup` idi ve orasi YALNIZCA TEMIZ KURULUMDA kosar —
yani yeni kurulan her saha, olcumle reddedilmis aralikla basliyor ve ancak
ilk `update.sh` kosumunda duzeliyordu.

Bu testler dort yolu da ayni otoriteye baglar: temiz kurulum, eski araliga
sahip mevcut kurulum, zaten dogru olan kurulum ve update.sh sozlesmesi.
"""

from __future__ import annotations

import os
import re

import pytest
from sqlalchemy import create_engine, text

pytestmark = pytest.mark.integration

PG_URL = os.getenv("E1_TEST_PG_URL", "")
if not PG_URL:
    pytest.skip("E1_TEST_PG_URL yok", allow_module_level=True)

ONEK = f"chunk_it_{os.getpid()}"


def _db_url(ad: str) -> str:
    return re.sub(r"/[^/?]+(\?|$)", f"/{ad}\\1", PG_URL, count=1)


def _admin(sql: str):
    eng = create_engine(PG_URL, isolation_level="AUTOCOMMIT")
    try:
        with eng.connect() as c:
            r = c.execute(text(sql))
            try:
                return list(r.fetchall())
            except Exception:  # noqa: BLE001
                return []
    finally:
        eng.dispose()


def _dusur(ad: str) -> None:
    _admin(
        "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
        f"WHERE datname = '{ad}' AND pid <> pg_backend_pid()"
    )
    _admin(f'DROP DATABASE IF EXISTS "{ad}"')


def _aralik_sn(eng) -> float:  # noqa: ANN001
    with eng.connect() as c:
        return c.execute(text(
            "SELECT time_interval FROM timescaledb_information.dimensions "
            "WHERE hypertable_name = 'telemetry_history'"
        )).scalar().total_seconds()


def _tablo_kur(eng, hypertable: bool, aralik: str | None = None) -> None:  # noqa: ANN001
    with eng.connect() as c:
        c.execute(text("CREATE EXTENSION IF NOT EXISTS timescaledb"))
        c.execute(text(
            "CREATE TABLE telemetry_history ("
            " device_id int NOT NULL, signal_key varchar(120) NOT NULL,"
            " source_timestamp timestamptz NOT NULL, value double precision,"
            " PRIMARY KEY (device_id, signal_key, source_timestamp))"))
        if hypertable:
            c.execute(text(
                "SELECT create_hypertable('telemetry_history','source_timestamp',"
                f" chunk_time_interval => INTERVAL '{aralik}', if_not_exists => TRUE)"))


@pytest.fixture()
def bos_db():
    ad = f"{ONEK}_db"
    _dusur(ad)
    _admin(f'CREATE DATABASE "{ad}" TEMPLATE template0')
    eng = create_engine(_db_url(ad), isolation_level="AUTOCOMMIT")
    yield eng
    eng.dispose()
    _dusur(ad)


def _kur(eng) -> dict:  # noqa: ANN001
    """`ensure_historian_storage`'i URETIMDEKI GIBI kostur.

    TRANSACTION SART: modulun her adimi `begin_nested()` (SAVEPOINT) icinde
    kosar ve SAVEPOINT autocommit baglantida calismaz. Uretimde bu kod
    acilista transaction icindeki bir baglantiyla cagriliyor; test de ayni
    kosulu kurmali, yoksa her adim sessizce atlanir ve test "kurulum
    yapilmadi"yi "kurulum bozuk" sanir.
    """
    from app.db import timescale_setup as ts

    tx_eng = create_engine(eng.url.render_as_string(hide_password=False))
    try:
        with tx_eng.begin() as c:
            return ts.ensure_historian_storage(c)
    finally:
        tx_eng.dispose()


# ===========================================================================
# C01 — TEMIZ KURULUM
# ===========================================================================


def test_C01_temiz_kurulumda_aralik_1_SAAT(bos_db) -> None:
    """`create_hypertable` uretim sozlesmesiyle kosmali.

    Bu test 1 gun degerinde DUSER — B06 mutasyonunun kilididir.
    """
    _tablo_kur(bos_db, hypertable=False)
    _kur(bos_db)
    assert _aralik_sn(bos_db) == 3600


# ===========================================================================
# C02 — MEVCUT 1 GUNLUK HYPERTABLE HIZALANIR
# ===========================================================================


def test_C02_mevcut_1_gunluk_hypertable_1_SAATE_cekilir(bos_db) -> None:
    """`create_hypertable(if_not_exists)` mevcut tabloda NO-OP olur.

    Yani hizalama ayri bir `set_chunk_time_interval` cagrisi olmadan
    GERCEKLESMEZ. Bu test o cagrinin kilididir (B07).
    """
    _tablo_kur(bos_db, hypertable=True, aralik="1 day")
    assert _aralik_sn(bos_db) == 86400, "on kosul kurulamadi"

    _kur(bos_db)
    assert _aralik_sn(bos_db) == 3600


# ===========================================================================
# C03 — ZATEN DOGRUYSA ZARARSIZ
# ===========================================================================


def test_C03_zaten_1_saatse_degismez(bos_db) -> None:
    _tablo_kur(bos_db, hypertable=True, aralik="1 hour")
    _kur(bos_db)
    assert _aralik_sn(bos_db) == 3600
    # Idempotent: ikinci kosum da ayni sonucu vermeli.
    _kur(bos_db)
    assert _aralik_sn(bos_db) == 3600


# ===========================================================================
# C04 — POLITIKALAR BOZULMAZ
# ===========================================================================


def test_C04_politikalar_kurulur_ve_korunur(bos_db) -> None:
    """Chunk hizalamasi saklama/sikistirma/CAGG kurulumunu bozmamali."""
    _tablo_kur(bos_db, hypertable=False)
    _kur(bos_db)

    with bos_db.connect() as c:
        isler = {
            (r[0], r[1])
            for r in c.execute(text(
                "SELECT proc_name, config::text FROM timescaledb_information.jobs "
                "WHERE proc_name IS NOT NULL"
            )).fetchall()
        }
        adlar = {p for p, _ in isler}
        assert "policy_retention" in adlar, "saklama politikasi kurulmadi"
        assert "policy_compression" in adlar, "sikistirma politikasi kurulmadi"

        cagg = c.execute(text(
            "SELECT count(*) FROM timescaledb_information.continuous_aggregates"
        )).scalar()
        assert cagg == 2, f"iki ozet katmani bekleniyordu, {cagg} bulundu"

        # Saklama penceresi kod sabitiyle ayni olmali.
        from app.db.timescale_setup import RETENTION_DAYS

        ham = c.execute(text(
            "SELECT config->>'drop_after' FROM timescaledb_information.jobs j "
            "WHERE j.proc_name='policy_retention' AND j.hypertable_name='telemetry_history'"
        )).scalar()
        assert ham == f"{RETENTION_DAYS} days"

    # Ikinci kosum yeni is EKLEMEMELI (idempotentlik).
    _kur(bos_db)
    with bos_db.connect() as c:
        sayi = c.execute(text(
            "SELECT count(*) FROM timescaledb_information.jobs WHERE proc_name IS NOT NULL"
        )).scalar()
    _kur(bos_db)
    with bos_db.connect() as c:
        sayi2 = c.execute(text(
            "SELECT count(*) FROM timescaledb_information.jobs WHERE proc_name IS NOT NULL"
        )).scalar()
    assert sayi == sayi2, "tekrar kosumda politika cogaldi"


# ===========================================================================
# C05 — update.sh SOZLESMESI AYNI DEGERI TASIR
# ===========================================================================


def test_C05_update_sh_ile_kod_ayni_araligi_soyluyor() -> None:
    """Iki yer arasinda deger CELISMEMELI.

    update.sh'taki ensure blogu kalabilir (backend hic kalkmadan da hizalama
    yapabilmesi degerli) ama farkli bir deger tasirsa hangisinin gecerli
    oldugu kurulum sirasina baglanirdi.
    """
    from pathlib import Path

    from app.db.timescale_setup import CHUNK_INTERVAL

    assert CHUNK_INTERVAL == "1 hour"

    kok = Path(__file__).resolve().parents[4]
    for yol in (kok / "update.sh", kok / "infra" / "scripts" / "linux" / "_lib.sh"):
        if not yol.is_file():
            continue
        metin = yol.read_text(encoding="utf-8")
        if "set_chunk_time_interval" not in metin:
            continue
        araliklar = set(re.findall(r"set_chunk_time_interval\([^)]*INTERVAL '([^']+)'", metin))
        assert araliklar <= {CHUNK_INTERVAL}, (
            f"{yol.name} farkli chunk araligi tasiyor: {araliklar} != {CHUNK_INTERVAL}"
        )
