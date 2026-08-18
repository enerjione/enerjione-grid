"""Migration testleri YANLIS veritabanina kosamaz — fail-closed kanit.

YASANMIS OLAY
-------------
0071 gelistirilirken bir migration testi `alembic upgrade head`i
GELISTIRICININ veritabanina (`localhost:5432/enerjione_grid`) kosturdu ve
onu 0063'ten 0071'e tasidi. Sebep: `alembic_migrations/env.py`
`sqlalchemy.url`'i KOSULSUZ `settings.database_url` ile ezer, yani testin
`cfg.set_main_option("sqlalchemy.url", ...)` cagrisi sessizce yok sayilir.

Bu dosya, o hatanin bir daha SESSIZCE olmasini imkansiz kilar. Guard'in
kendisi `tests/integration/pg_target` icinde.

KAPSAM NOTU: `env.py` mimarisi BU TASKTA DEGISTIRILMEZ (Alembic Schema
Authority ayri bir is). Burada yalnizca test tarafi izole edilir.
"""

from __future__ import annotations

import logging

import pytest
from sqlalchemy import create_engine, inspect, text

import app.models  # noqa: F401
from app.db.base import Base
from tests.integration import pg_target
from tests.integration.pg_target import UnsafeMigrationTarget

pytestmark = pytest.mark.integration

if not pg_target.pg_url():
    pytest.skip("E1_TEST_PG_URL yok", allow_module_level=True)


# --------------------------------------------------------------------------
# Yardimcilar
# --------------------------------------------------------------------------
def _olustur(ad: str) -> None:
    yonetim = create_engine(pg_target.pg_url(), isolation_level="AUTOCOMMIT")
    with yonetim.connect() as c:
        c.execute(text(f'DROP DATABASE IF EXISTS "{ad}"'))
        c.execute(text(f'CREATE DATABASE "{ad}" TEMPLATE template0'))
    yonetim.dispose()
    pg_target.kaydet_olusturuldu(ad)


def _sil(ad: str) -> None:
    yonetim = create_engine(pg_target.pg_url(), isolation_level="AUTOCOMMIT")
    with yonetim.connect() as c:
        c.execute(text(
            f"SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
            f"WHERE datname='{ad}' AND pid <> pg_backend_pid()"
        ))
        c.execute(text(f'DROP DATABASE IF EXISTS "{ad}"'))
    yonetim.dispose()
    pg_target.unut(ad)


@pytest.fixture()
def gecici_db():
    ad = pg_target.yeni_db_adi("alembic_iso")
    _olustur(ad)
    try:
        yield ad
    finally:
        _sil(ad)


def _tablo_var_mi(url: str, ad: str) -> bool:
    eng = create_engine(url)
    try:
        return inspect(eng).has_table(ad)
    finally:
        eng.dispose()


def _revizyon(url: str) -> str | None:
    eng = create_engine(url)
    try:
        with eng.connect() as c:
            if not inspect(c).has_table("alembic_version"):
                return None
            return c.execute(text("select version_num from alembic_version")).scalar()
    finally:
        eng.dispose()


# --------------------------------------------------------------------------
# A01 — acikca izole edilmis PG16/Timescale hedefinde migration KOSAR
# --------------------------------------------------------------------------
def test_A01_explicit_izole_hedefte_migration_PASS(gecici_db, monkeypatch):
    from alembic import command

    url = pg_target.url_for(gecici_db)
    eng = create_engine(url)
    Base.metadata.create_all(eng)
    eng.dispose()

    cfg = pg_target.alembic_config(url, monkeypatch)
    command.stamp(cfg, "0070")
    command.upgrade(cfg, "head")

    assert _revizyon(url) == "0071"
    assert _tablo_var_mi(url, "unknown_device_telemetry")


# --------------------------------------------------------------------------
# A02 / A07 — gelistirici DB'si hedef gosterilirse MIGRATION HIC BASLAMAZ
# --------------------------------------------------------------------------
def test_A02_gelistirici_DB_hedefi_migration_ONCESI_FAIL():
    """Guard, Alembic'e HIC ulasmadan reddetmeli."""
    from app.core.config import settings

    with pytest.raises(UnsafeMigrationTarget):
        pg_target.hedefi_dogrula(settings.database_url)


@pytest.mark.parametrize(
    "kotu_ad",
    ["enerjione_grid", "postgres", "template1", "uretim_db", "e1_test_uydurma"],
)
def test_A02b_guvensiz_hedefler_REDDEDILIR(kotu_ad):
    """Isim deseni TEK BASINA yeterli degil: `e1_test_uydurma` dogru oneki
    tasir ama bu kosuda BIZ olusturmadik — yine reddedilir."""
    with pytest.raises(UnsafeMigrationTarget):
        pg_target.hedefi_dogrula(pg_target.url_for(kotu_ad))


def test_A07_migration_testi_gelistirici_semasina_DOKUNAMAZ(monkeypatch):
    """Guard tetiklenince Alembic komutu CAGRILMAMIS olmali."""
    from app.core.config import settings

    cagrildi = {"n": 0}

    def upgrade_spy(*_a, **_k):
        cagrildi["n"] += 1

    import alembic.command as _cmd

    monkeypatch.setattr(_cmd, "upgrade", upgrade_spy)

    with pytest.raises(UnsafeMigrationTarget):
        cfg = pg_target.alembic_config(settings.database_url, monkeypatch)
        _cmd.upgrade(cfg, "head")

    assert cagrildi["n"] == 0, "guard'a ragmen migration calisti"


def test_A02c_env_degiskeni_yoksa_FAIL_CLOSED(monkeypatch):
    """Ortulu varsayilan yok: `E1_TEST_PG_URL` yoksa hicbir hedef gecerli degil."""
    monkeypatch.delenv(pg_target.ENV_ADI, raising=False)
    with pytest.raises(UnsafeMigrationTarget):
        pg_target.hedefi_dogrula("postgresql+psycopg2://x@127.0.0.1:15433/e1_test_x")


# --------------------------------------------------------------------------
# A03 / A04 / A05 — migration yollari
# --------------------------------------------------------------------------
def test_A03_create_all_stamp0070_upgrade0071_PASS(gecici_db, monkeypatch):
    """Temiz kurulum/restore yolu: tablo MODELDEN gelmis, 0071 yine kosar."""
    from alembic import command

    url = pg_target.url_for(gecici_db)
    eng = create_engine(url)
    Base.metadata.create_all(eng)  # tablo ZATEN var
    eng.dispose()

    cfg = pg_target.alembic_config(url, monkeypatch)
    command.stamp(cfg, "0070")
    command.upgrade(cfg, "head")  # korumasiz create_table burada patlardi

    assert _revizyon(url) == "0071"
    assert _tablo_var_mi(url, "unknown_device_telemetry")


def test_A04_0070_semasindan_0071_PASS(gecici_db, monkeypatch):
    """Yukseltme yolu: 0070'te tablo YOK, 0071 onu KURAR."""
    from alembic import command

    url = pg_target.url_for(gecici_db)
    eng = create_engine(url)
    Base.metadata.create_all(eng)
    with eng.connect() as c:
        c.execute(text('DROP TABLE IF EXISTS "unknown_device_telemetry"'))
        c.commit()
    eng.dispose()

    cfg = pg_target.alembic_config(url, monkeypatch)
    command.stamp(cfg, "0070")
    assert not _tablo_var_mi(url, "unknown_device_telemetry")

    command.upgrade(cfg, "head")
    assert _tablo_var_mi(url, "unknown_device_telemetry")


def test_A05_0071_tekrar_kosmasi_idempotent(gecici_db, monkeypatch):
    """0071'e ikinci kez `upgrade head` no-op olmali; `downgrade` + tekrar
    `upgrade` de temiz calismali."""
    from alembic import command

    url = pg_target.url_for(gecici_db)
    eng = create_engine(url)
    Base.metadata.create_all(eng)
    eng.dispose()

    cfg = pg_target.alembic_config(url, monkeypatch)
    command.stamp(cfg, "0070")
    command.upgrade(cfg, "head")
    command.upgrade(cfg, "head")  # no-op
    assert _revizyon(url) == "0071"
    assert _tablo_var_mi(url, "unknown_device_telemetry")

    command.downgrade(cfg, "0070")
    assert not _tablo_var_mi(url, "unknown_device_telemetry")
    command.upgrade(cfg, "head")
    assert _tablo_var_mi(url, "unknown_device_telemetry")


# --------------------------------------------------------------------------
# A06 — in-process Alembic sonrasi logger'lar HALA calisir
# --------------------------------------------------------------------------
def test_A06_alembic_sonrasi_caplog_hala_calisir(gecici_db, monkeypatch, caplog):
    """`env.py`nin `fileConfig` cagrisi butun logger'lari susturabiliyor.

    Helper `config_file_name`i None birakarak o dali atlar. Bu test, atlanmis
    olmasinin GOZLEMLENEBILIR sonucunu dogrular: Alembic'ten SONRA sirasiz bir
    logger hala kayit uretmeli. (Bu koruma olmadan ayni surecte kosan alakasiz
    caplog testleri dusuyordu.)
    """
    from alembic import command

    url = pg_target.url_for(gecici_db)
    eng = create_engine(url)
    Base.metadata.create_all(eng)
    eng.dispose()

    cfg = pg_target.alembic_config(url, monkeypatch)
    command.stamp(cfg, "0070")
    command.upgrade(cfg, "head")

    log = logging.getLogger("app.services.unknown_device_quarantine")
    assert not log.disabled, "Alembic uygulama logger'ini devre disi birakti"

    with caplog.at_level(logging.WARNING):
        log.warning("alembic_sonrasi_kayit")
    assert "alembic_sonrasi_kayit" in caplog.text
