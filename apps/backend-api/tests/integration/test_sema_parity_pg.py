"""Sema parity — canonical sema ile SAF ALEMBIC semasi ayni mi? (gercek PG)

NEDEN GERCEK POSTGRES
---------------------
Karsilastirilan seylerin cogu SQLite'ta YOKTUR ya da farklidir: sequence
default'lari (`nextval`), `use_alter` ile eklenen dongusel FK, kismi
index'ler, `TIMESTAMPTZ`. Parity iddiasi ancak PG16 uzerinde anlamlidir.

KAPSAM
------
  A01  bos DB -> `stamp 0071` -> `upgrade head`  = eksiksiz sema
  A15  canonical (create_all) ile saf Alembic semasi ARASINDA fark YOK
  A16  mevcut 0071 uretim benzeri DB -> 0072 = NO-OP
  A17  hypertable/retention beklenen durumda
"""

from __future__ import annotations

import pytest
from sqlalchemy import create_engine, inspect, text

import app.models  # noqa: F401
from app.db.base import Base
from tests.integration import pg_target

pytestmark = pytest.mark.integration

if not pg_target.pg_url():
    pytest.skip("E1_TEST_PG_URL yok", allow_module_level=True)

#: Temiz kurulumda zincirin "uygulanmis" sayildigi taban (bkz. migrate_db).
TABAN = "0071"


# --------------------------------------------------------------------------
# Yardimcilar
# --------------------------------------------------------------------------
def _olustur(ad: str) -> None:
    y = create_engine(pg_target.pg_url(), isolation_level="AUTOCOMMIT")
    with y.connect() as c:
        c.execute(text(f'DROP DATABASE IF EXISTS "{ad}"'))
        c.execute(text(f'CREATE DATABASE "{ad}" TEMPLATE template0'))
    y.dispose()
    pg_target.kaydet_olusturuldu(ad)


def _sil(ad: str) -> None:
    y = create_engine(pg_target.pg_url(), isolation_level="AUTOCOMMIT")
    with y.connect() as c:
        c.execute(
            text(
                "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                f"WHERE datname='{ad}' AND pid <> pg_backend_pid()"
            )
        )
        c.execute(text(f'DROP DATABASE IF EXISTS "{ad}"'))
    y.dispose()
    pg_target.unut(ad)


@pytest.fixture()
def iki_db():
    """DB-A (canonical) ve DB-B (saf Alembic)."""
    a = pg_target.yeni_db_adi("parity_a")
    b = pg_target.yeni_db_adi("parity_b")
    _olustur(a)
    _olustur(b)
    try:
        yield a, b
    finally:
        _sil(a)
        _sil(b)


def _imza(url: str) -> dict:
    """Sema imzasi: tablo/kolon/tip/nullable/default/PK/FK/unique/index/check."""
    e = create_engine(url)
    insp = inspect(e)
    out: dict = {}
    for t in sorted(insp.get_table_names()):
        if t == "alembic_version":
            continue
        out[t] = {
            "columns": {
                c["name"]: (
                    str(c["type"]),
                    bool(c["nullable"]),
                    str(c.get("default")) if c.get("default") is not None else None,
                )
                for c in insp.get_columns(t)
            },
            "pk": sorted(insp.get_pk_constraint(t).get("constrained_columns") or []),
            "fk": sorted(
                f"{sorted(f['constrained_columns'])}->"
                f"{f['referred_table']}.{sorted(f['referred_columns'])}"
                for f in insp.get_foreign_keys(t)
            ),
            "unique": sorted(str(sorted(u["column_names"])) for u in insp.get_unique_constraints(t)),
            "indexes": sorted(
                f"{sorted(i['column_names'] or [])}|uniq={bool(i['unique'])}"
                for i in insp.get_indexes(t)
            ),
            "check": sorted(str(c.get("sqltext")) for c in insp.get_check_constraints(t)),
        }
    e.dispose()
    return out


def _canonical_kur(url: str, monkeypatch) -> None:
    """DB-A: bugun dogru kabul edilen sema — modelden + `stamp head`."""
    from alembic import command

    e = create_engine(url)
    Base.metadata.create_all(e)
    e.dispose()
    command.stamp(pg_target.alembic_config(url, monkeypatch), "head")


def _saf_alembic_kur(url: str, monkeypatch) -> None:
    """DB-B: production temiz kurulum yolu — `create_all` YOK."""
    from alembic import command

    cfg = pg_target.alembic_config(url, monkeypatch)
    command.stamp(cfg, TABAN)
    command.upgrade(cfg, "head")


# --------------------------------------------------------------------------
# A01 — bos DB, yalnizca Alembic ile eksiksiz sema
# --------------------------------------------------------------------------
def test_A01_bos_DB_saf_alembic_ile_eksiksiz_sema(iki_db, monkeypatch):
    _, b = iki_db
    url = pg_target.url_for(b)
    _saf_alembic_kur(url, monkeypatch)

    e = create_engine(url)
    var = set(inspect(e).get_table_names())
    e.dispose()

    eksik = sorted(set(Base.metadata.tables) - var)
    assert not eksik, f"saf Alembic kurulumunda eksik tablo: {eksik}"
    assert len(var) >= len(Base.metadata.tables), "uygulama tablosu olusmadi"


# --------------------------------------------------------------------------
# A15 — PARITY
# --------------------------------------------------------------------------
def test_A15_canonical_ile_saf_alembic_semasi_AYNI(iki_db, monkeypatch):
    a, b = iki_db
    ua, ub = pg_target.url_for(a), pg_target.url_for(b)
    _canonical_kur(ua, monkeypatch)
    _saf_alembic_kur(ub, monkeypatch)

    ia, ib = _imza(ua), _imza(ub)
    assert set(ia) == set(ib), (
        f"tablo kumesi farkli — yalniz A: {sorted(set(ia)-set(ib))}, "
        f"yalniz B: {sorted(set(ib)-set(ia))}"
    )
    farklar = [t for t in ia if ia[t] != ib[t]]
    assert not farklar, f"su tablolarda sema farki var: {farklar}"


# --------------------------------------------------------------------------
# A16 — mevcut 0071 kurulumu 0072'ye guvenle gecer (NO-OP)
# --------------------------------------------------------------------------
def test_A16_mevcut_0071_semasi_0072ye_gecer_NOOP(iki_db, monkeypatch):
    from alembic import command

    a, _ = iki_db
    url = pg_target.url_for(a)

    e = create_engine(url)
    Base.metadata.create_all(e)
    e.dispose()

    cfg = pg_target.alembic_config(url, monkeypatch)
    command.stamp(cfg, TABAN)

    e = create_engine(url)
    once = sorted(inspect(e).get_table_names())
    e.dispose()

    command.upgrade(cfg, "head")  # 0072 — korumasiz olsaydi "already exists"

    e = create_engine(url)
    sonra = sorted(inspect(e).get_table_names())
    with e.connect() as c:
        rev = c.execute(text("select version_num from alembic_version")).scalar()
    e.dispose()

    assert once == sonra, "0072 mevcut kurulumda tablo EKLEDI/DUSURDU"
    assert rev == "0072"


# --------------------------------------------------------------------------
# A14 — 0072 tekrar kosmasi ve downgrade/upgrade dongusu
# --------------------------------------------------------------------------
def test_A14_0072_tekrar_upgrade_NOOP(iki_db, monkeypatch):
    from alembic import command

    _, b = iki_db
    url = pg_target.url_for(b)
    _saf_alembic_kur(url, monkeypatch)

    cfg = pg_target.alembic_config(url, monkeypatch)
    command.upgrade(cfg, "head")  # no-op

    e = create_engine(url)
    with e.connect() as c:
        assert c.execute(text("select version_num from alembic_version")).scalar() == "0072"
    e.dispose()


# --------------------------------------------------------------------------
# A17 — Timescale: hypertable + saklama politikasi
# --------------------------------------------------------------------------
def test_A17_historian_hypertable_kurulur(iki_db, monkeypatch):
    """Sema Alembic'ten gelse de hypertable AYRI bir adimdir (prerequisite).

    `create_all` ve Alembic yalnizca DUZ TABLO kurar; hypertable'a cevirme
    `ensure_historian_storage` isidir. Bu test o ayrimin korundugunu ve
    saf-Alembic kurulumda da historian'in dogru kuruldugunu dogrular.
    """
    from app.db.timescale_setup import ensure_historian_storage

    _, b = iki_db
    url = pg_target.url_for(b)
    _saf_alembic_kur(url, monkeypatch)

    e = create_engine(url)
    with e.begin() as bind:
        rapor = ensure_historian_storage(bind)

    with e.connect() as c:
        try:
            n = c.execute(
                text(
                    "select count(*) from timescaledb_information.hypertables "
                    "where hypertable_name = 'telemetry_history'"
                )
            ).scalar()
        except Exception:  # noqa: BLE001
            n = None
    e.dispose()

    if rapor.get("skipped"):
        pytest.skip(f"timescaledb yok: {rapor['skipped']}")
    assert n == 1, "telemetry_history hypertable'a cevrilmedi"
