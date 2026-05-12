from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from pydantic import BaseModel

from app.core.config import settings
from app.core.rate_limit import limiter
from app.db.session import get_db
from app.models.user import User
from app.models.user_fcm_token import UserFcmToken
from app.schemas.auth import LoginRequest, TokenResponse
from app.schemas.user import LanguageUpdateRequest, SelfPasswordChangeRequest, SelfProfileUpdateRequest, UserRead


# Auth cookie ismi — frontend Authorization header yerine bu cookie ile
# gelirse `get_current_user` cookie'den okur. HttpOnly + Secure (production)
# + SameSite=Strict: XSS sonrasi token cikartilamaz + CSRF zorlasir.
_AUTH_COOKIE_NAME = "e1_session"


class FcmTokenRegisterRequest(BaseModel):
    token: str
    platform: str | None = None
    device_label: str | None = None


class FcmTokenDeleteRequest(BaseModel):
    token: str

# Frontend i18n ile uyumlu desteklenen diller. Yeni dil eklerken hem
# bu listeye hem de frontend resources/<code>.json dosyasina ekle.
SUPPORTED_LANGUAGES = {"tr", "en"}
from app.api.deps import get_current_user
from app.services.auth_service import create_access_token, get_password_hash, verify_password
from app.services.event_service import record_event

router = APIRouter(prefix="/auth", tags=["auth"])


@router.post("/login", response_model=TokenResponse)
@limiter.limit("5/minute")
def login(
    request: Request,
    response: Response,
    payload: LoginRequest,
    db: Session = Depends(get_db),
):
    """Login — brute-force koruma + HttpOnly cookie session.

    5/dakika online brute force'u zorlastirir (1M parola = 138 gun).
    6. istek 429 doner.

    Cookie auth (yeni, onerilen): basarili login'de `Set-Cookie: e1_session`
    HttpOnly + Secure (prod) + SameSite=Strict + Max-Age=access_token_minutes
    ile gonderilir. XSS sonrasi JS `document.cookie` okuyamaz (HttpOnly),
    token'i exfiltrate edemez. CSRF SameSite=Strict ile zorlasir.

    Geriye uyumluluk: response body'sinde `access_token` da donulur — eski
    frontend localStorage akisi calismaya devam eder. Bir sonraki major
    release'te body'den access_token cikarilacak.

    NOT: Reverse proxy arkasinda `X-Forwarded-For` spoof'a karsi koruma
    icin uvicorn `--forwarded-allow-ips=*` ile baslatilmali (Dockerfile CMD).

    Audit kaydi: yalnizca BASARILI login'de yazilir (DoS amplification onlemi).
    """
    _ = request  # slowapi key_func icin gerekli; kullanilmiyor
    stmt = select(User).where(User.username == payload.username)
    user = db.scalar(stmt)
    if not user or not verify_password(payload.password, user.hashed_password):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid username or password")

    access_token = create_access_token(user.username)

    # HttpOnly cookie — XSS sonrasi token exfiltrate olmaz.
    is_prod = settings.app_env.strip().lower() in ("production", "prod")
    response.set_cookie(
        key=_AUTH_COOKIE_NAME,
        value=access_token,
        max_age=settings.access_token_minutes * 60,
        httponly=True,
        secure=is_prod,
        samesite="strict",
        path="/",
    )

    record_event(
        db,
        category="auth",
        event_type="user_login",
        severity="info",
        actor_username=user.username,
        message=f"{user.username} signed in",
        i18n_key="user_login",
        i18n_params={"user": user.username},
    )
    db.commit()
    return TokenResponse(access_token=access_token, role=user.role, username=user.username)


@router.get("/me", response_model=UserRead)
def get_me(current_user: User = Depends(get_current_user)):
    return current_user


class WsTicketResponse(BaseModel):
    """WS handshake icin kisa-omurlu tek-kullanimlik bilet.

    Frontend HTTP ile bilet ister (auth header/cookie), sonra WS'i
    `?ticket=<TICKET>` ile acar. Eski yapida JWT URL query'sinde geliyordu
    → nginx access log + browser history + Referer'a sizar. Ticket 30sn
    TTL ve tek kullanim (consume sonrasi revoke).
    """

    ticket: str
    expires_in_sec: int


@router.post("/ws-ticket", response_model=WsTicketResponse)
def create_ws_ticket(current_user: User = Depends(get_current_user)):
    """WebSocket handshake icin tek-kullanimlik 30sn TTL bilet uretir.

    Bilet `username` ile in-memory cache'te tutulur; WS endpoint'i bileti
    consume edip revoke eder. Bu sayede:
      * JWT URL'de gorunmez (Authorization header / cookie auth-suz konum)
      * Ticket WS dosyalanmadan once revoke olur (replay yok)
      * Multi-replica deploy'da Redis'e tasinmasi gerek (TODO).
    """
    from app.services.auth_service import issue_ws_ticket

    ticket, ttl = issue_ws_ticket(current_user.username)
    return WsTicketResponse(ticket=ticket, expires_in_sec=ttl)


@router.patch("/me", response_model=UserRead)
def update_me(
    payload: SelfProfileUpdateRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    current_user.full_name = payload.full_name
    current_user.email = payload.email
    db.commit()
    db.refresh(current_user)
    return current_user


@router.put("/me/language", response_model=UserRead)
def update_my_language(
    payload: LanguageUpdateRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    code = (payload.language or "").strip().lower()
    if code not in SUPPORTED_LANGUAGES:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Desteklenmeyen dil: {payload.language}",
        )
    current_user.language = code
    db.commit()
    db.refresh(current_user)
    return current_user


@router.post("/me/change-password", status_code=status.HTTP_204_NO_CONTENT)
def change_my_password(
    payload: SelfPasswordChangeRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    if not verify_password(payload.current_password, current_user.hashed_password):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Current password is wrong")

    current_user.hashed_password = get_password_hash(payload.new_password)
    record_event(
        db,
        category="auth",
        event_type="password_changed",
        severity="info",
        actor_username=current_user.username,
        message=f"{current_user.username} changed password",
        i18n_key="password_changed",
        i18n_params={"user": current_user.username},
    )
    db.commit()
    return None


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
def logout(
    request: Request,
    response: Response,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Logout — JWT `jti` claim'ini revocation blacklist'ine ekle.

    Header'daki JWT decode edilir, jti + exp okunur ve blacklist'e konulur.
    Bu token'la yapilacak sonraki istekler 401 doner. Cati 0.x'te in-memory
    blacklist; multi-replica deploy icin Redis backend gerek (TODO).
    """
    # Authorization header'dan JWT'yi al, jti + exp cikar
    try:
        from jose import jwt as _jwt

        from app.core.config import settings as _settings
        from app.services.auth_service import revoke_jti

        auth_header = request.headers.get("authorization") or ""
        if auth_header.lower().startswith("bearer "):
            token = auth_header[7:].strip()
            payload = _jwt.decode(
                token, _settings.secret_key, algorithms=[_settings.algorithm]
            )
            jti = payload.get("jti")
            exp = payload.get("exp")
            if jti and exp:
                revoke_jti(str(jti), float(exp))
    except Exception:  # noqa: BLE001 — logout audit'i bozulmasin
        import logging as _logging

        _logging.getLogger(__name__).debug("logout_jti_revoke_failed", exc_info=True)

    # HttpOnly cookie'yi temizle — Set-Cookie ile expired tarih.
    # path="/" mutlaka login'deki ile ayni olmali yoksa tarayici silmez.
    response.delete_cookie(key=_AUTH_COOKIE_NAME, path="/")

    record_event(
        db,
        category="auth",
        event_type="user_logout",
        severity="info",
        actor_username=current_user.username,
        message=f"{current_user.username} signed out",
        i18n_key="user_logout",
        i18n_params={"user": current_user.username},
    )
    db.commit()
    return None


# ---------------------------------------------------------------------------
# FCM TOKEN MANAGEMENT (mobile push)
# ---------------------------------------------------------------------------


@router.post("/me/fcm-token", status_code=status.HTTP_204_NO_CONTENT)
def register_fcm_token(
    payload: FcmTokenRegisterRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Mobil cihaz token'ini kullaniciya kaydeder (idempotent).

    Ayni token zaten varsa user'a (yeni veya degisen sahibe) atanir ve
    last_seen guncellenir.
    """
    token = (payload.token or "").strip()
    if not token:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="token is empty")
    existing = db.scalar(select(UserFcmToken).where(UserFcmToken.token == token))
    if existing:
        existing.user_id = current_user.id
        existing.platform = payload.platform or existing.platform
        existing.device_label = payload.device_label or existing.device_label
    else:
        db.add(UserFcmToken(
            user_id=current_user.id,
            token=token,
            platform=payload.platform,
            device_label=payload.device_label,
        ))
    db.commit()
    return None


@router.delete("/me/fcm-token", status_code=status.HTTP_204_NO_CONTENT)
def delete_fcm_token(
    payload: FcmTokenDeleteRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Logout sirasinda mobil app kendi token'ini siler."""
    token = (payload.token or "").strip()
    if not token:
        return None
    row = db.scalar(select(UserFcmToken).where(
        UserFcmToken.token == token, UserFcmToken.user_id == current_user.id
    ))
    if row is not None:
        db.delete(row)
        db.commit()
    return None
