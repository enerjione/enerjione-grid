import logging
import threading
from datetime import datetime, timezone

from fastapi import Cookie, Depends, HTTPException, Request, status
from fastapi.security import OAuth2PasswordBearer
from jose import JWTError, jwt
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.db.session import get_db
from app.models.enums import UserRole
from app.models.user import User

logger = logging.getLogger(__name__)

# last_seen_at debounce — her istekte DB UPDATE yapmamak icin in-memory
# son yazma zamanlarini tut. Aynı jti icin son yazmadan 30sn gecmediyse
# atla. Container restart sonrasi cache sifirlanir; ilk istek yine bir
# UPDATE atar — sorun degil.
_LAST_SEEN_DEBOUNCE_SEC = 30.0
_last_seen_writes: dict[str, float] = {}
_last_seen_lock = threading.Lock()


def _touch_user_session(db: Session, jti: str, client_ip: str | None = None) -> None:
    """jti icin DB user_sessions.last_seen_at = now (debounce 30sn).

    `client_ip` verilirse ve kayitlidan farkliysa `ip_address` da guncellenir —
    kullanici ag degistirdiginde (VPN, mobil, farkli sube) 'Aktif Oturumlar'
    login anindaki eski IP'yi degil guncel IP'yi gosterir.

    Hata olursa sessizce yutulur (auth akisi devam etmeli).
    """
    if not jti:
        return
    now_ts = datetime.now(timezone.utc).timestamp()
    with _last_seen_lock:
        last = _last_seen_writes.get(jti, 0.0)
        if now_ts - last < _LAST_SEEN_DEBOUNCE_SEC:
            return
        _last_seen_writes[jti] = now_ts
        # Cache buyumesin: 50000+ giris varsa eski olanlari at
        if len(_last_seen_writes) > 50000:
            cutoff = now_ts - 3600
            for k in [k for k, v in _last_seen_writes.items() if v < cutoff]:
                _last_seen_writes.pop(k, None)
    try:
        from app.models.user_session import UserSession
        sess = db.get(UserSession, jti)
        if sess is not None and sess.revoked_at is None:
            sess.last_seen_at = datetime.now(timezone.utc)
            if client_ip and sess.ip_address != client_ip:
                sess.ip_address = client_ip
            db.commit()
    except Exception:  # noqa: BLE001
        # Auth akisini bloklamamak icin sessiz fallback
        try:
            db.rollback()
        except Exception:  # noqa: BLE001
            pass

# Authorization: Bearer (eski yol) — auto_error=False, cookie fallback'i icin
# yokken bile dependency hata yerine None doner.
oauth2_scheme = OAuth2PasswordBearer(
    tokenUrl=f"{settings.api_prefix}/auth/login",
    auto_error=False,
)

# Cookie auth (yeni). `/auth/login` Set-Cookie ile yerlestiriyor.
_AUTH_COOKIE_NAME = "e1_session"


def get_current_user(
    request: Request,
    db: Session = Depends(get_db),
    header_token: str | None = Depends(oauth2_scheme),
    cookie_token: str | None = Cookie(default=None, alias=_AUTH_COOKIE_NAME),
) -> User:
    """JWT'yi Authorization header VEYA HttpOnly cookie'den oku.

    Oncelik: cookie > Authorization header. Cookie HttpOnly oldugu icin
    XSS bypass riski daha dusuk; modern frontend bunu kullanacak. Eski
    localStorage akisi henuz kaldirilmadigi icin header da kabul edilir
    (geri uyumluluk). Iki yontemde de ayni `jti` revocation kontrolu.
    """
    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Could not validate credentials",
        headers={"WWW-Authenticate": "Bearer"},
    )
    token = cookie_token or header_token
    if not token:
        # TESHIS: 401'in en yaygın sebebi browser cookie'sinin gelmemesi
        # (samesite=strict, secure flag, vs.). Bunu acikca log'la ki
        # operator backend log'unda sebebini gorebilsin.
        logger.warning(
            "auth_401_no_token cookie_present=%s header_present=%s",
            cookie_token is not None, header_token is not None,
        )
        raise credentials_exception
    source = "cookie" if cookie_token else "header"
    try:
        payload = jwt.decode(token, settings.secret_key, algorithms=[settings.algorithm])
        username = payload.get("sub")
        if username is None:
            logger.warning("auth_401_no_sub source=%s", source)
            raise credentials_exception
        # Logout sonrasi revoke edilen jti'leri reddet — once in-memory
        # blacklist'i kontrol et (hizli), sonra DB'deki UserSession.revoked_at'a
        # bak (container restart sonrasi memory list bos olsa bile installer'in
        # 'oturum at' aksiyonu kalici olur).
        jti = payload.get("jti")
        if jti:
            from app.services.auth_service import is_jti_revoked

            if is_jti_revoked(jti):
                logger.warning("auth_401_jti_revoked source=%s jti=%s", source, jti[:8])
                raise credentials_exception
            # DB tarafindaki revoke kontrolu
            try:
                from app.models.user_session import UserSession
                sess = db.get(UserSession, str(jti))
                if sess is not None and sess.revoked_at is not None:
                    logger.warning("auth_401_session_revoked_db source=%s jti=%s", source, str(jti)[:8])
                    raise credentials_exception
            except HTTPException:
                raise
            except Exception:  # noqa: BLE001
                # DB lookup hatasi auth'u bloklamasin
                pass
    except JWTError as ex:
        logger.warning("auth_401_jwt_decode_failed source=%s error=%s", source, ex)
        raise credentials_exception from ex

    stmt = select(User).where(User.username == username)
    user = db.scalar(stmt)
    if user is None:
        logger.warning("auth_401_user_not_found source=%s username=%s", source, username)
        raise credentials_exception
    # Aktif oturum tracking — debounce'lu last_seen_at + ip_address update.
    if jti:
        from app.core.client_ip import client_ip_from_request

        _touch_user_session(db, str(jti), client_ip_from_request(request))
    return user


def require_role(role: UserRole):
    def checker(user: User = Depends(get_current_user)) -> User:
        if user.role != role:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Not enough permissions")
        return user

    return checker


def require_roles(roles: list[UserRole]):
    def checker(user: User = Depends(get_current_user)) -> User:
        if user.role not in roles:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Not enough permissions")
        return user

    return checker
