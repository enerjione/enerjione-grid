"""Lisanssiz kurulumda API'yi kapatan ASGI middleware.

Neden middleware, neden router basina `Depends(...)` degil:
  Router basina dependency eklemek ~30 dosyaya dokunmayi gerektirir ve
  YENI eklenen bir router unutuldugunda sessizce acik kalir. Middleware
  DEFAULT-DENY calisir: api_prefix altindaki her yol kapalidir, yalnizca
  asagidaki beyaz liste aciktir. Yeni bir router eklendiginde otomatik
  olarak korumaya girer.

Neden BaseHTTPMiddleware degil ham ASGI:
  BaseHTTPMiddleware WebSocket scope'unu isletmez; `/ws/live-values`
  korumasiz kalirdi. Ham ASGI middleware hem http hem websocket goruyor.

Beyaz liste (lisanssiz da erisilebilir) ve gerekcesi:
  /health          - konteyner saglik kontrolu; kilit disinda kalmali yoksa
                     lisanssiz kurulum surekli "unhealthy" gorunur.
  /auth/*          - giris yapilamadan lisans da yuklenemez.
  /license/*       - lisansi yukleyecek olan uclar. Kilit icinde olsaydi
                     sistem kendini asla acamazdi (kisir dongu).
  /network/*       - agi bozuk lisanssiz bir cihaz once agi duzeltmek
                     zorunda; bkz. commit 144539f.
  /remote-access/* - /network ile AYNI gerekce. Lisans bozuksa musterinin
                     yetkili kullanicisi (engineer) bize uzaktan bakim izni
                     verebilmeli ki lisansi biz duzeltelim. Kilit icinde
                     olsaydi kisir dongu olurdu: lisans yok -> API kapali ->
                     musteri izin veremiyor -> uzaktan duzeltilemiyor -> sahaya
                     gitmek gerekiyor. Urun islevi degil, kurtarma koludur;
                     ustelik kapiyi ACAN degil, ACMA IZNI VEREN uctur ve
                     yetkisi zaten yalnizca engineer rolundedir.
  /project-settings (GET) - giris ekranindaki logo/baslik. Urun islevi
                     tasimaz, sadece markalama.

Kilit HANGI durumda devreye girer: bkz. `license_service.ENFORCED_STATES`
(yalnizca "unlicensed"). Frontend'deki LICENSE_GATE_STATES ile ayni.
"""

from __future__ import annotations

import logging

from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Receive, Scope, Send

from app.core.config import settings
from app.services import license_service

logger = logging.getLogger(__name__)

# (prefix, izinli metotlar) — metot kumesi None ise tum metotlar serbest.
# Prefix'ler api_prefix'e GORE yazilir (basindaki /api/v1 haric).
_ALLOWED: tuple[tuple[str, frozenset[str] | None], ...] = (
    ("/health", None),
    ("/auth/", None),
    ("/auth", None),
    ("/license/", None),
    ("/license", None),
    ("/network/", None),
    ("/network", None),
    ("/remote-access/", None),
    ("/remote-access", None),
    ("/project-settings", frozenset({"GET", "HEAD", "OPTIONS"})),
)

_LOCK_BODY = {
    "detail": {
        "code": "LICENSE_REQUIRED",
        "message": (
            "Bu kurulumda gecerli lisans yok. Lisans yuklenene kadar API "
            "kullanilamaz."
        ),
    }
}

# WebSocket kapatma kodu — 4000-4999 uygulamaya ozel aralik.
_WS_CLOSE_LICENSE = 4003


def _is_allowed(path: str, method: str) -> bool:
    """Yol lisans kilidinin DISINDA mi?"""
    prefix = settings.api_prefix
    if not path.startswith(prefix):
        # api_prefix disi: /docs, /openapi.json, statik vb. Kilit kapsaminda
        # degil — zaten urun verisi tasimazlar.
        return True
    rest = path[len(prefix):] or "/"
    for allowed_prefix, methods in _ALLOWED:
        if rest == allowed_prefix or rest.startswith(allowed_prefix):
            if methods is None or method.upper() in methods:
                return True
    return False


class LicenseGateMiddleware:
    """Lisans yoksa api_prefix altindaki her seyi 403 ile reddeder."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] not in ("http", "websocket"):
            await self.app(scope, receive, send)
            return

        path = scope.get("path", "")
        # WebSocket scope'unda "method" yoktur; GET kabul et.
        method = scope.get("method", "GET")
        if _is_allowed(path, method):
            await self.app(scope, receive, send)
            return

        try:
            locked = license_service.is_api_locked()
        except Exception:  # noqa: BLE001
            # Kilit kontrolu kendisi patlarsa sistemi kapatma — fail-open
            # BURADA bilincli: lisans dosyasi okunabiliyor ama beklenmedik bir
            # hata olustuysa calisan bir sahayi karartmak dogru degil. Gercek
            # "lisans yok" durumu zaten `unlicensed` state'i ile temsil edilir.
            logger.exception("license_gate_check_failed path=%s", path)
            await self.app(scope, receive, send)
            return

        if not locked:
            await self.app(scope, receive, send)
            return

        logger.warning("license_gate_blocked path=%s method=%s", path, method)
        if scope["type"] == "websocket":
            # accept ETMEDEN kapat — handshake tamamlanmaz.
            await send({"type": "websocket.close", "code": _WS_CLOSE_LICENSE})
            return
        response = JSONResponse(status_code=403, content=_LOCK_BODY)
        await response(scope, receive, send)
