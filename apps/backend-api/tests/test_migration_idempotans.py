"""MIGRATION'LAR `create_all` ILE CAKISMAZ.

YASANAN HATA
------------
Temiz kurulum ile yukseltilen kurulum AYNI YERDEN GECMIYOR:

  * TEMIZ kurulum semayi `create_all` + `stamp head` ile kuruyor
    (bkz. `scripts/migrate_db.py`) — migration'lar HIC kosmuyor.
  * YUKSELTILEN kurulum alembic zincirini kosuyor.

Bir migration bunu hesaba katmazsa, `create_all` ile kurulmus bir semada
`CREATE TABLE` calistirmaya kalkar ve

    psycopg2.errors.DuplicateTable: relation "..." already exists

ile patlar. Migration konteyner CMD'sinde uvicorn'dan ONCE kostugu icin
sonuc KALICI BIR CRASH-LOOP olur.

Bu tam olarak 0075'te yasandi: tablo kapisi (0073/0074'te zaten var olan
desen) unutuldu ve hata YALNIZCA CI'daki gercek PostgreSQL restore
testinde ortaya cikti — birim testleri yesildi.

BU DOSYA O BOSLUGU KAPATIR: senaryoyu SQLite uzerinde, YERELDE surer.
Gercek PostgreSQL'e gerek yok, cunku kirilan sey lehce degil MANTIK.
"""

from __future__ import annotations

import importlib.util
import pathlib

import pytest
import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations

import app.models  # noqa: F401  (Base.metadata tam olsun)
from app.db.base import Base

VERSIYONLAR = pathlib.Path(__file__).resolve().parents[1] / "alembic_migrations/versions"


def _migration_dosyalari() -> list[pathlib.Path]:
    """Tum migration dosyalari, revision sirasina gore."""
    return sorted(p for p in VERSIYONLAR.glob("*.py") if not p.name.startswith("__"))


def _yukle(yol: pathlib.Path):
    spec = importlib.util.spec_from_file_location(f"mig_{yol.stem}", yol)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


#: `create_all` sonrasi kosulunca ATLAMASI gereken migration'lar.
#:
#: Yalnizca SON halkalar sinaniyor: eski migration'lar tarihsel olarak
#: kosulmus semalar uzerinde calisti ve bazilari veri tasima adimlari
#: iceriyor (bos semada anlamsiz). Yeni eklenen her migration BURAYA
#: eklenmelidir — asagidaki bekci testi bunu zorlar.
SON_HALKALAR = ("0073", "0074", "0075", "0076", "0077", "0078")


def _son_migrationlar() -> list[pathlib.Path]:
    out = []
    for rev in SON_HALKALAR:
        eslesen = [p for p in _migration_dosyalari() if f"-{rev}_" in p.name]
        assert len(eslesen) == 1, f"{rev} icin tek dosya beklenirdi: {eslesen}"
        out.append(eslesen[0])
    return out


def test_create_all_sonrasi_upgrade_CAKISMAZ():
    """CI'daki gercek senaryo: tam sema kurulu, migration yine kosuyor."""
    eng = sa.create_engine("sqlite://")
    Base.metadata.create_all(eng)
    with eng.begin() as conn:
        with Operations.context(MigrationContext.configure(conn)):
            for yol in _son_migrationlar():
                try:
                    _yukle(yol).upgrade()
                except Exception as exc:  # noqa: BLE001
                    pytest.fail(
                        f"{yol.name} `create_all` ile kurulmus semada patladi: "
                        f"{type(exc).__name__}: {exc}\n"
                        "Migration ZATEN VARSA ATLAMALI (bkz. 0073/0074 deseni: "
                        "`sa.inspect(bind).has_table(...)` / `get_columns(...)`)."
                    )


def test_upgrade_IKI_KEZ_kosulabilir():
    """Yarim kalmis bir yukseltme yeniden denendiginde patlamamali."""
    eng = sa.create_engine("sqlite://")
    Base.metadata.create_all(eng)
    for tur in (1, 2):
        with eng.begin() as conn:
            with Operations.context(MigrationContext.configure(conn)):
                for yol in _son_migrationlar():
                    try:
                        _yukle(yol).upgrade()
                    except Exception as exc:  # noqa: BLE001
                        pytest.fail(f"{yol.name} {tur}. turda patladi: {exc}")


def test_YENI_MIGRATION_bu_teste_EKLENMELI():
    """Bekci: zincirin son halkasi `SON_HALKALAR` icinde olmali.

    Yeni bir migration eklenip buraya yazilmazsa, `create_all` cakismasi
    yine YALNIZCA CI'da gorulurdu — bu dosyanin var olma sebebi tam da o.
    """
    from alembic.config import Config
    from alembic.script import ScriptDirectory

    kok = pathlib.Path(__file__).resolve().parents[1]
    cfg = Config(str(kok / "alembic.ini"))
    cfg.set_main_option("script_location", str(kok / "alembic_migrations"))
    head = ScriptDirectory.from_config(cfg).get_heads()[0]
    assert head in SON_HALKALAR, (
        f"zincirin son halkasi {head!r} bu testin kapsaminda degil. "
        "Yeni migration'i SON_HALKALAR'a ekleyin; aksi halde `create_all` "
        "cakismasi yalnizca CI'daki PostgreSQL restore testinde gorulur."
    )


def test_TABLO_OLUSTURAN_migrationlarda_KAPI_VAR():
    """`create_table` cagiran her son migration bir varlik kapisi tasimali."""
    for yol in _son_migrationlar():
        metin = yol.read_text(encoding="utf-8")
        if "op.create_table(" not in metin:
            continue
        assert "has_table(" in metin, (
            f"{yol.name}: `create_table` var ama `has_table` kapisi YOK. "
            "Temiz kurulumda tablo `create_all` ile olusmus olabilir."
        )


def test_KOLON_EKLEYEN_migrationlarda_KAPI_VAR():
    """`add_column` cagiran her son migration bir varlik kapisi tasimali."""
    for yol in _son_migrationlar():
        metin = yol.read_text(encoding="utf-8")
        if "op.add_column(" not in metin:
            continue
        assert "get_columns(" in metin, (
            f"{yol.name}: `add_column` var ama `get_columns` kapisi YOK."
        )


def test_downgrade_YOKKEN_de_dayanikli():
    """Bos semada geri alma hata vermemeli (yarim kalmis yukseltme)."""
    eng = sa.create_engine("sqlite://")
    # Yalnizca FK hedefleri; yeni tablo/kolonlar YOK.
    for ad in ("devices", "device_config_versions", "device_commands"):
        Base.metadata.tables[ad].create(eng)
    with eng.begin() as conn:
        with Operations.context(MigrationContext.configure(conn)):
            yol = [p for p in _son_migrationlar() if "-0075_" in p.name][0]
            try:
                _yukle(yol).downgrade()
            except Exception as exc:  # noqa: BLE001
                pytest.fail(f"0075 downgrade bos semada patladi: {exc}")
