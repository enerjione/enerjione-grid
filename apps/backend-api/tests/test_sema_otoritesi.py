"""Alembic = production sema otoritesi — regresyon kaniti.

Bu dosya, sema otoritesinin calisma zamanina GERI KACMASINI imkansiz kilar.

YASANMIS DURUM
--------------
Backend acilista `Base.metadata.create_all()` cagirip ~124 idempotent DDL
ifadesi kosuyordu (106 ALTER TABLE, 11 CREATE TABLE, 12 CREATE INDEX,
2 ALTER TYPE). Eksik sema SESSIZCE "onariliyor", uygulama yarim semayla
ayaga kalkabiliyordu. Temiz kurulum da semayi Alembic'ten degil
modellerden (`create_all` + `stamp head`) aliyordu.

Testler SAF: gercek veritabani gerekmez.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest

KOK = Path(__file__).resolve().parents[1]

#: Production'da calisan, sema otoritesi tasimasi YASAK dosyalar.
URETIM_YOLLARI = [
    KOK / "app" / "main.py",
    KOK / "scripts" / "migrate_db.py",
]

#: Uygulamaya ait durable sema DDL'i. `text("...")` govdelerinde aranir.
SEMA_DDL = re.compile(
    r"\b(CREATE\s+TABLE|ALTER\s+TABLE|CREATE\s+(UNIQUE\s+)?INDEX|DROP\s+INDEX|ALTER\s+TYPE)\b",
    re.I,
)


def _kod_metni(yol: Path) -> str:
    """Dosyanin YORUM ve DOCSTRING'siz kod metni.

    Gerekce: docstring'ler bilerek eski davranisi ANLATIYOR ("eskiden burada
    create_all vardi"). Duz `grep` bunlari kod sanip testi anlamsiz sekilde
    kirmis olurdu.
    """
    agac = ast.parse(yol.read_text(encoding="utf-8"))
    for dugum in ast.walk(agac):
        if isinstance(dugum, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            if (
                dugum.body
                and isinstance(dugum.body[0], ast.Expr)
                and isinstance(dugum.body[0].value, ast.Constant)
                and isinstance(dugum.body[0].value.value, str)
            ):
                dugum.body[0].value.value = ""  # docstring'i bosalt
    return ast.unparse(agac)


# --------------------------------------------------------------------------
# A09 / M7 — production'da create_all YOK
# --------------------------------------------------------------------------
@pytest.mark.parametrize("yol", URETIM_YOLLARI, ids=lambda p: p.name)
def test_A09_uretim_yolunda_create_all_YOK(yol):
    """M7: production'a `create_all` geri eklenirse bu test DUSER."""
    kod = _kod_metni(yol)
    assert "create_all" not in kod, (
        f"{yol.name} icinde `create_all` var — sema otoritesi Alembic olmali. "
        "Temiz kurulum 0072 ile Alembic'ten gelir."
    )


# --------------------------------------------------------------------------
# A08 — production startup sema MUTATE ETMEZ
# --------------------------------------------------------------------------
def test_A08_startup_sema_DDL_calistirmaz():
    """`app/main.py` icinde uygulama semasi DDL'i kalmamali."""
    kod = _kod_metni(KOK / "app" / "main.py")
    bulunan = SEMA_DDL.findall(kod)
    assert not bulunan, (
        f"app/main.py hala {len(bulunan)} sema DDL ifadesi tasiyor: "
        f"{ {b[0].upper() for b in bulunan} }. Bunlar Alembic migration'ina ait."
    )


def test_A08b_migrate_db_elle_sema_DDL_yazmaz():
    """`migrate_db` yalnizca Alembic'i SURER; kendi DDL'ini yazmaz.

    Hypertable/retention adimi haric — o `timescale_setup` icinde ve modelde
    ifade EDILEMEZ (bkz. §16 prerequisite ayrimi).
    """
    kod = _kod_metni(KOK / "scripts" / "migrate_db.py")
    assert not SEMA_DDL.findall(kod)


# --------------------------------------------------------------------------
# A10 / M8 — sema uyumlulugu SALT OKUNUR ve fail-closed
# --------------------------------------------------------------------------
def _guard():
    from app.db import schema_guard

    return schema_guard


def test_A10_sema_yoksa_NOT_READY(monkeypatch):
    g = _guard()
    monkeypatch.setattr(g, "beklenen_revizyon", lambda: "0072")
    monkeypatch.setattr(g, "gercek_revizyon", lambda _b: None)
    uyumlu, sebep = g.dogrula(object())
    assert uyumlu is False
    assert "KURULMAMIS" in sebep


def test_A10b_ESKI_sema_NOT_READY(monkeypatch):
    """M8: eski revizyonu 'uyumlu' saymak istenirse bu test DUSER."""
    g = _guard()
    monkeypatch.setattr(g, "beklenen_revizyon", lambda: "0072")
    monkeypatch.setattr(g, "gercek_revizyon", lambda _b: "0071")
    monkeypatch.setattr(g, "_bilinen_revizyonlar", lambda: {"0071", "0072"})
    uyumlu, sebep = g.dogrula(object())
    assert uyumlu is False, "ESKI sema READY sayildi"
    assert "ESKI" in sebep


def test_A11_ILERIDE_sema_NOT_READY(monkeypatch):
    """Bilinmeyen (ileri) revizyon: otomatik downgrade/stamp/repair YOK."""
    g = _guard()
    monkeypatch.setattr(g, "beklenen_revizyon", lambda: "0072")
    monkeypatch.setattr(g, "gercek_revizyon", lambda _b: "9999")
    monkeypatch.setattr(g, "_bilinen_revizyonlar", lambda: {"0071", "0072"})
    uyumlu, sebep = g.dogrula(object())
    assert uyumlu is False
    assert "ILERIDE" in sebep


def test_guncel_sema_READY(monkeypatch):
    g = _guard()
    monkeypatch.setattr(g, "beklenen_revizyon", lambda: "0072")
    monkeypatch.setattr(g, "gercek_revizyon", lambda _b: "0072")
    uyumlu, _ = g.dogrula(object())
    assert uyumlu is True


def test_guard_SALT_OKUNUR_olmali():
    """Guard modulu sema DEGISTIREN hicbir cagri icermemeli."""
    kod = _kod_metni(KOK / "app" / "db" / "schema_guard.py")
    for yasak in ("create_all", "command.upgrade", "command.stamp", "command.downgrade"):
        assert yasak not in kod, f"schema_guard sema mutasyonu iceriyor: {yasak}"
    assert not SEMA_DDL.findall(kod)


# --------------------------------------------------------------------------
# A12 — tek head, ve 0072 gercekten head
# --------------------------------------------------------------------------
def test_A12_tek_head_ve_0072():
    from alembic.config import Config
    from alembic.script import ScriptDirectory

    cfg = Config(str(KOK / "alembic.ini"))
    cfg.set_main_option("script_location", str(KOK / "alembic_migrations"))
    script = ScriptDirectory.from_config(cfg)
    heads = list(script.get_heads())
    # TEK HEAD sarti degismez; SURUM ise zincir ilerledikce guncellenir.
    # Sabit "0072" yerine "en yuksek revizyon" demiyoruz cunku o, iki head
    # olustugunda da gecerdi — asil korunan sey CATALLANMAMA.
    assert heads == ["0078"], f"tek head bekleniyordu, gelen: {heads}"


# --------------------------------------------------------------------------
# M6 — 0072 eksik tablo birakirsa yakalanmali
# --------------------------------------------------------------------------
def test_M6_migrationlar_tum_model_tablolarini_kurar():
    """`Base.metadata`daki HER tablonun bir migration'i olmali.

    M6: bir `create_table` silinirse ya da yeni bir model migration'siz
    eklenirse bu test DUSER — parity testi gercek PostgreSQL istiyor, bu
    ise saf ve her kosuda calisir.

    NEDEN ARTIK YALNIZCA 0072'YE BAKMIYOR: 0072 temiz kurulumun TABANIDIR
    ama zincir orada bitmiyor. Yalnizca tabana bakan bir kontrol, 0073 ile
    gelen `gateway_updates`i "eksik" sayar ve gelistiriciyi tabani geriye
    donuk sismeye iterdi — oysa dogru davranis yeni tabloyu YENI bir
    migration'a koymaktir. Korunan sey degismedi: modelde olup HICBIR
    migration'da olmayan tablo kalmasin.
    """
    import app.models  # noqa: F401
    from app.db.base import Base

    kurulan: set[str] = set()
    for yol in (KOK / "alembic_migrations" / "versions").glob("*.py"):
        kaynak = yol.read_text(encoding="utf-8")
        kurulan |= set(re.findall(r"op\.create_table\(\s*['\"]([a-z_]+)", kaynak))
    eksik = sorted(set(Base.metadata.tables) - kurulan)
    assert not eksik, f"su tablolari HICBIR migration kurmuyor: {eksik}"
