"""Firebase Cloud Messaging (FCM HTTP v1) entegrasyonu.

Service account JSON ile OAuth2 token alir, /v1/projects/<id>/messages:send
endpoint'ine POST atar. firebase-admin yerine google-auth + requests kullaniyoruz
ki bagımlılık hafif kalsın.

Configuration:
- FCM_SERVICE_ACCOUNT_PATH env var, ornek: "/app/fcm-service-account.json"
- Yoksa repo root'undaki "fcm-service-account.json" dosyasi denenir.
- Dosya yoksa servis sessizce devre disi kalir; push gonderim cagrilari
  no-op olur (loglanir).
"""
from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from threading import Lock
from typing import Any

import requests

# google-auth lazy import — paket yuklu degilse backend yine ayaga kalksin,
# sadece push gonderim cagrilari devre disi olsun. Sebep: bazi prod
# ortamlarinda requirements.txt cache'i nedeniyle paket eksik kalabiliyor;
# bu durumda tum app'in ImportError ile patlamasi yerine FCM'i kapatip
# diger kanallari (email/SMS/Telegram) calismaya devam ettiriyoruz.
try:
    from google.auth.transport.requests import Request as GAuthRequest  # type: ignore
    from google.oauth2 import service_account  # type: ignore
    _GOOGLE_AUTH_AVAILABLE = True
except ImportError:  # pragma: no cover
    GAuthRequest = None  # type: ignore[assignment,misc]
    service_account = None  # type: ignore[assignment]
    _GOOGLE_AUTH_AVAILABLE = False

logger = logging.getLogger(__name__)

_FCM_SCOPE = "https://www.googleapis.com/auth/firebase.messaging"
_LOCK = Lock()


class _FcmClient:
    """Lazy-init FCM client. Service account dosyasi yoksa enabled=False."""

    def __init__(self) -> None:
        self.enabled: bool = False
        self.project_id: str | None = None
        self._credentials: service_account.Credentials | None = None
        self._load()

    def _load(self) -> None:
        if not _GOOGLE_AUTH_AVAILABLE:
            logger.info("google-auth paketi yuklu degil; FCM push devre disi.")
            return
        path = os.environ.get("FCM_SERVICE_ACCOUNT_PATH", "").strip()
        if not path:
            # backend-api/ repo root'unu dene (app/services/fcm.py uzaginda)
            here = Path(__file__).resolve().parent.parent.parent
            candidate = here / "fcm-service-account.json"
            if candidate.exists():
                path = str(candidate)
        if not path or not Path(path).exists():
            logger.info("FCM service account JSON bulunamadi; push devre disi.")
            return
        try:
            with open(path, "r", encoding="utf-8") as f:
                raw = json.load(f)
            self.project_id = raw.get("project_id")
            self._credentials = service_account.Credentials.from_service_account_info(
                raw, scopes=[_FCM_SCOPE]
            )
            self.enabled = True
            logger.info("FCM aktif (project=%s)", self.project_id)
        except Exception as exc:  # noqa: BLE001
            logger.warning("FCM yuklenirken hata: %s", exc)
            self.enabled = False

    def _access_token(self) -> str | None:
        if not self.enabled or self._credentials is None:
            return None
        with _LOCK:
            if not self._credentials.valid:
                self._credentials.refresh(GAuthRequest())
            return self._credentials.token

    def send(
        self,
        token: str,
        title: str,
        body: str,
        data: dict[str, str] | None = None,
    ) -> tuple[bool, bool]:
        """Tek cihaza push gonder.

        Returns: (success, is_invalid_token)
        - success=True  -> 2xx, basarili
        - is_invalid_token=True -> 404/UNREGISTERED veya INVALID_ARGUMENT
          gibi durumlarda DB'den temizlenmesi gerekir.
        """
        if not self.enabled:
            return False, False
        access_token = self._access_token()
        if not access_token:
            return False, False
        url = f"https://fcm.googleapis.com/v1/projects/{self.project_id}/messages:send"
        message: dict[str, Any] = {
            "message": {
                "token": token,
                "notification": {"title": title, "body": body},
                "android": {
                    "priority": "HIGH",
                    "notification": {
                        "channel_id": "horstmann_alerts",
                        "sound": "default",
                    },
                },
                "apns": {
                    "payload": {
                        "aps": {
                            "sound": "default",
                            "content-available": 1,
                        }
                    }
                },
            }
        }
        if data:
            message["message"]["data"] = {k: str(v) for k, v in data.items()}
        try:
            res = requests.post(
                url,
                json=message,
                headers={
                    "Authorization": f"Bearer {access_token}",
                    "Content-Type": "application/json; charset=utf-8",
                },
                timeout=8,
            )
            if 200 <= res.status_code < 300:
                return True, False
            txt = (res.text or "")[:300]
            invalid = (
                res.status_code in (404,)
                or "UNREGISTERED" in txt
                or "INVALID_ARGUMENT" in txt
                or "registration-token-not-registered" in txt
            )
            logger.warning(
                "FCM push basarisiz status=%s invalid=%s body=%s",
                res.status_code,
                invalid,
                txt,
            )
            return False, invalid
        except Exception as exc:  # noqa: BLE001
            logger.warning("FCM push exception: %s", exc)
            return False, False


_client: _FcmClient | None = None


def get_fcm() -> _FcmClient:
    global _client
    if _client is None:
        _client = _FcmClient()
    return _client


def send_push_to_tokens(
    tokens: list[str],
    title: str,
    body: str,
    data: dict[str, Any] | None = None,
) -> tuple[int, list[str]]:
    """Birden cok token'a push gonder.

    Returns: (basarili sayisi, gecersiz token listesi)
    Caller gecersiz tokenlari DB'den silebilir.
    """
    fcm = get_fcm()
    if not fcm.enabled or not tokens:
        return 0, []
    success = 0
    invalid: list[str] = []
    str_data = {k: str(v) for k, v in (data or {}).items()}
    for tok in tokens:
        if not tok:
            continue
        ok, is_invalid = fcm.send(tok, title, body, str_data)
        if ok:
            success += 1
        if is_invalid:
            invalid.append(tok)
    return success, invalid
