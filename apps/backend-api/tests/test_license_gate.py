"""Lisans kilidi (core/license_gate.py) testleri.

Kilit DEFAULT-DENY: api_prefix altindaki her yol kapalidir, yalnizca beyaz
liste aciktir. Buradaki testler hem beyaz listeyi hem de "yeni bir router
otomatik korunur" garantisini kontrol eder.

Neden TestClient degil elle ASGI: starlette.testclient httpx gerektiriyor,
bu proje httpx'e bagli degil. Middleware zaten ham ASGI oldugu icin scope'u
elle kurup mesajlari toplamak hem bagimlilik eklemez hem de test edilen
sozlesmenin (http + websocket scope'lari) tam olarak kendisidir.
"""

from __future__ import annotations

import asyncio
import json

import pytest

from app.core.config import settings
from app.core.license_gate import LicenseGateMiddleware, _is_allowed
from app.services import license_service


PREFIX = settings.api_prefix


async def _downstream(scope, receive, send):
    """Kilidi gecen istekleri karsilayan sahte uygulama."""
    if scope["type"] == "websocket":
        await send({"type": "websocket.accept"})
        await send({"type": "websocket.send", "text": '{"ok":true}'})
        await send({"type": "websocket.close", "code": 1000})
        return
    await send(
        {
            "type": "http.response.start",
            "status": 200,
            "headers": [(b"content-type", b"application/json")],
        }
    )
    await send({"type": "http.response.body", "body": b'{"ok":true}'})


def _call(method: str, path: str, *, locked: bool, scope_type: str = "http"):
    """Middleware'i tek istek icin surer; (status, body_dict, messages) doner."""
    app = LicenseGateMiddleware(_downstream)
    scope = {
        "type": scope_type,
        "path": path,
        "headers": [],
        "query_string": b"",
    }
    if scope_type == "http":
        scope["method"] = method

    sent: list[dict] = []

    async def send(message):
        sent.append(message)

    async def receive():
        return {"type": "http.request", "body": b"", "more_body": False}

    original = license_service.is_api_locked
    license_service.is_api_locked = lambda: locked
    try:
        asyncio.run(app(scope, receive, send))
    finally:
        license_service.is_api_locked = original

    status = next(
        (m["status"] for m in sent if m["type"] == "http.response.start"), None
    )
    body = b"".join(m.get("body", b"") for m in sent if m["type"] == "http.response.body")
    payload = json.loads(body) if body else None
    return status, payload, sent


# --- Beyaz liste: lisanssiz da acik kalmali ---------------------------------

@pytest.mark.parametrize(
    "method,path",
    [
        ("GET", f"{PREFIX}/health"),
        ("POST", f"{PREFIX}/auth/login"),
        ("GET", f"{PREFIX}/license/gate"),
        ("GET", f"{PREFIX}/license/status"),
        # Kisir dongu korumasi: lisansi yukleyecek uc kilit icinde olamaz,
        # yoksa lisanssiz sistem kendini asla acamaz.
        ("POST", f"{PREFIX}/license/import"),
        ("GET", f"{PREFIX}/network/status"),
        ("POST", f"{PREFIX}/network/wifi/connect"),
        ("GET", f"{PREFIX}/project-settings"),
        # api_prefix disi (dokuman/statik) kilit kapsaminda degil
        ("GET", "/openapi.json"),
        ("GET", "/docs"),
    ],
)
def test_allowlist_open_when_unlicensed(method, path):
    status, payload, _ = _call(method, path, locked=True)
    assert status == 200, f"{method} {path} kilitlenmemeliydi"
    assert payload == {"ok": True}


# --- Kilitli: urun islevi tasiyan her sey kapali ----------------------------

@pytest.mark.parametrize(
    "method,path",
    [
        ("GET", f"{PREFIX}/devices"),
        ("POST", f"{PREFIX}/devices"),
        ("GET", f"{PREFIX}/faults"),
        ("GET", f"{PREFIX}/telemetry/latest"),
        ("GET", f"{PREFIX}/alarms"),
        ("GET", f"{PREFIX}/grid/snapshot"),
        ("GET", f"{PREFIX}/users"),
        ("POST", f"{PREFIX}/internal/alarms"),
        ("GET", f"{PREFIX}/public/devices"),
        ("GET", f"{PREFIX}/backups"),
        # Beyaz listedeki yol ama YASAK metot: branding okunur, yazilamaz.
        ("PUT", f"{PREFIX}/project-settings"),
    ],
)
def test_blocked_when_unlicensed(method, path):
    status, payload, _ = _call(method, path, locked=True)
    assert status == 403, f"{method} {path} kilitlenmeliydi"
    assert payload["detail"]["code"] == "LICENSE_REQUIRED"


def test_websocket_blocked_without_accept():
    """WebSocket accept EDILMEDEN kapatilmali.

    BaseHTTPMiddleware kullansaydik websocket scope'u middleware'i hic
    gormezdi ve canli telemetri kilidin disinda kalirdi.
    """
    _, _, sent = _call("GET", f"{PREFIX}/ws/live-values", locked=True, scope_type="websocket")
    assert sent == [{"type": "websocket.close", "code": 4003}]
    assert not any(m["type"] == "websocket.accept" for m in sent)


# --- Lisansli: hicbir sey engellenmemeli ------------------------------------

@pytest.mark.parametrize(
    "method,path",
    [
        ("GET", f"{PREFIX}/devices"),
        ("GET", f"{PREFIX}/faults"),
        ("POST", f"{PREFIX}/internal/alarms"),
        ("PUT", f"{PREFIX}/project-settings"),
    ],
)
def test_open_when_licensed(method, path):
    status, payload, _ = _call(method, path, locked=False)
    assert status == 200
    assert payload == {"ok": True}


def test_websocket_open_when_licensed():
    _, _, sent = _call("GET", f"{PREFIX}/ws/live-values", locked=False, scope_type="websocket")
    assert sent[0] == {"type": "websocket.accept"}


# --- Default-deny garantisi -------------------------------------------------

def test_unknown_new_router_is_denied_by_default():
    """Ileride eklenen bir router beyaz listeye girmedigi surece KAPALI olmali.

    Korumayi router basina `Depends` ile yapsaydik yeni router'i unutmak
    sessiz bir acik birakirdi; middleware default-deny oldugu icin birakmaz.
    """
    assert not _is_allowed(f"{PREFIX}/yepyeni-modul", "GET")
    assert not _is_allowed(f"{PREFIX}/yepyeni-modul/alt-yol", "POST")


def test_allowlist_does_not_leak_to_other_roots():
    assert _is_allowed(f"{PREFIX}/network/status", "GET")
    assert not _is_allowed(f"{PREFIX}/devices/network", "GET")
    assert not _is_allowed(f"{PREFIX}/gateways", "GET")


# --- get_enforcement_state: dosya okuma + cache -----------------------------

@pytest.fixture
def license_dir(tmp_path, monkeypatch):
    """Bos, izole bir lisans dizini + temiz cache."""
    monkeypatch.setattr(settings, "license_dir", str(tmp_path))
    license_service.invalidate_enforcement_cache()
    yield tmp_path
    license_service.invalidate_enforcement_cache()


def test_no_license_file_means_unlicensed_and_locked(license_dir):
    state, reason = license_service.get_enforcement_state(refresh=True)
    assert state == "unlicensed"
    assert reason == "LICENSE_REQUIRED"
    assert license_service.is_api_locked() is True


def test_corrupt_license_does_not_lock_the_system(license_dir):
    """Lisans VAR ama bozuk -> 'invalid'. Sistem KILITLENMEZ.

    Sahada calisan bir SCADA'yi bozuk imza yuzunden karartmak, ariza
    takibini de durdurur; bilincli karar (bkz. ENFORCED_STATES).
    """
    (license_dir / "license.lic").write_bytes(b"bu gecerli bir lisans degil")
    state, _ = license_service.get_enforcement_state(refresh=True)
    assert state == "invalid"
    assert license_service.is_api_locked() is False


def test_state_is_cached_between_calls(license_dir):
    """Kilit her istekte kontrol ediliyor; dosya+imza her seferinde okunamaz."""
    license_service.get_enforcement_state(refresh=True)
    assert license_service.is_api_locked() is True
    # Dosya sonradan olusturuldu ama cache TTL'i dolmadi -> eski cevap.
    (license_dir / "license.lic").write_bytes(b"bozuk ama VAR")
    assert license_service.is_api_locked() is True
    # Explicit invalidation (import_license bunu yapar) -> yeni durum okunur.
    license_service.invalidate_enforcement_cache()
    assert license_service.is_api_locked() is False


def test_import_invalidates_cache_so_lock_opens_immediately():
    """import_license cache'i dusurmeli, yoksa kullanici TTL kadar kilitli kalir."""
    import inspect

    source = inspect.getsource(license_service.import_license)
    assert "invalidate_enforcement_cache()" in source


def test_enforced_states_only_unlicensed():
    """Frontend LICENSE_GATE_STATES ile senkron olmali.

    invalid / machine_mismatch / machine_unavailable KILITLEMEZ — sahada
    calisan bir SCADA'yi karartmamak icin bilincli karar.
    """
    assert license_service.ENFORCED_STATES == frozenset({"unlicensed"})


def test_gate_check_failure_fails_open():
    """Kilit KONTROLU patlarsa istek gecmeli.

    "Lisans yok" durumu zaten `unlicensed` state'i ile temsil ediliyor;
    beklenmedik bir istisna yuzunden calisan sahayi karartmak dogru degil.
    """
    def boom():
        raise RuntimeError("beklenmedik")

    app = LicenseGateMiddleware(_downstream)
    scope = {"type": "http", "path": f"{PREFIX}/devices", "method": "GET",
             "headers": [], "query_string": b""}
    sent: list[dict] = []

    async def send(message):
        sent.append(message)

    async def receive():
        return {"type": "http.request", "body": b"", "more_body": False}

    original = license_service.is_api_locked
    license_service.is_api_locked = boom
    try:
        asyncio.run(app(scope, receive, send))
    finally:
        license_service.is_api_locked = original

    status = next(m["status"] for m in sent if m["type"] == "http.response.start")
    assert status == 200
