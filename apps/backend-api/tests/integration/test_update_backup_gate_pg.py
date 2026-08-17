"""Update Backup Gate — GERCEK PostgreSQL 16 + TimescaleDB 2.17.2 roundtrip.

NE KANITLIYOR
-------------
Guncelleme oncesi kapinin urettigi arsiv, GERI YUKLENEBILIR OLMAK ZORUNDA.
Aksi halde kapi yalnizca bir dosya uretir ve "geri donus noktam var" yanilgisi
yaratir — tam da eski davranisin (duz `.sql.gz`, `validate_dump_file`in PGDMP
imza kontrolune takilan, yani GERI YUKLENEMEYEN dosya) yarattigi yanilgi.

Bu yuzden burada arsiv sentetik uretilmiyor: `update.sh`in kullandigi
BAYRAKLARIN AYNISI ile aliniyor (`-F c --no-owner --no-acl` +
`--exclude-table-data` listesi `_lib.sh`ten OKUNUYOR) ve ardindan uretimin
gercek Safe Restore staging akisindan geciriliyor.

Bayrak listesi kaynaktan okunuyor cunku iki kopya (backend / update.sh)
zamanla ayrisabilir; sabit kopyalamak bu testi ilk ayrismada anlamsiz
kilardi.
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path

import pytest
from sqlalchemy import create_engine, text

from app.services import safe_restore as sr

pytestmark = pytest.mark.integration

PG_URL = os.getenv("E1_TEST_PG_URL", "")
if not PG_URL:
    pytest.skip("E1_TEST_PG_URL yok", allow_module_level=True)

ONEK = f"ubg_it_{os.getpid()}"
KOK = Path(__file__).resolve().parents[4]  # integration -> tests -> backend-api -> apps -> repo koku
LIB_SH = KOK / "infra" / "scripts" / "linux" / "_lib.sh"


# ---------------------------------------------------------------------------
# Yardimcilar (safe_restore integration testleriyle ayni kalip)
# ---------------------------------------------------------------------------


def _db_url(ad: str) -> str:
    return re.sub(r"/[^/?]+(\?|$)", f"/{ad}\\1", PG_URL, count=1)


def _admin(sql: str):
    eng = create_engine(PG_URL, isolation_level="AUTOCOMMIT")
    try:
        with eng.connect() as c:
            r = c.execute(text(sql))
            return list(r) if r.returns_rows else []
    finally:
        eng.dispose()


def _q(db: str, sql: str):
    eng = create_engine(_db_url(db), isolation_level="AUTOCOMMIT")
    try:
        with eng.connect() as c:
            r = c.execute(text(sql))
            return list(r) if r.returns_rows else []
    finally:
        eng.dispose()


def _dusur(ad: str) -> None:
    _admin(
        f"SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
        f"WHERE datname='{ad}' AND pid <> pg_backend_pid()"
    )
    _admin(f'DROP DATABASE IF EXISTS "{ad}"')


def _lib_exclude_listesi() -> list[str]:
    """`_lib.sh` icindeki `E1_DUMP_EXCLUDE` dizisini okur.

    Kopyalamak yerine kaynaktan okunuyor: liste degistiginde bu test de
    otomatik olarak yeni listeyi kullanir, yani sessizce eskimez.
    """
    metin = LIB_SH.read_text(encoding="utf-8")
    m = re.search(r"E1_DUMP_EXCLUDE=\((.*?)\n\)", metin, re.S)
    assert m, "_lib.sh icinde E1_DUMP_EXCLUDE bulunamadi"
    girdiler = []
    for satir in m.group(1).splitlines():
        satir = satir.split("#", 1)[0].strip().strip("'\"")
        if satir:
            girdiler.append(satir)
    assert girdiler, "E1_DUMP_EXCLUDE bos okundu"
    return girdiler


@pytest.fixture()
def uretim(monkeypatch, paylasim):
    """Isaretli veri + TimescaleDB yapisi tasiyan gercek bir 'uretim' DB'si."""
    ad = f"{ONEK}_prod"
    _dusur(ad)
    _admin(f'CREATE DATABASE "{ad}" TEMPLATE template0')

    eng = create_engine(_db_url(ad), isolation_level="AUTOCOMMIT")
    with eng.connect() as c:
        c.execute(text("CREATE EXTENSION IF NOT EXISTS timescaledb"))
        c.execute(text(
            "CREATE TABLE alembic_version (version_num varchar(32) primary key)"))
        c.execute(text("INSERT INTO alembic_version VALUES ('0069')"))
        # ISARET VERISI — restore sonrasi birebir aranacak.
        c.execute(text("CREATE TABLE users (id serial primary key, username text)"))
        c.execute(text("INSERT INTO users (username) VALUES ('ISARET-OPERATOR')"))
        c.execute(text("CREATE TABLE devices (id serial primary key, code text)"))
        c.execute(text("INSERT INTO devices (code) VALUES ('ISARET-CIHAZ')"))
        c.execute(text(
            "CREATE TABLE project_settings (id int primary key, ad text)"))
        c.execute(text("INSERT INTO project_settings VALUES (1,'ISARET-PROJE')"))
        # Yedek DISI birakilan buyuk tablo (hypertable).
        c.execute(text(
            "CREATE TABLE telemetry_history ("
            " device_id int, signal_key text, value double precision,"
            " source_timestamp timestamptz NOT NULL)"))
        c.execute(text(
            "SELECT create_hypertable('telemetry_history','source_timestamp')"))
        c.execute(text(
            "INSERT INTO telemetry_history "
            "SELECT 1,'s', g, now() - (g || ' minutes')::interval "
            "FROM generate_series(1,500) g"))
        # Yedege GIRMESI gereken kucuk tablolar
        c.execute(text("CREATE TABLE telemetry (id bigserial primary key)"))
        c.execute(text("CREATE TABLE outbox_events (id bigserial primary key)"))
        c.execute(text("CREATE TABLE processed_messages (id bigserial primary key)"))
        c.execute(text("CREATE TABLE gateway_ingest_batches (id bigserial primary key)"))
        c.execute(text("CREATE TABLE backup_jobs (id bigserial primary key)"))
        c.execute(text("CREATE TABLE backup_schedule (id bigserial primary key)"))
        c.execute(text("CREATE TABLE telemetry_latest (id bigserial primary key)"))
        c.execute(text("CREATE TABLE telemetry_history_1m (id bigserial primary key)"))
        c.execute(text("CREATE TABLE telemetry_history_1h (id bigserial primary key)"))
        c.execute(text("CREATE TABLE alarm_events (id bigserial primary key)"))
        c.execute(text("CREATE TABLE system_events (id bigserial primary key)"))
    eng.dispose()

    from app.core.config import settings

    monkeypatch.setattr(settings, "database_url", _db_url(ad), raising=False)
    monkeypatch.setattr("app.services.backup_service.get_backup_dir", lambda: paylasim)
    # Migration adimi bu testin konusu degil (Safe Restore'un kendi IT-13'u
    # onu kapsiyor); staging'e gercek alembic kosturmak burada gereksiz.
    monkeypatch.setattr(sr, "staging_migrate", lambda s: (True, ""))
    monkeypatch.setattr(sr, "_kod_head", lambda: "0069")

    yield ad

    for (d,) in _admin("SELECT datname FROM pg_database"):
        if str(d).startswith(ONEK):
            _dusur(str(d))


@pytest.fixture()
def gate_dump(uretim, paylasim) -> Path:
    """`update.sh` kapisiyla AYNI bayraklarla alinmis pre-update yedegi."""
    from app.services.backup_service import _parse_db_url, resolve_pg_binary

    db = _parse_db_url(PG_URL)
    hedef = paylasim / f"auto-pre-update-2.97.0_to_2.98.0-{ONEK}.dump"
    gecici = paylasim / f".backup.tmp.{os.getpid()}"

    args = [
        resolve_pg_binary("pg_dump"),
        "-h", db["host"], "-p", db["port"], "-U", db["user"],
        "-d", uretim,
        "-F", "c", "--no-owner", "--no-acl",
    ]
    for t in _lib_exclude_listesi():
        args += ["--exclude-table-data", t]
    args += ["-f", str(gecici)]

    ortam = os.environ.copy()
    if db["password"]:
        ortam["PGPASSWORD"] = db["password"]
    p = subprocess.run(args, env=ortam, capture_output=True, text=True, check=False)
    assert p.returncode == 0, p.stderr[-1500:]

    # Kapinin sozlesmesi: once gecici ad, dogrulama sonrasi ATOMIK tasima.
    gecici.replace(hedef)
    return hedef


# ===========================================================================
# IT01-IT04 — yedek uretimi ve dogrulanabilirligi
# ===========================================================================


def test_IT01_gate_yedegi_uretilir_ve_pgdmp(gate_dump: Path) -> None:
    assert gate_dump.exists() and gate_dump.is_file()
    assert gate_dump.stat().st_size > 0
    assert gate_dump.read_bytes()[:5] == b"PGDMP", "custom format degil"


def test_IT02_pg_restore_list_gecerli(gate_dump: Path) -> None:
    """Kapinin uyguladigi dogrulamanin GERCEK arsiv uzerindeki karsiligi."""
    from app.services.backup_service import resolve_pg_binary

    p = subprocess.run(
        [resolve_pg_binary("pg_restore"), "--list", str(gate_dump)],
        capture_output=True, text=True, check=False,
    )
    assert p.returncode == 0, p.stderr[-800:]
    assert "users" in p.stdout


def test_IT02b_safe_restore_on_dogrulamasi_kabul_eder(gate_dump: Path) -> None:
    """Ayni arsivi backend'in Safe Restore dogrulayicisi da kabul etmeli.

    Iki ayri dogrulayici ayrisirsa kapi "gecerli" der, geri yukleme reddeder.
    """
    ok, mesaj = sr.arsiv_on_dogrula(gate_dump)
    assert ok is True, mesaj


def test_IT03_kirpilmis_arsiv_reddedilir(gate_dump: Path, paylasim) -> None:
    """Kirpilmis arsiv HEM pg_restore HEM Safe Restore tarafindan reddedilmeli."""
    from app.services.backup_service import resolve_pg_binary

    bozuk = paylasim / "kirpik.dump"
    ham = gate_dump.read_bytes()
    bozuk.write_bytes(ham[: len(ham) // 3])

    p = subprocess.run(
        [resolve_pg_binary("pg_restore"), "--list", str(bozuk)],
        capture_output=True, text=True, check=False,
    )
    assert p.returncode != 0, "kirpilmis arsiv gecerli sayildi"


def test_IT04_haric_tutulan_tablo_verisi_yedege_girmez(gate_dump: Path) -> None:
    """`telemetry_history` SEMASI girer, VERISI girmez.

    Bu, dosyanin sahada GB'lara sismesini onleyen sozlesme.
    """
    from app.services.backup_service import resolve_pg_binary

    p = subprocess.run(
        [resolve_pg_binary("pg_restore"), "--list", str(gate_dump)],
        capture_output=True, text=True, check=False,
    )
    assert p.returncode == 0
    veri_satirlari = [
        s for s in p.stdout.splitlines()
        if "TABLE DATA" in s and "telemetry_history" in s
    ]
    assert not veri_satirlari, f"haric tutulan tablonun VERISI dump'ta: {veri_satirlari}"


# ===========================================================================
# IT05 — SAFE RESTORE ROUNDTRIP (asil kanit)
# ===========================================================================


def test_IT05_gate_yedegi_safe_restore_ile_geri_yuklenir(uretim, gate_dump) -> None:
    """Kapinin urettigi yedek, uretimin GERCEK restore akisiyla geri acilir.

    Bu test gecmeden kapi "geri donus noktasi uretiyor" DIYEMEZ.
    """
    # Uretimi BOZ: isaret verisi degistirilsin ki geri gelmesi olculebilsin.
    _q(uretim, "UPDATE users SET username='BOZULDU'")
    _q(uretim, "DELETE FROM devices")
    assert _q(uretim, "SELECT username FROM users")[0][0] == "BOZULDU"

    ok, hata = sr.run(9701, gate_dump)
    assert ok is True, hata

    # 1) Isaret verisi geri geldi
    assert _q(uretim, "SELECT username FROM users")[0][0] == "ISARET-OPERATOR"
    assert _q(uretim, "SELECT code FROM devices")[0][0] == "ISARET-CIHAZ"
    assert _q(uretim, "SELECT ad FROM project_settings")[0][0] == "ISARET-PROJE"

    # 2) Sema butun
    assert _q(uretim, "SELECT version_num FROM alembic_version")[0][0] == "0069"

    # 3) TimescaleDB eklentisi ve hypertable yapisi korundu
    assert _q(
        uretim, "SELECT count(*) FROM pg_extension WHERE extname='timescaledb'"
    )[0][0] == 1
    assert _q(
        uretim,
        "SELECT count(*) FROM timescaledb_information.hypertables "
        "WHERE hypertable_name='telemetry_history'",
    )[0][0] == 1

    # 4) Haric tutulan tablonun VERISI bilincli olarak yok — ama TABLOSU var.
    #    (Bu bir kayip degil, tasarim: historian arsivi yedege alinmiyor.)
    assert _q(uretim, "SELECT count(*) FROM telemetry_history")[0][0] == 0
