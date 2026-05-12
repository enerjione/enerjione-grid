"""Public REST API icin auth dependency'leri.

Kullanim:

    from app.api.public_deps import require_api_key, require_scope

    @router.get("/devices", dependencies=[Depends(require_scope("devices:read"))])
    def list_devices(...): ...

`require_api_key` token'i dogrular ve `ApiKeyContext` doner; `require_scope`
ek olarak belirli scope'un olmasini sart kosar.

Audit log:
  - 401 (token gecersiz) → `api_key_auth_failed`
  - 403 (scope yok)      → `api_key_scope_denied`
  - 200 (basarili)       → her N requestte bir `api_key_access` (volume azalt)
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from threading import Lock

from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.models.api_key import ApiKey
from app.models.user import User
from app.services import api_key_service
from app.services.event_service import record_event


# DoS amplification koruma: basarisiz API key auth her denemede DB INSERT +
# commit yapiyor → saldirgan saniyede 1000 invalid token gondererek
# system_events tablosunu sisirip diski doldurabilir. Bu cache (ip, reason)
# basina ayni event'i 60 saniyede sadece 1 kez yazar; aradaki tum istekler
# `_unauthorized()` doner ama audit'e gitmez (slowapi rate-limit ayri
# katman olarak DoS'u zaten kapsar).
_AUDIT_DEDUP: dict[tuple[str, str], float] = {}
_AUDIT_LOCK = Lock()
_AUDIT_DEDUP_TTL_SEC = 60.0


def _should_record_audit(ip: str | None, reason: str) -> bool:
    """IP + reason kombinasyonu icin son audit'ten >=60sn gectiyse True."""
    key = ((ip or "?"), reason)
    now = time.monotonic()
    with _AUDIT_LOCK:
        last = _AUDIT_DEDUP.get(key, 0.0)
        if now - last < _AUDIT_DEDUP_TTL_SEC:
            return False
        _AUDIT_DEDUP[key] = now
        # Cache size cap (memory leak onlemi): 10K key'i asarsa eski yarisini at.
        if len(_AUDIT_DEDUP) > 10_000:
            cutoff = now - _AUDIT_DEDUP_TTL_SEC
            for k in list(_AUDIT_DEDUP.keys()):
                if _AUDIT_DEDUP[k] < cutoff:
                    _AUDIT_DEDUP.pop(k, None)
    return True

# auto_error=False → eksik header'da kendi mesajimizi donuyoruz.
bearer_scheme = HTTPBearer(auto_error=False, scheme_name="ApiKey")


@dataclass
class ApiKeyContext:
    """Endpoint icine gonderilen dogrulanmis API key + sahibi."""

    api_key: ApiKey
    user: User


def _client_ip(request: Request) -> str | None:
    # Reverse proxy varsa X-Forwarded-For ilk IP'yi kullan.
    xff = request.headers.get("x-forwarded-for")
    if xff:
        return xff.split(",")[0].strip()
    if request.client:
        return request.client.host
    return None


def _unauthorized(detail: str = "Geçersiz veya eksik API anahtarı") -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail=detail,
        headers={"WWW-Authenticate": 'Bearer realm="public-api"'},
    )


def require_api_key(
    request: Request,
    creds: HTTPAuthorizationCredentials | None = Depends(bearer_scheme),
    db: Session = Depends(get_db),
) -> ApiKeyContext:
    """Header'dan Bearer token oku, DB'de dogrula, ApiKeyContext don.

    401 alternatifleri:
      - Header yok.
      - Bearer disinda scheme.
      - Token DB'de yok.
      - Token revoke/expired/deactivated.
      - allowed_ips check fail.
    """
    if creds is None or creds.scheme.lower() != "bearer":
        raise _unauthorized("API anahtarı eksik (Authorization: Bearer <token>)")

    token = creds.credentials or ""
    if not token.startswith(api_key_service.TOKEN_PREFIX):
        # Yanlis sistem token'i (orn. JWT) buraya gelmis olabilir.
        raise _unauthorized("Geçersiz API anahtarı formatı")

    ip = _client_ip(request)
    row = api_key_service.find_active_by_token(db, token)
    if row is None:
        if _should_record_audit(ip, "unknown_token"):
            record_event(
                db, category="security", event_type="api_key_auth_failed",
                severity="warning",
                message=f"Bilinmeyen API anahtarı ile erişim denemesi",
                metadata={"ip": ip, "path": str(request.url.path)},
            )
            db.commit()
        raise _unauthorized()

    ok, reason = api_key_service.is_usable(row)
    if not ok:
        if _should_record_audit(ip, f"unusable:{reason}"):
            record_event(
                db, category="security", event_type="api_key_auth_failed",
                severity="warning",
                message=f"Kullanılamaz API anahtarı: {reason}",
                metadata={
                    "api_key_id": row.id,
                    "user_id": row.user_id,
                    "reason": reason,
                    "ip": ip,
                    "path": str(request.url.path),
                },
            )
            db.commit()
        raise _unauthorized()

    if not api_key_service.ip_allowed(row, ip):
        if _should_record_audit(ip, "ip_whitelist_denied"):
            record_event(
                db, category="security", event_type="api_key_auth_failed",
                severity="warning",
                message="API anahtarı IP whitelist dışından kullanıldı",
                metadata={
                    "api_key_id": row.id,
                    "user_id": row.user_id,
                    "ip": ip,
                    "path": str(request.url.path),
                },
            )
            db.commit()
        raise _unauthorized()

    # Sahibini ekle
    user = db.get(User, row.user_id)
    if user is None:
        # Kullanici silinmis ama key kalmis — guvenli tarafa dus.
        raise _unauthorized()

    # last_used_at debounced update.
    try:
        api_key_service.touch_last_used(db, row)
    except Exception:  # noqa: BLE001
        # last_used update kritik degil; auth basarilai sayilir.
        db.rollback()

    return ApiKeyContext(api_key=row, user=user)


def require_scope(scope: str):
    """Belirli bir scope'a sahip API key gerektiren bir dependency factory."""

    def _checker(
        ctx: ApiKeyContext = Depends(require_api_key),
        db: Session = Depends(get_db),
        request: Request = None,  # type: ignore[assignment]
    ) -> ApiKeyContext:
        if not api_key_service.has_scope(ctx.api_key, scope):
            record_event(
                db, category="security", event_type="api_key_scope_denied",
                severity="warning",
                message=f"API anahtarı '{scope}' scope'una sahip değil",
                metadata={
                    "api_key_id": ctx.api_key.id,
                    "user_id": ctx.user.id,
                    "scope_required": scope,
                    "scopes_present": ctx.api_key.scopes,
                    "ip": _client_ip(request) if request else None,
                    "path": str(request.url.path) if request else None,
                },
            )
            db.commit()
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Bu API anahtarında '{scope}' yetkisi yok",
            )
        return ctx

    return _checker
