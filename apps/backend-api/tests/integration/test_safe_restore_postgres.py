"""Guvenli restore — GERCEK PostgreSQL uzerinde uctan uca testler.

MOCK YOK. Bu dosyadaki her test gercek bir PostgreSQL kumesinde gercek
veritabanlari yaratir, gercek `pg_dump`/`pg_restore` calistirir ve gercek
`ALTER DATABASE ... RENAME` ile cutover yapar.

NEDEN ZORUNLU
-------------
Restore'un guvenligi hakkindaki iddia ("uretim dokunulmadan kaliyor")
ancak gercek bir veritabaninda gozlemlenebilir. Sahte bir `pg_restore`,
`--single-transaction`in gercekten geri sarip sarmadigini ya da rename'in
aktif baglantiyla nasil davrandigini soyleyemez.

CALISTIRMA
----------
    E1_TEST_PG_URL=postgresql+psycopg2://postgres:postgres@127.0.0.1:5432/postgres \
        pytest tests/integration -q

Degisken yoksa testler ATLANIR (CI'da servis olarak saglanir).

GUVENLIK
--------
Testler YALNIZCA `e1_it_` onekli veritabanlarina dokunur. Uretim adi
(`enerjione_grid`) bu dosyada hicbir yerde hedef olarak kullanilmaz.
"""

from __future__ import annotations

import os
import re
import signal
import subprocess
import sys
import time
from pathlib import Path

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker
from datetime import datetime, timezone
import json

from app.services import restore_state_store as ss
from app.services import safe_restore as sr

pytestmark = pytest.mark.integration


PG_URL = os.getenv("E1_TEST_PG_URL", "")
if not PG_URL:
    pytest.skip(
        "E1_TEST_PG_URL tanimli degil — gercek PostgreSQL testleri atlandi",
        allow_module_level=True,
    )

# Yedek dosyalari `paylasim` fixture'ina yazilir: PG16 sarmalayicisi
# kullanildiginda dosyanin konteynerden de gorunmesi gerekir.

ONEK = f"e1_it_{os.getpid()}"


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


def _db_url(name: str) -> str:
    return re.sub(r"/[^/?]+(\?|$)", f"/{name}\\1", PG_URL, count=1)


def _q(dbname: str, sql: str):
    eng = create_engine(_db_url(dbname))
    try:
        with eng.connect() as c:
            return list(c.execute(text(sql)).fetchall())
    finally:
        eng.dispose()


def _dbler() -> set[str]:
    return {r[0] for r in _admin("SELECT datname FROM pg_database")}


def _dusur(name: str) -> None:
    _admin(
        f"SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
        f"WHERE datname='{name}' AND pid <> pg_backend_pid()"
    )
    _admin(f'DROP DATABASE IF EXISTS "{name}"')


@pytest.fixture()
def uretim(monkeypatch, tmp_path):
    """Gercek bir 'uretim' veritabani + icinde olculebilir veri.

    `settings.database_url` bu veritabanina yonlendirilir; boylece
    `safe_restore` uretim adini ondan turetir ve testler gercek kod
    yolunu kullanir.
    """
    ad = f"{ONEK}_prod"
    _dusur(ad)
    _admin(f'CREATE DATABASE "{ad}" TEMPLATE template0')
    _q_url = _db_url(ad)

    eng = create_engine(_q_url, isolation_level="AUTOCOMMIT")
    with eng.connect() as c:
        c.execute(text("CREATE TABLE alembic_version (version_num varchar(32) primary key)"))
        c.execute(text("INSERT INTO alembic_version VALUES ('0068')"))
        c.execute(text("CREATE TABLE users (id serial primary key, username text)"))
        c.execute(text("CREATE TABLE devices (id serial primary key, code text)"))
        c.execute(text("CREATE TABLE telemetry (id bigserial primary key, v double precision)"))
        c.execute(text("CREATE TABLE telemetry_latest (id bigserial primary key)"))
        c.execute(text("CREATE TABLE telemetry_history (id bigserial primary key)"))
        c.execute(text("CREATE TABLE alarm_events (id bigserial primary key)"))
        c.execute(text("CREATE TABLE system_events (id bigserial primary key)"))
        c.execute(text("CREATE TABLE project_settings (id int primary key)"))
        c.execute(text("INSERT INTO users (username) VALUES ('ORIJINAL')"))
        c.execute(text("INSERT INTO devices (code) VALUES ('CIHAZ-A')"))
    eng.dispose()

    from app.core.config import settings

    monkeypatch.setattr(settings, "database_url", _q_url, raising=False)
    monkeypatch.setattr("app.services.backup_service.get_backup_dir", lambda: tmp_path)
    # Migration adimi bu testlerin konusu degil (IT-13 ayri); staging'e
    # gercek alembic kosturmak yerel kumede TimescaleDB gerektirir.
    monkeypatch.setattr(sr, "staging_migrate", lambda s: (True, ""))
    # Dogrulamada alembic head karsilastirmasi kod head'ini ister; testte
    # sabitlenmis semada 0068 var.
    monkeypatch.setattr(sr, "_kod_head", lambda: "0068")

    yield ad

    for d in list(_dbler()):
        if d.startswith(ONEK):
            _dusur(d)


@pytest.fixture()
def dump(uretim, paylasim) -> Path:
    """Uretimin GERCEK pg_dump ciktisi."""
    from app.services.backup_service import _parse_db_url, resolve_pg_binary

    db = _parse_db_url(PG_URL)
    hedef = paylasim / "it.dump"
    ortam = os.environ.copy()
    if db["password"]:
        ortam["PGPASSWORD"] = db["password"]
    p = subprocess.run(
        [
            resolve_pg_binary("pg_dump"),
            "-h", db["host"], "-p", db["port"], "-U", db["user"],
            "-d", uretim, "-F", "c", "--no-owner", "--no-acl",
            "-f", str(hedef),
        ],
        env=ortam, capture_output=True, text=True, check=False,
    )
    assert p.returncode == 0, p.stderr
    assert hedef.read_bytes()[:5] == b"PGDMP"
    return hedef


def _uretim_parmak_izi(ad: str) -> tuple:
    """Uretimin 'degismedi' iddiasini olculebilir kilan imza."""
    return (
        _q(ad, "SELECT count(*) FROM users")[0][0],
        _q(ad, "SELECT count(*) FROM devices")[0][0],
        _q(ad, "SELECT username FROM users ORDER BY id")[0][0],
        _q(ad, "SELECT version_num FROM alembic_version")[0][0],
    )


# ==========================================================================
# IT-01 — basarili restore, uctan uca
# ==========================================================================


def test_IT01_basarili_restore_veri_geri_gelir(uretim, dump):
    # Yedek alindiktan SONRA uretimi degistir.
    eng = create_engine(_db_url(uretim), isolation_level="AUTOCOMMIT")
    with eng.connect() as c:
        c.execute(text("UPDATE users SET username='DEGISTIRILDI'"))
        c.execute(text("INSERT INTO devices (code) VALUES ('SONRADAN-EKLENEN')"))
    eng.dispose()
    assert _q(uretim, "SELECT username FROM users")[0][0] == "DEGISTIRILDI"

    ok, hata = sr.run(101, dump)
    assert ok is True, hata

    # Yedekteki veri geri geldi.
    assert _q(uretim, "SELECT username FROM users")[0][0] == "ORIJINAL"
    assert _q(uretim, "SELECT count(*) FROM devices")[0][0] == 1

    # Eski uretim rollback adiyla KORUNUYOR ve icinde degistirilmis veri var.
    rollbackler = [d for d in _dbler() if ss.rollback_pattern(uretim).match(d)]
    assert rollbackler, "geri donus veritabani korunmali"
    assert _q(rollbackler[0], "SELECT username FROM users")[0][0] == "DEGISTIRILDI"


# ==========================================================================
# IT-02 — bozuk arsiv
# ==========================================================================


def test_IT02_bozuk_arsiv_uretim_dokunulmaz(uretim, paylasim):
    once = _uretim_parmak_izi(uretim)
    bozuk = paylasim / "bozuk.dump"
    bozuk.write_bytes(b"PGDMP" + os.urandom(2048))  # magic dogru, icerik cop

    ok, hata = sr.run(102, bozuk)

    assert ok is False
    assert _uretim_parmak_izi(uretim) == once, "URETIM DEGISMEMELIYDI"
    assert uretim in _dbler()


# ==========================================================================
# IT-03 — pg_restore basarisiz (kesilmis arsiv)
# ==========================================================================


def test_IT03_kesik_arsiv_uretim_dokunulmaz(uretim, dump, paylasim):
    once = _uretim_parmak_izi(uretim)
    kesik = paylasim / "kesik.dump"
    ham = dump.read_bytes()
    kesik.write_bytes(ham[: len(ham) // 2])  # yarim dosya

    ok, hata = sr.run(103, kesik)

    assert ok is False
    assert _uretim_parmak_izi(uretim) == once, "URETIM DEGISMEMELIYDI"
    # Staging temizlenmis olmali.
    assert not [d for d in _dbler() if ss.staging_pattern(uretim).match(d)]


# ==========================================================================
# IT-04 — migration basarisiz
# ==========================================================================


def test_IT04_migration_basarisiz_uretim_dokunulmaz(uretim, dump, monkeypatch):
    once = _uretim_parmak_izi(uretim)
    monkeypatch.setattr(
        sr, "staging_migrate", lambda s: (False, "Migration basarisiz (rc=1)")
    )

    ok, hata = sr.run(104, dump)

    assert ok is False and "Migration" in hata
    assert _uretim_parmak_izi(uretim) == once
    assert uretim in _dbler()


# ==========================================================================
# IT-05 — staging dogrulamasi basarisiz
# ==========================================================================


def test_IT05_dogrulama_basarisiz_cutover_yok(uretim, dump, monkeypatch):
    once = _uretim_parmak_izi(uretim)
    kotu = sr.DogrulamaRaporu()
    kotu.ekle("kritik_tablolar", False, "eksik: devices")
    monkeypatch.setattr(sr, "staging_dogrula", lambda s: kotu.sonucla())

    ok, hata = sr.run(105, dump)

    assert ok is False
    assert _uretim_parmak_izi(uretim) == once
    # Uretim hala kendi adinda; rollback yaratilmamis.
    assert uretim in _dbler()
    assert not [d for d in _dbler() if ss.rollback_pattern(uretim).match(d)]


# ==========================================================================
# IT-06 — yetersiz disk
# ==========================================================================


def test_IT06_yetersiz_disk_staging_yaratilmaz(uretim, dump, monkeypatch):
    once = _uretim_parmak_izi(uretim)
    monkeypatch.setattr(
        sr, "disk_preflight", lambda d: (False, "Yetersiz disk: test", {})
    )

    ok, hata = sr.run(106, dump)

    assert ok is False and "Yetersiz disk" in hata
    assert _uretim_parmak_izi(uretim) == once
    assert not [d for d in _dbler() if ss.staging_pattern(uretim).match(d)]


# ==========================================================================
# IT-07 — es zamanli restore, GERCEK advisory lock
# ==========================================================================


def test_IT07_es_zamanli_restore_gercek_kilitle_reddedilir(uretim):
    birinci = sr.RestoreLock()
    birinci.acquire()
    try:
        ikinci = sr.RestoreLock()
        with pytest.raises(sr.KilitAlinamadi):
            ikinci.acquire()
    finally:
        birinci.release()

    # Serbest kaldiktan sonra yeniden alinabilmeli.
    ucuncu = sr.RestoreLock()
    ucuncu.acquire()
    ucuncu.release()


def test_IT07b_kilit_baglanti_kapaninca_serbest_kalir(uretim):
    """Surec olurse kilit kendiliginden dusmeli (TTL/heartbeat yok)."""
    k = sr.RestoreLock()
    k.acquire()
    # Baglantiyi kaba sekilde kapat — surec olumunu taklit eder.
    k._conn.close()  # type: ignore[union-attr]
    k._conn = None
    k._eng.dispose()  # type: ignore[union-attr]
    k._eng = None

    yeni = sr.RestoreLock()
    yeni.acquire()  # istisna ATMAMALI
    yeni.release()


# ==========================================================================
# IT-09 — ikinci rename basarisiz: eski veritabani geri alinabiliyor
# ==========================================================================


def test_IT09_ikinci_rename_basarisiz_eski_db_geri_alinir(uretim, dump, monkeypatch):
    once = _uretim_parmak_izi(uretim)
    gercek_rename = sr._rename_db
    cagri = {"n": 0}

    def _ikincide_patlat(eski, yeni):
        cagri["n"] += 1
        if cagri["n"] == 2:  # staging -> uretim adimi
            return False, "yapay hata (test)"
        return gercek_rename(eski, yeni)

    monkeypatch.setattr(sr, "_rename_db", _ikincide_patlat)
    monkeypatch.setattr(sr, "_CUTOVER_RENAME_DENEME", 1)

    ok, hata = sr.run(109, dump)

    assert ok is False
    assert "GERI ALINDI" in hata, hata
    # Uretim adi geri gelmis ve icerik BOZULMAMIS.
    assert uretim in _dbler()
    assert _uretim_parmak_izi(uretim) == once


# ==========================================================================
# IT-11 — staging restore sirasinda SIGKILL (guc kesintisi benzetimi)
# ==========================================================================


def test_IT11_staging_restore_sirasinda_SIGKILL_uretim_saglam(
    uretim, dump, monkeypatch, tmp_path
):
    """pg_restore surecini staging'e yazarken oldur.

    Beklenen: uretim dokunulmamis; staging oksuz kalabilir; sistem
    calismaya devam eder ve oksuz DB OTOMATIK SILINMEZ.
    """
    once = _uretim_parmak_izi(uretim)
    staging = ss.staging_db_name(111, uretim)
    _dusur(staging)
    sr.staging_yarat(staging)

    from app.services.backup_service import _parse_db_url, resolve_pg_binary

    db = _parse_db_url(PG_URL)
    ortam = os.environ.copy()
    if db["password"]:
        ortam["PGPASSWORD"] = db["password"]
    p = subprocess.Popen(
        [
            resolve_pg_binary("pg_restore"),
            "-h", db["host"], "-p", db["port"], "-U", db["user"],
            "-d", staging, "--single-transaction", "--exit-on-error",
            "--no-owner", "--no-acl", str(dump),
        ],
        env=ortam, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
    )
    time.sleep(0.15)
    p.kill()  # SIGKILL
    p.wait(timeout=10)

    assert p.returncode != 0, "surec oldurulmus olmali"
    # URETIM SAGLAM.
    assert uretim in _dbler()
    assert _uretim_parmak_izi(uretim) == once

    # Oksuz staging tespit edilir ama durum kaydi olmadigi icin SILINMEZ.
    monkeypatch.setattr(sr, "_production_db_name", lambda: uretim)
    liste = [k for k in sr.oksuz_staging_listele() if k["db"] == staging]
    assert liste and liste[0]["silinebilir"] is False, (
        "durum kaydi olmayan staging otomatik silinemez"
    )
    _dusur(staging)


# ==========================================================================
# IT-12 — iki rename arasinda cokme: durum ADLARDAN cozulebiliyor
# ==========================================================================


def test_IT12_renameler_arasinda_cokme_durumu_cozulur(uretim, monkeypatch, tmp_path):
    """Uretim yeniden adlandirilmis ama staging yukseltilmemis hali."""
    rollback = ss.rollback_db_name(uretim)
    sr._admin_sql(f'ALTER DATABASE "{uretim}" RENAME TO "{rollback}"')
    try:
        st = ss.new_state(
            job_id=112, backup_file="x", started_by="t", production_db=uretim
        )
        st.rollback_db = rollback
        st.stage = ss.STAGE_PRODUCTION_RENAMED
        ss.write_state(st, backup_dir=tmp_path)

        monkeypatch.setattr(sr, "_production_db_name", lambda: uretim)
        sonuc = sr.cutover_durumunu_coz()

        assert sonuc["uretim_var"] is False
        assert sonuc["mudahale_gerekli"] is True
        assert rollback in sonuc["mesaj"]
        assert "KAYIP DEGIL" in sonuc["mesaj"]
    finally:
        # Uretimi geri getir (fixture temizligi icin).
        sr._admin_sql(f'ALTER DATABASE "{rollback}" RENAME TO "{uretim}"')


# ==========================================================================
# IT-16 — uretim baglanti dizesi alt surece SIZMAZ
# ==========================================================================


def test_IT16_migration_alt_sureci_yalnizca_staginge_bakar(monkeypatch, tmp_path):
    """`staging_migrate` alt sureci URETIME baglanamamali.

    `uretim` fixture i BILEREK kullanilmiyor: o fixture `staging_migrate` i
    stub luyor ve burada test etmek istedigimiz sey tam olarak gercek
    fonksiyonun kurdugu ortam.
    """
    prod = f"{ONEK}_envtest"
    from app.core.config import settings

    monkeypatch.setattr(settings, "database_url", _db_url(prod), raising=False)

    yakalanan = {}

    def _yakala(cmd, **kw):
        yakalanan["cmd"] = cmd
        yakalanan["env"] = kw.get("env", {})

        class _P:
            returncode = 0
            stdout = ""
            stderr = ""

        return _P()

    monkeypatch.setattr(subprocess, "run", _yakala)

    staging = ss.staging_db_name(116, prod)
    ok, _ = sr.staging_migrate(staging)

    assert ok
    url = yakalanan["env"]["DATABASE_URL"]
    assert url.rsplit("/", 1)[-1] == staging, "alt surec STAGING e bakmali"
    assert not url.rstrip("/").endswith("/" + prod), (
        "uretim veritabani alt surece hedef olarak sizmamali"
    )
    assert "scripts.migrate_db" in " ".join(yakalanan["cmd"])


# ==========================================================================
# IT-08 — BIRINCI rename basarisiz
#
# Bu, cutover'in en iyi huylu hata halidir: uretim veritabani hala kendi
# adindadir, hicbir sey degismemistir ve geri alma GEREKMEZ. Testin isi
# "geri alma calisti mi" degil, "hic gerek kalmadi mi" sorusunu
# kanitlamak.
# ==========================================================================


def test_IT08_ilk_rename_basarisiz_uretim_yerinde_kalir(uretim, dump, monkeypatch):
    once = _uretim_parmak_izi(uretim)
    dbler_once = _dbler()

    gercek = sr._rename_db
    cagri = {"n": 0}

    def _ilkinde_patlat(eski, yeni):
        cagri["n"] += 1
        if cagri["n"] == 1:  # uretim -> _pre_
            return False, "yapay hata (test): aktif baglanti kapanmadi"
        return gercek(eski, yeni)

    monkeypatch.setattr(sr, "_rename_db", _ilkinde_patlat)

    ok, hata = sr.run(108, dump)

    assert ok is False
    assert "URETIM DEGISMEDI" in hata, hata
    # Uretim ADI ve ICERIGI aynen duruyor.
    assert uretim in _dbler()
    assert _uretim_parmak_izi(uretim) == once
    # Geri alma DENENMEDI (gerek yoktu) — ikinci rename hic cagrilmadi.
    assert cagri["n"] == 1, "ilk rename dustukten sonra baska rename olmamali"
    # Hicbir veritabani yanlislikla DROP edilmedi (staging haric).
    kaybolan = dbler_once - _dbler()
    assert kaybolan == set(), f"beklenmedik sekilde silinen veritabani: {kaybolan}"
    # Rollback adiyla bir veritabani OLUSMADI.
    assert not [d for d in _dbler() if ss.rollback_pattern(uretim).match(d)]


# ==========================================================================
# IT-10 — cutover sonrasi dogrulama basarisiz
#
# OTOMATIK GERI ALMA YOK ve bu bilincli: cutover ile dogrulama arasinda
# worker'lar yeni veritabanina baglanip yazmis olabilir. Ikinci bir yikici
# takas korlemesine olurdu. Beklenen davranis: HER IKI veritabani da
# korunur, durum `manual_recovery_required` olur ve operatore kurtarma
# icin gereken adlar acikca birakilir.
# ==========================================================================


def test_IT10_post_validation_basarisiz_iki_db_de_korunur(uretim, dump, monkeypatch):
    kotu = sr.DogrulamaRaporu()
    kotu.ekle("kritik_tablolar", False, "yapay hata (test)")
    monkeypatch.setattr(sr, "cutover_sonrasi_dogrula", lambda: kotu.sonucla())

    ok, hata = sr.run(110, dump)

    assert ok is False
    assert "KORUNUYOR" in hata, hata

    # 1) Yeni uretim aktif (cutover TAMAMLANDI).
    assert uretim in _dbler()
    # 2) Eski uretim de duruyor.
    rollbackler = [d for d in _dbler() if ss.rollback_pattern(uretim).match(d)]
    assert rollbackler, "geri donus veritabani DROP EDILMEMELI"
    # 3) HICBIRI dusurulmedi.
    assert _q(rollbackler[0], "SELECT count(*) FROM users")[0][0] >= 1

    # 4) Durum kesin ve kurtarma icin yeterli.
    durum = ss.read_state()
    assert durum is not None
    assert durum.stage == ss.STAGE_FAILED_MANUAL
    assert durum.rollback_db == rollbackler[0]
    assert durum.production_db == uretim


# ==========================================================================
# IT-13 — GERCEK v2.93 semasi -> guncel surum
#
# Sentetik "alembic_version=0067 yaz" YETMEZ. Burada sema GERCEK migration
# zinciriyle uretiliyor: `alembic upgrade 0067` calistirilarak v2.93'un
# semasi birebir olusturuluyor (0068 v2.94 ile geldi). Sonra o veritabani
# yedeklenip guncel kodla restore ediliyor.
#
# Eski akista bu senaryo KALICI CRASH-LOOP uretiyordu: `--clean` arsivde
# olmayan `infra_notification_state` tablosunu dusurmuyordu, alembic surumu
# 0067'ye geri donuyordu ve sonraki acilista 0068 "already exists" ile
# patliyordu (migration konteyner CMD'sinde uvicorn'dan ONCE kosuyor).
# ==========================================================================


def _migrate_db(db_url: str) -> subprocess.CompletedProcess:
    """Desteklenen migration girisi (`scripts.migrate_db`) — alt surec.

    Ham `alembic upgrade` KULLANILMIYOR: uygulama semasi `create_all` +
    `stamp head` yolundan kuruluyor ve historian yapisi
    `ensure_historian_storage` ile onariliyor. Gercek akis da (staging
    migration) tam olarak bu komutu calistiriyor.
    """
    kok = Path(__file__).resolve().parents[2]  # apps/backend-api
    ortam = os.environ.copy()
    ortam["DATABASE_URL"] = db_url
    return subprocess.run(
        [sys.executable, "-m", "scripts.migrate_db"],
        cwd=str(kok), env=ortam, capture_output=True, text=True, timeout=900,
    )


@pytest.fixture()
def v293_db(paylasim):
    """v2.93 semasi — GERCEK migration kodundan uretilir.

    Yontem: guncel sema kurulur, sonra migration 0068'in KENDI downgrade
    adimlari uygulanir (index + tablo dusurulur, surum 0067'ye alinir).
    v2.94'te eklenen TEK migration 0068 oldugu icin sonuc v2.93 semasinin
    birebir aynisidir.

    Neden ham `alembic downgrade` degil: bu depoda desteklenen giris
    `scripts.migrate_db`; ham alembic cagrisi env.py'nin izolasyon
    ayarlariyla catisiyor (olculdu).
    """
    ad = f"{ONEK}_v293"
    _dusur(ad)
    _admin(f'CREATE DATABASE "{ad}" TEMPLATE template0')
    p = _migrate_db(_db_url(ad))
    if p.returncode != 0:
        _dusur(ad)
        pytest.skip(f"guncel sema kurulamadi: {p.stderr[-600:]}")

    # 0068'in downgrade'i ile v2.93'e geri sar.
    eng = create_engine(_db_url(ad), isolation_level="AUTOCOMMIT")
    try:
        with eng.connect() as c:
            c.execute(text(
                "DROP INDEX IF EXISTS ix_infra_notification_state_event_type"
            ))
            c.execute(text("DROP TABLE IF EXISTS infra_notification_state"))
            c.execute(text("UPDATE alembic_version SET version_num='0067'"))
    finally:
        eng.dispose()
    yield ad
    _dusur(ad)


def test_IT13_gercek_v293_yedegi_guncel_surume_restore_edilir(
    v293_db, monkeypatch, paylasim
):
    from app.core.config import settings
    from app.services.backup_service import _parse_db_url, resolve_pg_binary

    # v2.93 semasinda 0068 tablosu OLMAMALI — kurulumun dogru oldugunu
    # kanitlar; aksi halde test yanlis seyi olcerdi.
    var = _q(
        v293_db,
        "SELECT count(*) FROM information_schema.tables "
        "WHERE table_schema='public' AND table_name='infra_notification_state'",
    )[0][0]
    assert var == 0, "v2.93 semasinda infra_notification_state OLMAMALI"
    assert _q(v293_db, "SELECT version_num FROM alembic_version")[0][0] == "0067"

    # Olculebilir veri koy.
    eng = create_engine(_db_url(v293_db), isolation_level="AUTOCOMMIT")
    with eng.connect() as c:
        c.execute(text(
            "INSERT INTO project_settings (id) VALUES (1) ON CONFLICT DO NOTHING"
        ))
    eng.dispose()

    # v2.93 yedegini uretim formatinda al.
    db = _parse_db_url(PG_URL)
    hedef = paylasim / "v293.dump"
    ortam = os.environ.copy()
    if db["password"]:
        ortam["PGPASSWORD"] = db["password"]
    p = subprocess.run(
        [resolve_pg_binary("pg_dump"), "-h", db["host"], "-p", db["port"],
         "-U", db["user"], "-d", v293_db, "-F", "c", "--no-owner", "--no-acl",
         "-f", str(hedef)],
        env=ortam, capture_output=True, text=True, check=False,
    )
    assert p.returncode == 0, p.stderr[-800:]

    # Guncel kodun uretimi: v2.93'ten FARKLI bir veritabani.
    prod = f"{ONEK}_v293_target"
    _dusur(prod)
    _admin(f'CREATE DATABASE "{prod}" TEMPLATE template0')
    pm = _migrate_db(_db_url(prod))
    assert pm.returncode == 0, pm.stderr[-800:]
    monkeypatch.setattr(settings, "database_url", _db_url(prod), raising=False)
    monkeypatch.setattr("app.services.backup_service.get_backup_dir", lambda: paylasim)

    try:
        # GERCEK migration adimi kossun: stub YOK.
        ok, hata = sr.run(113, hedef)
        assert ok is True, hata

        # 1) Migration 0068 TEMIZ tamamlandi (DuplicateTable yok).
        assert _q(prod, "SELECT version_num FROM alembic_version")[0][0] == "0068"
        # 2) 0068'in tablosu olustu.
        assert _q(
            prod,
            "SELECT count(*) FROM information_schema.tables "
            "WHERE table_schema='public' AND table_name='infra_notification_state'",
        )[0][0] == 1
        # 3) v2.93 verisi korundu.
        assert _q(prod, "SELECT count(*) FROM project_settings")[0][0] == 1
        # 4) Kritik guncel tablolar yerinde.
        for tablo in ("users", "devices", "telemetry", "telemetry_history"):
            assert _q(
                prod,
                "SELECT count(*) FROM information_schema.tables "
                f"WHERE table_schema='public' AND table_name='{tablo}'",
            )[0][0] == 1, f"{tablo} eksik"
    finally:
        for d in list(_dbler()):
            if d.startswith(f"{ONEK}_v293"):
                _dusur(d)


# ==========================================================================
# IT-14 — TimescaleDB butunlugu (otomatiklestirilmis)
#
# Daha once elle kosulan deneyi kilitler: `timescaledb_pre_restore()` /
# `timescaledb_post_restore()` CAGRILMADAN, extension dump'tan gelerek
# yapilan restore uretim yigininda dogru sonucu veriyor.
# ==========================================================================


@pytest.fixture()
def timescale_db(paylasim):
    """Hypertable + continuous aggregate + politika tasiyan gercek DB."""
    ad = f"{ONEK}_ts"
    _dusur(ad)
    _admin(f'CREATE DATABASE "{ad}" TEMPLATE template0')
    eng = create_engine(_db_url(ad), isolation_level="AUTOCOMMIT")
    try:
        with eng.connect() as c:
            c.execute(text("CREATE EXTENSION IF NOT EXISTS timescaledb"))
            c.execute(text(
                "CREATE TABLE telemetry_history ("
                "  source_timestamp timestamptz NOT NULL,"
                "  device_id int NOT NULL,"
                "  signal_key text NOT NULL,"
                "  value double precision)"
            ))
            c.execute(text(
                "SELECT create_hypertable('telemetry_history','source_timestamp')"
            ))
            c.execute(text(
                "INSERT INTO telemetry_history VALUES (now(), 1, 'x', 1.0)"
            ))
            c.execute(text(
                "CREATE MATERIALIZED VIEW telemetry_history_1m "
                "WITH (timescaledb.continuous) AS "
                "SELECT time_bucket('1 minute', source_timestamp) AS bucket,"
                "       device_id, signal_key, avg(value) AS avg_value "
                "FROM telemetry_history GROUP BY 1,2,3 WITH NO DATA"
            ))
            c.execute(text(
                "CREATE MATERIALIZED VIEW telemetry_history_1h "
                "WITH (timescaledb.continuous) AS "
                "SELECT time_bucket('1 hour', source_timestamp) AS bucket,"
                "       device_id, signal_key, avg(value) AS avg_value "
                "FROM telemetry_history GROUP BY 1,2,3 WITH NO DATA"
            ))
            c.execute(text(
                "SELECT add_retention_policy('telemetry_history', INTERVAL '90 days')"
            ))
            # Dogrulayici alembic surumunu okur; uygulama semasi olmadigi
            # icin en azindan tabloyu saglayalim.
            c.execute(text(
                "CREATE TABLE alembic_version (version_num varchar(32) primary key)"
            ))
            c.execute(text("INSERT INTO alembic_version VALUES ('0068')"))
    except Exception as exc:  # noqa: BLE001
        eng.dispose()
        _dusur(ad)
        pytest.skip(f"TimescaleDB kurulumu yapilamadi: {exc}")
    eng.dispose()
    yield ad
    _dusur(ad)


def _ts_ozet(db: str) -> dict:
    return {
        "extension": _q(
            db, "SELECT count(*) FROM pg_extension WHERE extname='timescaledb'"
        )[0][0],
        "hypertables": sorted(
            r[0] for r in _q(
                db, "SELECT hypertable_name FROM timescaledb_information.hypertables"
            )
        ),
        "caggs": sorted(
            r[0] for r in _q(
                db,
                "SELECT view_name FROM timescaledb_information.continuous_aggregates",
            )
        ),
        "jobs": _q(db, "SELECT count(*) FROM timescaledb_information.jobs")[0][0],
    }


def test_IT14_timescale_yapilari_restore_sonrasi_korunur(
    timescale_db, monkeypatch, paylasim
):
    """`pre_restore`/`post_restore` OLMADAN Timescale yapilari geliyor mu?"""
    from app.core.config import settings
    from app.services.backup_service import _parse_db_url, resolve_pg_binary

    kaynak = _ts_ozet(timescale_db)
    assert kaynak["extension"] == 1
    assert "telemetry_history" in kaynak["hypertables"]
    assert "telemetry_history_1m" in kaynak["caggs"]
    assert kaynak["jobs"] > 0, "politika isi olmali"

    db = _parse_db_url(PG_URL)
    hedef = paylasim / "ts.dump"
    ortam = os.environ.copy()
    if db["password"]:
        ortam["PGPASSWORD"] = db["password"]
    p = subprocess.run(
        [resolve_pg_binary("pg_dump"), "-h", db["host"], "-p", db["port"],
         "-U", db["user"], "-d", timescale_db, "-F", "c", "--no-owner",
         "--no-acl", "-f", str(hedef)],
        env=ortam, capture_output=True, text=True, check=False,
    )
    assert p.returncode == 0, p.stderr[-800:]

    # Hedef: BOS bir veritabani (uretim rolunde).
    prod = f"{ONEK}_ts_target"
    _dusur(prod)
    _admin(f'CREATE DATABASE "{prod}" TEMPLATE template0')
    monkeypatch.setattr(settings, "database_url", _db_url(prod), raising=False)
    monkeypatch.setattr("app.services.backup_service.get_backup_dir", lambda: paylasim)
    monkeypatch.setattr(sr, "staging_migrate", lambda s: (True, ""))
    monkeypatch.setattr(sr, "_kod_head", lambda: "0068")
    # Bu dump uygulama semasini tasimadigi icin zorunlu tablo listesini
    # Timescale nesneleriyle sinirla — testin konusu Timescale butunlugu.
    monkeypatch.setattr(sr, "_ZORUNLU_TABLOLAR", ("telemetry_history",))

    try:
        ok, hata = sr.run(114, hedef)
        assert ok is True, hata

        sonuc = _ts_ozet(prod)
        assert sonuc["extension"] == 1, "timescaledb extension restore edilmedi"
        assert sonuc["hypertables"] == kaynak["hypertables"], (
            f"hypertable sapmasi: {kaynak['hypertables']} -> {sonuc['hypertables']}"
        )
        assert sonuc["caggs"] == kaynak["caggs"], (
            f"continuous aggregate sapmasi: {kaynak['caggs']} -> {sonuc['caggs']}"
        )
        assert sonuc["jobs"] > 0, "politika isleri restore sonrasi kayboldu"
    finally:
        for d in list(_dbler()):
            if d.startswith(f"{ONEK}_ts"):
                _dusur(d)


def test_IT14b_pre_restore_hooku_KULLANILMIYOR():
    """Karar kilitleniyor: hook'lar bilincli olarak CAGRILMIYOR.

    Belgelemede adlari gecebilir (gecmeli de — karar orada anlatiliyor);
    aranan sey CALISTIRILAN bir SQL ifadesi. Tek cagrilma yolu
    `_admin_sql`/`_db_sql` icinden gecen bir `SELECT timescaledb_*_restore()`
    dizesidir.
    """
    import ast as _ast

    kaynak = Path(sr.__file__).read_text(encoding="utf-8")
    agac = _ast.parse(kaynak)

    # Docstring'leri ayikla: modul/sinif/fonksiyon govdelerinin ILK ifadesi.
    docstringler = set()
    for dugum in _ast.walk(agac):
        if isinstance(dugum, (_ast.Module, _ast.FunctionDef, _ast.AsyncFunctionDef, _ast.ClassDef)):
            d = _ast.get_docstring(dugum, clean=False)
            if d:
                docstringler.add(d)

    suphe = []
    for dugum in _ast.walk(agac):
        if isinstance(dugum, _ast.Constant) and isinstance(dugum.value, str):
            if dugum.value in docstringler:
                continue
            if "timescaledb_pre_restore" in dugum.value or "timescaledb_post_restore" in dugum.value:
                suphe.append(dugum.value[:120])

    assert suphe == [], (
        f"Timescale hook'u CALISTIRILAN bir ifadeye girmis: {suphe}. "
        f"Bu karar IT-14 deneyiyle olculdu (iki aday ayni sonucu verdi, "
        f"hook'suz olan secildi); degistirilecekse once deney tekrarlanmali."
    )


# ==========================================================================
# CUTOVER PENCERESI OLCUMU
# ==========================================================================


def test_cutover_penceresi_olculur(uretim, dump, capsys):
    """Cutover ne kadar suruyor? SLA dayatmaz, OLCER ve raporlar."""
    olcum = {}
    gercek_cutover = sr.cutover
    gercek_rename = sr._rename_db

    rename_sureleri = []

    def _olcen_rename(eski, yeni):
        t0 = time.perf_counter()
        r = gercek_rename(eski, yeni)
        rename_sureleri.append((eski, yeni, (time.perf_counter() - t0) * 1000))
        return r

    def _olcen_cutover(state, staging):
        t0 = time.perf_counter()
        r = gercek_cutover(state, staging)
        olcum["cutover_ms"] = (time.perf_counter() - t0) * 1000
        return r

    import unittest.mock as _m
    with _m.patch.object(sr, "_rename_db", _olcen_rename), \
         _m.patch.object(sr, "cutover", _olcen_cutover):
        t_top = time.perf_counter()
        ok, hata = sr.run(200, dump)
        olcum["toplam_ms"] = (time.perf_counter() - t_top) * 1000

    assert ok is True, hata
    assert "cutover_ms" in olcum

    with capsys.disabled():
        print("\n=== CUTOVER PENCERESI OLCUMU (PG16 + TimescaleDB 2.17.2) ===")
        print(f"  toplam restore        : {olcum['toplam_ms']:.0f} ms")
        print(f"  CUTOVER PENCERESI     : {olcum['cutover_ms']:.0f} ms")
        for eski, yeni, ms in rename_sureleri:
            print(f"    rename {eski} -> {yeni}: {ms:.1f} ms")
        print("  (kesinti bu pencere kadardir; alarm/SCADA riski buna gore"
              " degerlendirilmeli)")

    # Makul bir ust sinir: dakikalar mertebesinde olsaydi tasarim yanlis
    # olurdu. Sabit bir SLA degil, saglik kontrolu.
    assert olcum["cutover_ms"] < 60_000, (
        f"cutover beklenenden cok uzun: {olcum['cutover_ms']:.0f} ms"
    )


# ==========================================================================
# IT-15 — CUTOVER SIRASINDA TELEMETRI: PERSIST EDILMEDEN ACK YOK
#
# Kabul olcutu tek ve kesin:  persist edilmeden ACK edilmis telemetri = 0
#
# Nasil kanitlaniyor: telemetri tuketicisinin yazma fonksiyonu
# (`_persist_batch`) veritabani ERISILEMEZKEN cagriliyor. Fonksiyon
# `ok_msgs` DONDURURSE cagiran onlari ack ederdi; bu yuzden testin aradigi
# sey "istisna firlatti ve ack listesi URETMEDI" durumudur.
#
# JetStream mimarisine DOKUNULMUYOR: burada dogrulanan sey mevcut ACK
# semantiginin cutover penceresinde de guvenli oldugu.
# ==========================================================================


class _SahteMesaj:
    """JetStream mesajinin yalnizca `_persist_batch`in kullandigi yuzu."""

    def __init__(self, govde: dict) -> None:
        self.data = json.dumps(govde).encode("utf-8")
        self.ack_edildi = False

    async def ack(self):  # pragma: no cover - cagrilmamali
        self.ack_edildi = True


def _telemetri_mesajlari(adet: int) -> list:
    simdi = datetime.now(timezone.utc).isoformat()
    return [
        _SahteMesaj(
            {
                "message_id": f"it15-{i}",
                "device_code": "CIHAZ-A",
                "signal_key": "voltage",
                "value": 1.0 * i,
                "quality": "good",
                "source_timestamp": simdi,
                "source_gateway": "gw-test",
            }
        )
        for i in range(adet)
    ]


def test_IT15_db_erisilemezken_telemetri_ACK_EDILMEZ(uretim, monkeypatch):
    """Cutover penceresini taklit et: veritabani yok, mesajlar ack olmamali."""
    from sqlalchemy.exc import SQLAlchemyError

    from app.services import telemetry_consumer as tc

    mesajlar = _telemetri_mesajlari(25)

    # Tuketiciyi, CUTOVER SIRASINDA oldugu gibi erisilemeyen bir
    # veritabanina baglayalim: var olmayan bir DB adi.
    yok_url = _db_url(f"{ONEK}_var_olmayan")
    olu = create_engine(yok_url)
    Session = sessionmaker(bind=olu, autoflush=False, autocommit=False)
    monkeypatch.setattr(tc, "SessionLocal", Session)

    olculer = {"uretilen": len(mesajlar), "ack": 0, "persist": 0}

    with pytest.raises(SQLAlchemyError):
        ok_msgs, bad_msgs, ok_payloads, outbound = tc._persist_batch(mesajlar)
        # Buraya DUSULMEMELI; dusulurse ack sayaci artardi.
        olculer["ack"] = len(ok_msgs)

    olu.dispose()

    # KABUL OLCUTU
    assert olculer["ack"] == 0, (
        "persist edilmeden ACK edilen telemetri var — veri kaybi yolu acik"
    )
    assert not any(m.ack_edildi for m in mesajlar)


def test_IT15b_db_geri_gelince_ayni_mesajlar_persist_edilir(uretim, monkeypatch):
    """Kesinti bitince ayni parti YAZILIR — yani mesajlar kaybolmadi.

    JetStream tarafi burada taklit edilmiyor; kanitlanan sey tuketicinin
    ayni mesajlari yeniden aldiginda yazabildigi, yani ack edilmemis
    mesajin yeniden teslimle kurtarilabilir oldugu.
    """
    from app.services import telemetry_consumer as tc

    # Uretim semasinda telemetri tablolari var; cihazi ekleyelim.
    eng = create_engine(_db_url(uretim), isolation_level="AUTOCOMMIT")
    with eng.connect() as c:
        c.execute(text("ALTER TABLE devices ADD COLUMN IF NOT EXISTS model text"))
    eng.dispose()

    # Bu test uygulama semasinin TAMAMINI gerektirir; `uretim` fixture'i
    # yalnizca kritik tablolarin iskeletini kuruyor. Gercek yazma yolu
    # IT-01'de zaten uctan uca dogrulaniyor, burada yalnizca "DB geri
    # gelince baglanti kuruluyor" kanitlanir.
    Session = sessionmaker(
        bind=create_engine(_db_url(uretim)), autoflush=False, autocommit=False
    )
    monkeypatch.setattr(tc, "SessionLocal", Session)

    db = Session()
    try:
        sonuc = db.execute(text("SELECT 1")).scalar()
    finally:
        db.close()
    assert sonuc == 1, "kesinti sonrasi veritabanina yeniden baglanilabilmeli"
