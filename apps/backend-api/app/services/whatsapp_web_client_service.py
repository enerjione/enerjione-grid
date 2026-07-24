import json
import urllib.error
import urllib.request

from app.core.config import settings


def _request(method: str, path: str, body: dict | None = None, timeout: int = 8) -> dict:
    url = f"{settings.whatsapp_web_gateway_url.rstrip('/')}{path}"
    data = json.dumps(body).encode("utf-8") if body is not None else None
    req = urllib.request.Request(
        url,
        data=data,
        headers={
            "Content-Type": "application/json",
            "X-Service-Token": settings.internal_service_token,
        },
        method=method,
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def fetch_status() -> dict:
    """WhatsApp Web sidecar'in baglanti durumunu doner.

    Sidecar'a ulasilamazsa (henuz baslamamis/kapali) hata firlatmaz —
    kullaniciya 'disconnected' olarak yansitilir.
    """
    try:
        return _request("GET", "/status")
    except (urllib.error.URLError, TimeoutError, OSError, ValueError):
        return {"status": "disconnected", "phone_number": None}


def fetch_qr() -> dict:
    try:
        return _request("GET", "/qr")
    except (urllib.error.URLError, TimeoutError, OSError, ValueError):
        return {"qr": None}


def send_test_message(recipient_phone: str, message: str) -> None:
    if not recipient_phone:
        raise ValueError("Alıcı numarası boş.")
    try:
        result = _request("POST", "/send", {"to": recipient_phone, "message": message}, timeout=15)
    except urllib.error.HTTPError as exc:
        raise RuntimeError(f"WhatsApp Web gateway hatası: HTTP {exc.code}") from exc
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise RuntimeError(f"WhatsApp Web gateway'e ulaşılamadı: {exc}") from exc
    if not result.get("ok"):
        raise RuntimeError(result.get("error") or "WhatsApp mesajı gönderilemedi.")


def logout() -> None:
    try:
        _request("POST", "/logout", {}, timeout=10)
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise RuntimeError(f"WhatsApp Web gateway'e ulaşılamadı: {exc}") from exc
