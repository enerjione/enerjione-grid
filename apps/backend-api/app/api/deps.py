from fastapi import Cookie, Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer
from jose import JWTError, jwt
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.db.session import get_db
from app.models.enums import UserRole
from app.models.user import User

# Authorization: Bearer (eski yol) — auto_error=False, cookie fallback'i icin
# yokken bile dependency hata yerine None doner.
oauth2_scheme = OAuth2PasswordBearer(
    tokenUrl=f"{settings.api_prefix}/auth/login",
    auto_error=False,
)

# Cookie auth (yeni). `/auth/login` Set-Cookie ile yerlestiriyor.
_AUTH_COOKIE_NAME = "e1_session"


def get_current_user(
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
        raise credentials_exception
    try:
        payload = jwt.decode(token, settings.secret_key, algorithms=[settings.algorithm])
        username = payload.get("sub")
        if username is None:
            raise credentials_exception
        # Logout sonrasi revoke edilen jti'leri reddet (in-memory blacklist).
        jti = payload.get("jti")
        if jti:
            from app.services.auth_service import is_jti_revoked

            if is_jti_revoked(jti):
                raise credentials_exception
    except JWTError as ex:
        raise credentials_exception from ex

    stmt = select(User).where(User.username == username)
    user = db.scalar(stmt)
    if user is None:
        raise credentials_exception
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
