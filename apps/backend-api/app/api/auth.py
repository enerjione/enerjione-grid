from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from pydantic import BaseModel

from app.core.config import settings
from app.core.rate_limit import limiter
from app.db.session import get_db
from app.models.user import User
from app.models.user_fcm_token import UserFcmToken
from app.schemas.auth import LoginRequest, SetupPasswordRequest, TokenResponse
from app.schemas.user import LanguageUpdateRequest, SelfPasswordChangeRequest, SelfProfileUpdateRequest, UserRead


# Auth cookie ismi — frontend Authorization header yerine bu cookie ile
# gelirse `get_current_user` cookie'den okur. HttpOnly + Secure (production)
# + SameSite=Strict: XSS sonrasi token cikartilamaz + CSRF zorlasir.
_AUTH_COOKIE_NAME = "e1_session"

# Account lockout esikleri. SlowAPI IP-bazli 5/dk limitin UZERINE eklenen
# hesap-bazli savunma — saldirgan farkli IP'lerden veya X-Forwarded-For
# spoof ile gelse bile ayni username icin 10 hatadan sonra hesap kilitlenir.
_MAX_FAILED_LOGIN = 10
_LOCKOUT_MINUTES = 15


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

    # Kilitli hesap kontrolu — kullanici varsa ve locked_until > now ise
    # parola dogrulamayi atla, dogrudan 423 don. Boylece kilit suresi
    # icinde dogru parola bile login etmez (kasten — saldirgan dogru
    # parolayi bulmus olsa bile kilit suresi bitene kadar beklemeli).
    now = datetime.now(timezone.utc)
    if user is not None and user.locked_until is not None and user.locked_until > now:
        remaining = int((user.locked_until - now).total_seconds())
        raise HTTPException(
            status_code=status.HTTP_423_LOCKED,
            detail=f"Account temporarily locked. Try again in {remaining // 60 + 1} minute(s).",
        )

    # Davet edilmis ama henuz sifre belirlememis kullanici (hashed_password=NULL)
    # icin verify_password False doner — bu davranis istenen: token sahibi
    # olmayan kimse giremez. Kullaniciya UI'da "henuz aktivasyon yapilmadi"
    # mesaji gosterilmek istenirse ayri bir status code donulebilir; simdilik
    # standart 401 (enumeration koruma) yeterli.
    if not user or not user.hashed_password or not verify_password(payload.password, user.hashed_password):
        # Basarisiz deneme — user varsa sayaci artir; esik asilinca kilitle.
        # User yoksa kayit tutulmaz (enumeration korumasi: sayac sahte
        # username'lerle baska bir hesabi kilitlemek icin kullanilamaz).
        if user is not None:
            user.failed_login_count = (user.failed_login_count or 0) + 1
            locked = False
            if user.failed_login_count >= _MAX_FAILED_LOGIN:
                user.locked_until = now + timedelta(minutes=_LOCKOUT_MINUTES)
                locked = True
            # Audit kaydi: basarisiz deneme + kilit. record_event commit yapmaz;
            # asagidaki db.commit() topluca yazar.
            record_event(
                db,
                category="auth",
                event_type="login_failed_locked" if locked else "login_failed",
                severity="warning" if locked else "info",
                actor_username=user.username,
                message=(
                    f"{user.username} hesabi {_LOCKOUT_MINUTES} dk kilitlendi "
                    f"({_MAX_FAILED_LOGIN} basarisiz deneme)"
                    if locked
                    else f"{user.username} basarisiz login denemesi "
                    f"({user.failed_login_count}/{_MAX_FAILED_LOGIN})"
                ),
                i18n_key="login_failed_locked" if locked else "login_failed",
                i18n_params={
                    "user": user.username,
                    "count": user.failed_login_count,
                    "max": _MAX_FAILED_LOGIN,
                    "minutes": _LOCKOUT_MINUTES,
                },
            )
            db.commit()
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid username or password")

    # Basarili login — sayaci ve kilit alanini temizle. Bir onceki basarisiz
    # denemeleri sifirla, boylece "9 hatadan sonra 1 dogru" senaryosunda
    # bir sonraki yanlis 1. denemeden tekrar sayilir.
    if user.failed_login_count or user.locked_until:
        user.failed_login_count = 0
        user.locked_until = None

    access_token, ttl_sec = create_access_token(user.username, remember_me=payload.remember_me)

    # HttpOnly cookie — XSS sonrasi token exfiltrate olmaz. Cookie max_age
    # token'in gercek TTL'i ile ayni (remember_me=true ise 7 gun, aksi
    # halde 8 saat).
    is_prod = settings.app_env.strip().lower() in ("production", "prod")
    response.set_cookie(
        key=_AUTH_COOKIE_NAME,
        value=access_token,
        max_age=ttl_sec,
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
    return TokenResponse(
        access_token=access_token,
        role=user.role,
        username=user.username,
        must_change_password=bool(user.must_change_password),
    )


@router.get("/me", response_model=UserRead)
def get_me(current_user: User = Depends(get_current_user)):
    return current_user


@router.post("/setup-password", status_code=status.HTTP_204_NO_CONTENT)
@limiter.limit("5/minute")
def setup_password(
    request: Request,
    payload: SetupPasswordRequest,
    db: Session = Depends(get_db),
):
    """Davet edilmis kullanici ilk sifresini token ile belirler.

    Admin yeni user yarattiginda token uretilir + email/link gonderilir.
    Kullanici setup-password sayfasinda token + yeni sifre POST eder; backend
    SHA-256 hash'i ile eslestirip sifreyi set eder.

    Tek kullanim: basari sonrasi token_hash NULL'a alinir; ayni link tekrar
    kullanilamaz. 7 gunluk TTL (token_expires_at).

    Auth gerekmez (token zaten secret) ama rate-limit 5/dk brute-force korumasi.
    """
    _ = request
    import hashlib

    token = (payload.token or "").strip()
    new_pwd = (payload.new_password or "")
    if not token or len(token) < 16:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid token")
    if len(new_pwd) < 8:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Sifre en az 8 karakter olmali.",
        )

    token_hash = hashlib.sha256(token.encode("utf-8")).hexdigest()
    user = db.scalar(
        select(User).where(User.password_reset_token_hash == token_hash)
    )
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Token gecersiz veya kullanilmis.",
        )
    now = datetime.now(timezone.utc)
    if user.password_reset_token_expires_at is None or user.password_reset_token_expires_at < now:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Token suresi dolmus. Admin'den yeni davet linki isteyin.",
        )

    user.hashed_password = get_password_hash(new_pwd)
    user.password_reset_token_hash = None
    user.password_reset_token_expires_at = None
    user.must_change_password = False
    user.failed_login_count = 0
    user.locked_until = None

    record_event(
        db,
        category="auth",
        event_type="password_setup",
        severity="info",
        actor_username=user.username,
        message=f"{user.username} davet token'i ile sifresini belirledi",
        i18n_key="password_setup",
        i18n_params={"user": user.username},
    )
    db.commit()
    return None


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
@limiter.limit("60/minute")
def create_ws_ticket(
    request: Request,
    current_user: User = Depends(get_current_user),
):
    """WebSocket handshake icin tek-kullanimlik 30sn TTL bilet uretir.

    Bilet `username` ile in-memory cache'te tutulur; WS endpoint'i bileti
    consume edip revoke eder. Bu sayede:
      * JWT URL'de gorunmez (Authorization header / cookie auth-suz konum)
      * Ticket WS dosyalanmadan once revoke olur (replay yok)
      * Multi-replica deploy'da Redis'e tasinmasi gerek (TODO).

    Rate-limit 60/dakika — normal client'lar reconnect sirasinda 1-2 bilet
    alir; abusive client (in-memory cache flood) engellenir.
    """
    _ = request  # slowapi key_func icin gerekli
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
@limiter.limit("10/minute")
def change_my_password(
    request: Request,
    payload: SelfPasswordChangeRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    _ = request  # slowapi key_func icin gerekli
    # 10/dk: kullanici kendi parola degistirme isteklerini yapabilir ama
    # ele gecirilmis oturumdan brute-force "current_password" denemesi
    # zorlasir (1M parola = 69 gun).
    if not current_user.hashed_password or not verify_password(
        payload.current_password, current_user.hashed_password
    ):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Current password is wrong")
    # Yeni sifre eski ile ayni ise reddet (force-change senaryosunda
    # default-pwd'yi tekrar girip flag'i kapatma fail-open'i onler).
    if verify_password(payload.new_password, current_user.hashed_password):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Yeni sifre eskisiyle ayni olamaz.",
        )
    # Force-change flag'ini sifirla — kullanici basariyla sifresini degistirdi.
    current_user.must_change_password = False

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
    # JWT'yi HEM Authorization header'dan HEM cookie'den dene — cookie-only
    # frontend kullanicilari icin cookie'deki jti'yi de revoke et. Yoksa
    # cookie silinse bile token TTL bitene kadar Bearer ile yeniden
    # kullanilabilirdi. Iki kaynaktan da decode ederiz; ayni jti olsa bile
    # revoke_jti idempotent (set'e ekler).
    try:
        from jose import jwt as _jwt

        from app.core.config import settings as _settings
        from app.services.auth_service import revoke_jti

        tokens_to_revoke: list[str] = []
        auth_header = request.headers.get("authorization") or ""
        if auth_header.lower().startswith("bearer "):
            tokens_to_revoke.append(auth_header[7:].strip())
        cookie_token = request.cookies.get(_AUTH_COOKIE_NAME)
        if cookie_token:
            tokens_to_revoke.append(cookie_token.strip())

        for token in tokens_to_revoke:
            if not token:
                continue
            try:
                payload = _jwt.decode(
                    token, _settings.secret_key, algorithms=[_settings.algorithm]
                )
                jti = payload.get("jti")
                exp = payload.get("exp")
                if jti and exp:
                    revoke_jti(str(jti), float(exp))
            except Exception:  # noqa: BLE001
                # Tek bir token decode hatasi diger token'lari engellemesin
                pass
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
@limiter.limit("20/minute")
def register_fcm_token(
    request: Request,
    payload: FcmTokenRegisterRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Mobil cihaz token'ini kullaniciya kaydeder (idempotent).

    Ayni token zaten varsa user'a (yeni veya degisen sahibe) atanir ve
    last_seen guncellenir.

    Rate-limit 20/dk — normal mobile reconnect 1-2 cagri yapar; ele
    gecirilmis user-account ile token cycling DB row spam'i engellenir.
    """
    _ = request  # slowapi key_func
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
@limiter.limit("20/minute")
def delete_fcm_token(
    request: Request,
    payload: FcmTokenDeleteRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Logout sirasinda mobil app kendi token'ini siler."""
    _ = request
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
