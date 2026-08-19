"""Historian yedek dislamasi — GERCEK PostgreSQL 16 + TimescaleDB 2.17.2.

NE KANITLIYOR
-------------
Yedek dosyasina historian VERISI girmez. Bu bir kalip/argüman testi DEGIL:
uretilen GERCEK dump'in icindekiler tablosu (`pg_restore --list`) okunur ve
oraya restore edilip satirlar SAYILIR.

NEDEN BOYLE YAZILDI — YASANAN ARIZA (saha, 2026-08-19)
------------------------------------------------------
Onceki koruma iki sabit kalipti:

    _timescaledb_internal._hyper_*
    _timescaledb_internal._materialized_hypertable_*

ve yanindaki test "kalip pg_dump argumanlarinda var mi" diye bakiyordu.
Ikisi de gecti, ariza yine de CANLIYDI: sikistirilmis bir hypertable'in
satirlari `_hyper_*` chunk'larinda DEGIL

    _timescaledb_internal.compress_hyper_<id>_<M>_chunk

tablolarinda durur. Bu ad `_hyper_` ile BASLAMAZ, dolayisiyla kalip ona hic
degmiyordu. Sahada 272 chunk'in 240'i sikistirilmisti — yani historian'in
neredeyse tamami her yedege giriyordu ve `pg_restore` ile geri geliyordu
(olculdu: 131.091 satir).

Mevcut `test_IT04_haric_tutulan_tablo_verisi_yedege_girmez` de bu yuzden
sessiz kaldi: TOC satirinda `"telemetry_history"` alt dizesini ariyordu,
oysa sizan satirin adi `compress_hyper_4_113_chunk` idi.

Bu dosyadaki testler bu iki korlugu birden kapatir:
  * chunk adlari KALIPLA degil, hypertable'a AIT OLMA uzerinden aranir,
  * ve nihai olcut TOC degil, RESTORE SONRASI SATIR SAYISIDIR.
"""

from __future__ import annotations

import os
import re
import subprocess
from pathlib import Path

import pytest
from sqlalchemy import create_engine, text

pytestmark = pytest.mark.integration

PG_URL = os.getenv("E1_TEST_PG_URL", "")
if not PG_URL:
    pytest.skip("E1_TEST_PG_URL yok", allow_module_level=True)

ONEK = f"hbx_it_{os.getpid()}"
KOK = Path(__file__).resolve().parents[4]
LIB_SH = KOK / "infra" / "scripts" / "linux" / "_lib.sh"


# ---------------------------------------------------------------------------
# Yardimcilar
# ---------------------------------------------------------------------------


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


def _q(db: str, sql: str):
    """Sorgu ya da prosedur cagrisi. `CALL ...` satir dondurmez; yutulur."""
    eng = create_engine(_db_url(db), isolation_level="AUTOCOMMIT")
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


def _lib_sh_historian_sql() -> str:
    """`_lib.sh` icindeki `E1_HISTORIAN_EXCLUDE_SQL` degerini OKUR.

    Kopyalanmaz: update.sh yolunun gercekten hangi sorguyu kullandigini
    kaynaktan aliriz, yoksa test ilk ayrismada anlamsizlasir.
    """
    metin = LIB_SH.read_text(encoding="utf-8")
    m = re.search(r'E1_HISTORIAN_EXCLUDE_SQL="(.*?)\n"', metin, re.S)
    assert m, "_lib.sh icinde E1_HISTORIAN_EXCLUDE_SQL bulunamadi"
    return m.group(1)


def _lib_sh_duz_liste() -> list[str]:
    metin = LIB_SH.read_text(encoding="utf-8")
    m = re.search(r"E1_DUMP_EXCLUDE=\((.*?)\n\)", metin, re.S)
    assert m, "_lib.sh icinde E1_DUMP_EXCLUDE bulunamadi"
    out = []
    for satir in m.group(1).splitlines():
        satir = satir.split("#", 1)[0].strip().strip("'\"")
        if satir:
            out.append(satir)
    return out


def _lib_sh_estimator_sql() -> str:
    """`e1_backup_gerekli_mb` icindeki tahmin SQL'ini OKUR."""
    metin = LIB_SH.read_text(encoding="utf-8")
    m = re.search(
        r'e1_backup_gerekli_mb\(\).*?-tAc "(.*?)" 2>/dev/null',
        metin, re.S,
    )
    assert m, "_lib.sh icinde e1_backup_gerekli_mb SQL'i bulunamadi"
    return m.group(1)


def _pg_dump(db: str, hedef: Path, ekstra: list[str]) -> None:
    from app.services.backup_service import _parse_db_url, resolve_pg_binary

    d = _parse_db_url(PG_URL)
    args = [
        resolve_pg_binary("pg_dump"),
        "-h", d["host"], "-p", d["port"], "-U", d["user"], "-d", db,
        "-F", "c", "--no-owner", "--no-acl",
        *ekstra,
        "-f", str(hedef),
    ]
    ortam = os.environ.copy()
    if d["password"]:
        ortam["PGPASSWORD"] = d["password"]
    p = subprocess.run(args, env=ortam, capture_output=True, text=True, check=False)
    assert p.returncode == 0, p.stderr[-2000:]
    assert hedef.exists() and hedef.stat().st_size > 0
    assert hedef.read_bytes()[:5] == b"PGDMP"


def _toc(dump: Path) -> list[str]:
    from app.services.backup_service import resolve_pg_binary

    p = subprocess.run(
        [resolve_pg_binary("pg_restore"), "--list", str(dump)],
        capture_output=True, text=True, check=False,
    )
    assert p.returncode == 0, p.stderr[-2000:]
    return p.stdout.splitlines()


def _historian_veri_satirlari(dump: Path) -> list[str]:
    """Dump'taki historian'a ait TUM `TABLE DATA` girdileri.

    Chunk adlarini kalipla degil, TimescaleDB'nin depolama adlandirmasiyla
    ariyoruz: ham chunk (`_hyper_N_M_chunk`), sikistirilmis chunk
    (`compress_hyper_N_M_chunk`), materialization/compressed ust tablolar ve
    duz `telemetry_history*` adlari.
    """
    desenler = (
        re.compile(r"_hyper_\d+_\d+_chunk"),
        re.compile(r"compress_hyper_\d+_\d+_chunk"),
        re.compile(r"_materialized_hypertable_\d+"),
        re.compile(r"_compressed_hypertable_\d+"),
        re.compile(r"\btelemetry_history(_1m|_1h)?\b"),
    )
    return [
        s for s in _toc(dump)
        if "TABLE DATA" in s and any(d.search(s) for d in desenler)
    ]


# ---------------------------------------------------------------------------
# Uretim benzeri historian
# ---------------------------------------------------------------------------


@pytest.fixture()
def historian(monkeypatch, paylasim):
    """Gercek historian yapisi: hypertable + SIKISTIRMA + iki CAGG.

    SIKISTIRMA SART: arizanin tamami sikistirilmis chunk deposundaydi.
    Sikistirmasiz bir fixture bu sinifi HIC gormez — nitekim mevcut
    `test_update_backup_gate_pg.py` fixture'i tam da bu yuzden sessiz kaldi.
    """
    ad = f"{ONEK}_prod"
    _dusur(ad)
    _admin(f'CREATE DATABASE "{ad}" TEMPLATE template0')

    eng = create_engine(_db_url(ad), isolation_level="AUTOCOMMIT")
    with eng.connect() as c:
        c.execute(text("CREATE EXTENSION IF NOT EXISTS timescaledb"))
        # Yedege GIRMESI gereken yapilandirma / olay verisi
        c.execute(text("CREATE TABLE alembic_version (version_num varchar(32) primary key)"))
        c.execute(text("INSERT INTO alembic_version VALUES ('0072')"))
        c.execute(text("CREATE TABLE users (id serial primary key, username text)"))
        c.execute(text("INSERT INTO users (username) VALUES ('ISARET-OPERATOR')"))
        c.execute(text("CREATE TABLE devices (id serial primary key, code text)"))
        c.execute(text("INSERT INTO devices (code) VALUES ('ISARET-CIHAZ')"))
        c.execute(text("CREATE TABLE gateways (id serial primary key, code text)"))
        c.execute(text("INSERT INTO gateways (code) VALUES ('ISARET-GW')"))
        c.execute(text("CREATE TABLE alarm_events (id serial primary key, title text)"))
        c.execute(text(
            "INSERT INTO alarm_events (title) SELECT 'a'||g FROM generate_series(1,50) g"))
        c.execute(text("CREATE TABLE system_events (id serial primary key, category text)"))
        c.execute(text(
            "INSERT INTO system_events (category) SELECT 'audit' FROM generate_series(1,80) g"))
        c.execute(text("CREATE TABLE device_commands (id serial primary key, status text)"))
        c.execute(text(
            "INSERT INTO device_commands (status) SELECT 'done' FROM generate_series(1,30) g"))

        # Yedek DISI birakilan duz tablolar
        for t in ("telemetry", "outbox_events", "processed_messages",
                  "gateway_ingest_batches", "backup_jobs", "backup_schedule"):
            c.execute(text(f"CREATE TABLE {t} (id bigserial primary key, doldur text)"))
            c.execute(text(
                f"INSERT INTO {t} (doldur) SELECT repeat('x',50) FROM generate_series(1,500) g"))

        # HISTORIAN
        c.execute(text(
            "CREATE TABLE telemetry_history ("
            " device_id int NOT NULL, signal_key varchar(120) NOT NULL,"
            " source_timestamp timestamptz NOT NULL, value double precision,"
            " PRIMARY KEY (device_id, signal_key, source_timestamp))"))
        c.execute(text(
            "SELECT create_hypertable('telemetry_history','source_timestamp',"
            " chunk_time_interval => INTERVAL '1 hour', if_not_exists => TRUE)"))
        c.execute(text(
            "INSERT INTO telemetry_history "
            "SELECT (g%10)+1, 'sig'||(g%20), now() - (g || ' seconds')::interval, random() "
            "FROM generate_series(1,60000) g"))

        for gorunum, kova in (("telemetry_history_1m", "1 minute"),
                              ("telemetry_history_1h", "1 hour")):
            c.execute(text(
                f"CREATE MATERIALIZED VIEW {gorunum} WITH (timescaledb.continuous) AS "
                f"SELECT device_id, signal_key,"
                f" time_bucket(INTERVAL '{kova}', source_timestamp) AS bucket,"
                f" avg(value) AS avg_value FROM telemetry_history"
                f" GROUP BY 1,2,3 WITH NO DATA"))

        # SIKISTIRMA — arizanin gercek yuzeyi.
        c.execute(text(
            "ALTER TABLE telemetry_history SET (timescaledb.compress,"
            " timescaledb.compress_segmentby='device_id,signal_key')"))
    eng.dispose()

    # CAGG refresh ve compress_chunk transaction disinda kosmali.
    for gorunum in ("telemetry_history_1m", "telemetry_history_1h"):
        _q(ad, f"CALL refresh_continuous_aggregate('{gorunum}', NULL, NULL)")
    for (chunk,) in _q(ad,
        "SELECT format('%I.%I', chunk_schema, chunk_name) FROM timescaledb_information.chunks "
        "WHERE hypertable_name='telemetry_history'"):
        _q(ad, f"SELECT compress_chunk('{chunk}', if_not_compressed => TRUE)")

    sikismis = _q(ad,
        "SELECT count(*) FROM timescaledb_information.chunks "
        "WHERE hypertable_name='telemetry_history' AND is_compressed")[0][0]
    assert sikismis > 0, "fixture sikistirilmis chunk uretemedi — ariza sinifi kapsanmaz"

    from app.core.config import settings

    monkeypatch.setattr(settings, "database_url", _db_url(ad), raising=False)
    monkeypatch.setattr("app.services.backup_service.get_backup_dir", lambda: paylasim)

    yield ad

    for (d,) in _admin("SELECT datname FROM pg_database"):
        if str(d).startswith(ONEK):
            _dusur(str(d))


@pytest.fixture()
def backend_args(historian, monkeypatch, paylasim) -> list[str]:
    """Backend'in GERCEK `pg_dump` komutundan okunan dislama argumanlari.

    ARGUMANLAR YENIDEN KURGULANMAZ — `run_pg_dump` calistirilir ve kurdugu
    komut yakalanir. Fark onemli: arguman listesini testte yeniden kurmak,
    `run_pg_dump` dislamayi uygulamayi BIRAKSA bile testi yesil birakirdi
    (bu dosyanin ilk halinde tam olarak bu oldu ve B04 mutasyonu kacti).
    """
    from app.services import backup_service as bs

    eng = create_engine(_db_url(historian))
    monkeypatch.setattr("app.db.session.engine", eng, raising=False)

    yakalanan: dict[str, list[str]] = {}
    gercek_run = subprocess.run

    def _yakala(cmd, *a, **kw):  # noqa: ANN001, ANN202
        yakalanan["cmd"] = list(cmd)
        return gercek_run(cmd, *a, **kw)

    monkeypatch.setattr(bs.subprocess, "run", _yakala)
    try:
        ok, hata = bs.run_pg_dump(paylasim / f"{ONEK}-gercek-yol.dump")
        assert ok, hata
    finally:
        eng.dispose()

    cmd = yakalanan.get("cmd")
    assert cmd, "run_pg_dump bir komut kurmadi"
    args: list[str] = []
    for anahtar, deger in zip(cmd, cmd[1:]):
        if anahtar == "--exclude-table-data":
            args += ["--exclude-table-data", deger]
    assert args, "run_pg_dump hic --exclude-table-data uretmedi"
    return args


@pytest.fixture()
def lib_args(historian) -> list[str]:
    """`update.sh` yolunun GERCEK dislama argumanlari (`_lib.sh`ten okunur)."""
    args: list[str] = []
    for t in _lib_sh_duz_liste():
        args += ["--exclude-table-data", t]
    for (kalip,) in _q(historian, _lib_sh_historian_sql()):
        args += ["--exclude-table-data", kalip]
    return args


# ===========================================================================
# HX01 — CURRENT FAILURE yeniden uretilebilir olmali (regresyon capasi)
# ===========================================================================


def test_HX01_eski_sabit_kalip_historian_i_SIZDIRIR(historian, paylasim) -> None:
    """Eski kalip setinin YETERSIZ oldugunu ayni ortamda kanitlar.

    Bu test gecerse duzeltmenin gercek bir seyi cozdugunu biliriz; duserse
    ariza sinifi ortadan kalkmis (ornegin TimescaleDB depolamayi degistirmis)
    demektir ve bu dosyanin gerekcesi gozden gecirilmeli.
    """
    dump = paylasim / f"{ONEK}-eski.dump"
    eski = []
    for t in ("telemetry", "outbox_events", "processed_messages",
              "telemetry_history", "telemetry_history_1m", "telemetry_history_1h"):
        eski += ["--exclude-table-data", t]
    eski += ["--exclude-table-data", "_timescaledb_internal._hyper_*"]
    eski += ["--exclude-table-data", "_timescaledb_internal._materialized_hypertable_*"]

    _pg_dump(historian, dump, eski)
    sizan = _historian_veri_satirlari(dump)
    assert sizan, (
        "eski kalip seti bu ortamda sizdirmiyor — ariza sinifi degismis olabilir"
    )
    assert any("compress_hyper" in s for s in sizan), (
        "sizinti sikistirilmis chunk'lardan gelmeliydi"
    )


# ===========================================================================
# HX02/HX03 — IKI YOL DA historian'i DISLAMALI  (B01, B02, B03)
# ===========================================================================


def test_HX02_backend_dumpinda_historian_TABLE_DATA_yok(
    historian, paylasim, backend_args
) -> None:
    dump = paylasim / f"{ONEK}-backend.dump"
    _pg_dump(historian, dump, backend_args)
    assert _historian_veri_satirlari(dump) == []


def test_HX03_preupdate_dumpinda_historian_TABLE_DATA_yok(
    historian, paylasim, lib_args
) -> None:
    dump = paylasim / f"{ONEK}-preupdate.dump"
    _pg_dump(historian, dump, lib_args)
    assert _historian_veri_satirlari(dump) == []


def test_HX04_iki_yol_ayni_kumeyi_disliyor(backend_args, lib_args) -> None:
    """Parite: string listesi degil, URETILEN ARGUMAN KUMESI karsilastirilir."""
    def kume(args: list[str]) -> set[str]:
        return {v for k, v in zip(args, args[1:]) if k == "--exclude-table-data"}

    assert kume(backend_args) == kume(lib_args)


# ===========================================================================
# HX05 — RESTORE SONRASI SATIR SAYISI (asil kabul olcutu)
# ===========================================================================


def test_HX05_restore_sonrasi_katmanlar_dogru(historian, paylasim, backend_args) -> None:
    dump = paylasim / f"{ONEK}-roundtrip.dump"
    _pg_dump(historian, dump, backend_args)

    hedef = f"{ONEK}_restored"
    _dusur(hedef)
    _admin(f'CREATE DATABASE "{hedef}" TEMPLATE template0')

    from app.services.backup_service import _parse_db_url, resolve_pg_binary

    d = _parse_db_url(PG_URL)
    ortam = os.environ.copy()
    if d["password"]:
        ortam["PGPASSWORD"] = d["password"]
    p = subprocess.run(
        [resolve_pg_binary("pg_restore"),
         "-h", d["host"], "-p", d["port"], "-U", d["user"], "-d", hedef,
         "--single-transaction", "--exit-on-error", "--no-owner", "--no-acl",
         str(dump)],
        env=ortam, capture_output=True, text=True, check=False,
    )
    assert p.returncode == 0, p.stderr[-2000:]

    def sayi(tablo: str) -> int:
        return int(_q(hedef, f"SELECT count(*) FROM {tablo}")[0][0])

    # Yapilandirma / olay verisi KORUNUR
    assert sayi("users") == 1
    assert sayi("devices") == 1
    assert sayi("gateways") == 1
    assert sayi("alarm_events") == 50
    assert sayi("system_events") == 80
    assert sayi("device_commands") == 30

    # Yuksek hacimli veri GIRMEZ
    assert sayi("telemetry") == 0
    assert sayi("outbox_events") == 0
    assert sayi("processed_messages") == 0

    # HISTORIAN GIRMEZ — arizanin tam olcutu
    assert sayi("telemetry_history") == 0
    assert sayi("telemetry_history_1m") == 0
    assert sayi("telemetry_history_1h") == 0

    # ...ama historian YAPISI saglam kalir
    assert int(_q(hedef,
        "SELECT count(*) FROM timescaledb_information.hypertables "
        "WHERE hypertable_name='telemetry_history'")[0][0]) == 1
    assert int(_q(hedef,
        "SELECT count(*) FROM timescaledb_information.continuous_aggregates "
        "WHERE view_name IN ('telemetry_history_1m','telemetry_history_1h')")[0][0]) == 2
    assert _q(hedef,
        "SELECT compression_enabled FROM timescaledb_information.hypertables "
        "WHERE hypertable_name='telemetry_history'")[0][0] is True
    assert _q(hedef,
        "SELECT time_interval FROM timescaledb_information.dimensions "
        "WHERE hypertable_name='telemetry_history'")[0][0].total_seconds() == 3600


# ===========================================================================
# HX06 — BOYUT INVARIANT'I
# ===========================================================================


def test_HX06_dump_historian_ile_birlikte_buyumez(
    historian, paylasim, backend_args
) -> None:
    """Historian buyudukce DR yedegi historian kadar buyumemeli.

    Sabit bir oran DAYATILMAZ (chunk katalog metadata'si dogal olarak biraz
    buyur). Olcut karsilastirmali: ayni veri artisinda tam yedek belirgin
    buyurken DR yedegi bunun kucuk bir kesri kadar buyumeli.
    """
    dr1 = paylasim / f"{ONEK}-dr1.dump"
    tam1 = paylasim / f"{ONEK}-tam1.dump"
    _pg_dump(historian, dr1, backend_args)
    _pg_dump(historian, tam1, [])

    _q(historian,
       "INSERT INTO telemetry_history "
       "SELECT (g%10)+1, 'sig'||(g%20), now() - ((g+200000) || ' seconds')::interval,"
       " random() FROM generate_series(1,120000) g")

    dr2 = paylasim / f"{ONEK}-dr2.dump"
    tam2 = paylasim / f"{ONEK}-tam2.dump"
    _pg_dump(historian, dr2, backend_args)
    _pg_dump(historian, tam2, [])

    tam_artis = tam2.stat().st_size - tam1.stat().st_size
    dr_artis = dr2.stat().st_size - dr1.stat().st_size

    assert tam_artis > 0, "tam yedek historian artisini yansitmadi — fixture suphelli"
    assert dr_artis < tam_artis * 0.2, (
        f"DR yedegi historian ile birlikte buyuyor: dr=+{dr_artis} tam=+{tam_artis}"
    )


# ===========================================================================
# HX07 — DISK TAHMINI ile GERCEK DUMP AYNI KUMEYE BAGLI  (B04)
# ===========================================================================


def _dump_veri_relationlari(dump: Path) -> set[str]:
    """Dump'ta GERCEKTEN verisi bulunan relation'lar (`schema.tablo`)."""
    out: set[str] = set()
    for satir in _toc(dump):
        if "TABLE DATA" not in satir:
            continue
        # "4693; 0 17858 TABLE DATA <sema> <tablo> <sahip>"
        parca = satir.split("TABLE DATA", 1)[1].split()
        if len(parca) >= 2:
            out.add(f"{parca[0]}.{parca[1]}")
    return out


def test_HX07_tahmin_dumpta_kalan_veriyi_DUSMEZ(
    historian, paylasim, backend_args
) -> None:
    """Tahminin dustugu baytlar, dump'in GERCEKTEN disladigi baytlari asamaz.

    YASANAN HATA BUYDU: tahmin `_hyper_%` kalibiyla historian'i dusuyor,
    pg_dump ise (sikistirilmis chunk'lar yuzunden) onu YAZIYORDU. Kapi
    "yer var" deyip geciyor, ardindan dump diski dolduruyordu.

    MUTASYON: dislama backup'tan kaldirilir ama tahmin hala duserse
    `dusulen > gercekten_dislanan` olur ve bu test DUSER.
    """
    dump = paylasim / f"{ONEK}-tahmin.dump"
    _pg_dump(historian, dump, backend_args)

    dumpta_veri_olan = _dump_veri_relationlari(dump)

    toplam_db = int(_q(historian, "SELECT pg_database_size(current_database())")[0][0])
    tahmin_core = int(_q(historian, _lib_sh_estimator_sql())[0][0])
    dusulen = toplam_db - tahmin_core

    # Dump'a VERISI GIRMEYEN her kullanici relation'inin baytlari.
    satirlar = _q(historian, """
        SELECT n.nspname || '.' || c.relname, pg_total_relation_size(c.oid)
          FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
         WHERE c.relkind IN ('r','p')
           AND n.nspname IN ('public','_timescaledb_internal')
    """)
    gercekten_dislanan = sum(
        int(bayt) for ad, bayt in satirlar if ad not in dumpta_veri_olan
    )

    assert dusulen <= gercekten_dislanan, (
        "disk tahmini, dump'in gercekten disladigindan FAZLASINI dusuyor "
        f"(dusulen={dusulen}, gercekten_dislanan={gercekten_dislanan}) — "
        "tahmin ile yedek sozlesmesi ayrismis"
    )
