"""Migration 0025 var olan kolonda PATLAMAMALI.

YASANAN ARIZA
-------------
Backend acilista once `Base.metadata.create_all()` cagiriyor: tablolar
GUNCEL modellerden olusuyor ve `device_event_at` / `timestamp_quality`
zaten iceride oluyor. Ardindan alembic gecmisi bastan oynatiliyor; 0025
var olan kolonu eklemeye calisip patliyordu:

    psycopg2.errors.DuplicateColumn: column "device_event_at"
    of relation "telemetry_history" already exists

Sonuc: backend acilamiyor -> healthcheck dusuyor -> kurulum
"backend-api is unhealthy" ile duruyor. Cihaz KALICI kilitleniyor, her
yeniden deneme ayni noktada patliyor.

Temiz veritabaninda gorulmuyordu; yalnizca onceki bir denemeden veri hacmi
kalmis cihazlarda cikiyordu. "Bir sunucuda oluyor digerinde olmuyor"un
sebebi buydu ve bu yuzden uzun sure teshis edilemedi.
"""

from __future__ import annotations

import ast
from pathlib import Path

_MIG = (
    Path(__file__).resolve().parents[1]
    / "alembic_migrations/versions/2026_07_31_0007-0025_add_device_event_time.py"
)


def _fonksiyon(ad: str) -> ast.FunctionDef:
    for d in ast.walk(ast.parse(_MIG.read_text(encoding="utf-8"))):
        if isinstance(d, ast.FunctionDef) and d.name == ad:
            return d
    raise AssertionError(f"{ad} bulunamadi")


def _cagrilar(fn: ast.FunctionDef, ad: str) -> list[ast.Call]:
    return [
        d
        for d in ast.walk(fn)
        if isinstance(d, ast.Call)
        and getattr(d.func, "attr", None) == ad
    ]


def test_add_column_kosula_bagli() -> None:
    """Her `add_column` bir `if` icinde olmali.

    Kosulsuz `add_column`, kolon zaten varsa DuplicateColumn ile backend'i
    dusurur. Metin aramasi yetmez (yorumda da "if" gecer) — AST ile
    cagrinin gercekten kosul altinda oldugu dogrulaniyor.
    """
    up = _fonksiyon("upgrade")
    eklemeler = _cagrilar(up, "add_column")
    assert eklemeler, "0025 artik add_column cagirmiyor — migration bozulmus"

    kosul_icindekiler = [
        c
        for dugum in ast.walk(up)
        if isinstance(dugum, ast.If)
        for govde in dugum.body
        for c in ast.walk(govde)
        if isinstance(c, ast.Call) and getattr(c.func, "attr", None) == "add_column"
    ]
    assert len(kosul_icindekiler) == len(eklemeler), (
        "en az bir `add_column` kosulsuz — kolon zaten varsa backend acilmaz"
    )


def test_mevcut_kolonlar_okunuyor() -> None:
    """Kosul, kolon listesini GERCEKTEN veritabanindan okumali.

    Sabit bir `if True`/`if False` de yukaridaki testi gecerdi.
    """
    kaynak = _MIG.read_text(encoding="utf-8")
    assert "get_columns" in kaynak and "get_bind" in kaynak, (
        "mevcut kolonlar veritabanindan okunmuyor"
    )


def test_downgrade_de_dayanikli() -> None:
    """Kolon yoksa `drop_column` da patlamamali."""
    down = _fonksiyon("downgrade")
    dusurmeler = _cagrilar(down, "drop_column")
    assert dusurmeler
    kosul_icindekiler = [
        c
        for dugum in ast.walk(down)
        if isinstance(dugum, ast.If)
        for govde in dugum.body
        for c in ast.walk(govde)
        if isinstance(c, ast.Call) and getattr(c.func, "attr", None) == "drop_column"
    ]
    assert len(kosul_icindekiler) == len(dusurmeler)
