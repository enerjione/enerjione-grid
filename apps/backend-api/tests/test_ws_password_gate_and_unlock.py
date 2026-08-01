"""WebSocket sifre kapisi + hesap kilidini acma yolu (A2 eksigi).

ARIZA 1 — WEBSOCKET, ZORUNLU SIFRE DEGISIMINI ATLIYORDU
--------------------------------------------------------
A2 duzeltmesi kapiyi `deps.get_current_user` icine koydu. Ama WebSocket ucu
oradan GECMIYOR: kendi kimlik dogrulamasini yapiyor (`ws_live.py`).

Bilet yolu kapaliydi — `/auth/ws-ticket` muaf uclar listesinde degil, yani
bayrak acikken bilet ALINAMIYOR. Ancak LEGACY `?token=<JWT>` yolu bileti
tamamen atliyor ve o JWT parola degistirilmeden ONCE, login aninda
uretiliyor.

Sonuc: varsayilan kurulum parolasiyla giren biri HTTP'de 403 aliyor ama
WebSocket'ten TUM SAHANIN canli telemetrisini okuyabiliyordu — cihaz
konumlari, ariza durumlari, olcumler.

ARIZA 2 — KILITLENEN HESABIN ACILMA YOLU YOKTU
-----------------------------------------------
Kilit `locked_until` ile konuluyor ve parola dogrulamasindan ONCE kontrol
ediliyor; `failed_login_count` kilit suresi dolunca sifirlanmiyor.
`POST /users/{id}/reset-password` `locked_until`'a HIC dokunmuyordu.

INSTALLER hesabina yalnizca INSTALLER mudahale edebildiginden, tek installer
hesabi kilitlenince gateway ekleme, ag ayari, yedek/geri yukleme ve uzaktan
bakim izni hep birlikte kilitleniyor; cozum saha ziyareti oluyordu.
"""

from __future__ import annotations

import ast
import inspect

import pytest


# ---------------------------------------------------------------------------
# ARIZA 1 — WebSocket kapisi
# ---------------------------------------------------------------------------

def test_ws_kullanici_cozumu_bayragi_DONDURUYOR():
    """Kapinin kosabilmesi icin bayragin cozumleyiciden gelmesi sart."""
    from app.api import ws_live

    kaynak = inspect.getsource(ws_live._resolve_allowed_device_codes)
    agac = ast.parse(kaynak.lstrip())

    donusler = [n for n in ast.walk(agac) if isinstance(n, ast.Return)]
    assert donusler, "fonksiyon hic deger dondurmuyor"
    for r in donusler:
        assert isinstance(r.value, ast.Tuple) and len(r.value.elts) == 3, (
            "her donus (bulundu, kodlar, sifre_degisimi_zorunlu) uclusu olmali; "
            "eksik bir dal kapiyi sessizce devre disi birakir"
        )


def test_ws_ucu_bayragi_KONTROL_EDIYOR():
    """WS ucu bayragi okuyup baglantiyi kapatmali.

    AST ile bakiliyor: metin aramasi bu dosyanin ya da ws_live'in kendi
    aciklamalarina takilir ve testi anlamsizca yesil/kirmizi yapardi.
    """
    from app.api import ws_live

    fn = next(
        d
        for d in ast.walk(ast.parse(inspect.getsource(ws_live)))
        if isinstance(d, ast.AsyncFunctionDef) and d.name == "live_values_ws"
    )

    # `if must_change_password:` benzeri bir dal ve icinde close() cagrisi
    dallar = [
        n
        for n in ast.walk(fn)
        if isinstance(n, ast.If)
        and any(
            isinstance(x, ast.Name) and x.id == "must_change_password"
            for x in ast.walk(n.test)
        )
    ]
    assert dallar, (
        "WS ucu `must_change_password` bayragini hic kontrol etmiyor — "
        "varsayilan parolayla tum sahanin telemetrisi okunabilir"
    )

    kapatma_var = any(
        isinstance(c, ast.Call) and getattr(c.func, "attr", None) == "close"
        for dal in dallar
        for c in ast.walk(dal)
    )
    assert kapatma_var, "bayrak kontrol ediliyor ama baglanti KAPATILMIYOR"

    donus_var = any(isinstance(n, ast.Return) for dal in dallar for n in ast.walk(dal))
    assert donus_var, "close() cagriliyor ama akis devam ediyor"


def test_ws_bileti_muaf_uclar_listesinde_DEGIL():
    """Bilet yolu da kapali kalmali; acilirsa HTTP kapisi anlamsizlasir."""
    from app.api.deps import _PASSWORD_CHANGE_ALLOWED_SUFFIXES

    assert not any("ws-ticket" in s for s in _PASSWORD_CHANGE_ALLOWED_SUFFIXES), (
        "ws-ticket muaf hale gelmis — sifre degisimi zorunlulugu canli veri "
        "akisi icin atlatilabilir"
    )


# ---------------------------------------------------------------------------
# ARIZA 2 — reset-password kilidi acmali
# ---------------------------------------------------------------------------

def _reset_password_fn() -> ast.FunctionDef:
    from app.api import users

    return next(
        d
        for d in ast.walk(ast.parse(inspect.getsource(users)))
        if isinstance(d, ast.FunctionDef) and d.name == "reset_password"
    )


def _atanan_alanlar(fn: ast.FunctionDef) -> set[str]:
    alanlar: set[str] = set()
    for n in ast.walk(fn):
        if isinstance(n, ast.Assign):
            for hedef in n.targets:
                if isinstance(hedef, ast.Attribute):
                    alanlar.add(hedef.attr)
    return alanlar


@pytest.mark.parametrize(
    "alan,gerekce",
    [
        (
            "locked_until",
            "kilitlenen hesabin API uzerinden acilma yolu kalmaz; tek installer "
            "hesabi kilitlenirse cozum saha ziyareti olur",
        ),
        (
            "failed_login_count",
            "sayac sifirlanmazsa hesap bir sonraki hatali denemede aninda "
            "yeniden kilitlenir",
        ),
        (
            "must_change_password",
            "yoneticinin belirledigi gecici parola sinirsiz kullanilabilir kalir",
        ),
    ],
)
def test_reset_password_alani_AYARLIYOR(alan: str, gerekce: str):
    alanlar = _atanan_alanlar(_reset_password_fn())
    assert alan in alanlar, f"reset_password `{alan}` alanina dokunmuyor — {gerekce}"


def test_reset_password_parolayi_YINE_ayarliyor():
    """Kilit acma eklenirken asil isin kaybolmadigini sabitler."""
    assert "hashed_password" in _atanan_alanlar(_reset_password_fn())
