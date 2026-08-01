"""SMTP sonsuz bloklamamali; ariza dispatch'i de bayraga bagli olmali.

ARIZA 1 — SMTP'DE timeout YOKTU
--------------------------------
`smtplib.SMTP(host, port)` ve `SMTP_SSL(...)` cagrilarinda `timeout`
verilmezse `socket._GLOBAL_DEFAULT_TIMEOUT` (yani None) kullanilir: SONSUZ
BLOK. Ayni dosyalardaki DIGER tum ag cagrilari zaten timeout'luydu (Telegram
12 sn, FCM 8/15 sn); eksik olan tek yol SMTP idi.

Paket dusuren bir guvenlik duvari ya da yanit vermeyen bir relay, cagiran is
parcacigini kalici olarak tutar.

ARIZA 2 — ARIZA BILDIRIMI KORUMA BAYRAGININ DISINDAYDI
-------------------------------------------------------
Alarm bildirimleri `notification_inline_dispatch_enabled` ile korunuyordu
(varsayilan False; dispatch sorumlulugu notification-worker'da). ARIZA
bildirimleri ise bu bayragin disinda KOSULSUZ calisiyordu — yani "dispatch'i
worker'a aldik" korumasi ariza yolunda hic gecerli degildi.

Bu cagri ariza yeniden hesaplama akisinin ICINDE SMTP/HTTP yapiyor. Sonuc
bildirim gecikmesi degil: ariza motoru asili kaliyor ve ARIZA KAYDI COMMIT
EDILMIYOR — yani arizanin kendisi kaybolyor.
"""

from __future__ import annotations

import ast
import inspect

import pytest


def _smtp_cagrilari(modul) -> list[ast.Call]:
    """Moduldeki tum smtplib.SMTP / SMTP_SSL cagrilarini bulur."""
    agac = ast.parse(inspect.getsource(modul))
    bulunan = []
    for n in ast.walk(agac):
        if not isinstance(n, ast.Call):
            continue
        ad = getattr(n.func, "attr", None)
        if ad in ("SMTP", "SMTP_SSL"):
            bulunan.append(n)
    return bulunan


@pytest.mark.parametrize(
    "modul_yolu",
    [
        "app.services.notification_test_service",
        "app.services.notification_hook_service",
    ],
)
def test_TUM_smtp_cagrilari_timeout_veriyor(modul_yolu: str):
    """Tek bir eksik cagri bile sonsuz blok icin yeterli."""
    import importlib

    modul = importlib.import_module(modul_yolu)
    cagrilar = _smtp_cagrilari(modul)
    assert cagrilar, f"{modul_yolu}: hic SMTP cagrisi bulunamadi (test bosa kosuyor)"

    for c in cagrilar:
        anahtarlar = {k.arg for k in c.keywords}
        assert "timeout" in anahtarlar, (
            f"{modul_yolu}: {getattr(c.func, 'attr', '?')} cagrisinda timeout YOK — "
            "yanit vermeyen bir relay is parcacigini kalici olarak tutar"
        )


def test_timeout_degeri_MAKUL():
    """Cok kisa olursa yavas relay'ler kirilir, cok uzun olursa koruma anlamsiz."""
    from app.services.notification_test_service import SMTP_TIMEOUT_SEC

    assert 5 <= SMTP_TIMEOUT_SEC <= 60, (
        f"SMTP_TIMEOUT_SEC={SMTP_TIMEOUT_SEC} — greylisting/TLS el sikismasi "
        "icin yer birakmali ama operatoru dakikalarca bekletmemeli"
    )


def test_iki_modul_AYNI_sabiti_kullaniyor():
    """Iki ayri deger zamanla ayrisir; biri unutulur."""
    from app.services import notification_hook_service as hook
    from app.services.notification_test_service import SMTP_TIMEOUT_SEC

    assert hook.SMTP_TIMEOUT_SEC is SMTP_TIMEOUT_SEC


def test_ariza_dispatch_i_BAYRAGA_bagli():
    """Alarm yolu korunuyordu, ariza yolu korunmuyordu — hizalanmali."""
    from app.services import fault_recompute_service as frs

    kaynak = inspect.getsource(frs)
    agac = ast.parse(kaynak)

    # `dispatch_fault_notifications` cagrisini iceren fonksiyonu bul
    hedef_fn = None
    for fn in ast.walk(agac):
        if not isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        for n in ast.walk(fn):
            if (
                isinstance(n, ast.Call)
                and getattr(n.func, "id", None) == "dispatch_fault_notifications"
            ):
                hedef_fn = fn
                break
        if hedef_fn is not None:
            break
    assert hedef_fn is not None, "dispatch_fault_notifications cagrisi bulunamadi"

    # Ayni fonksiyonda bayragi okuyan bir dal olmali
    bayrak_okundu = any(
        isinstance(n, ast.Attribute) and n.attr == "notification_inline_dispatch_enabled"
        for n in ast.walk(hedef_fn)
    )
    assert bayrak_okundu, (
        "ariza dispatch'i `notification_inline_dispatch_enabled` bayragini "
        "kontrol etmiyor — SMTP/HTTP cagrisi ariza motorunun icinde kosar ve "
        "yanit vermeyen bir relay ariza kaydinin COMMIT EDILMEMESINE yol acar"
    )


def test_bayrak_varsayilani_KAPALI():
    """Varsayilan acik olsaydi koruma pratikte hic devreye girmezdi."""
    from app.core.config import settings

    assert settings.notification_inline_dispatch_enabled is False
