"""Authentication services — JWT token + parola hash + revocation.

Parola hash:
  * bcrypt (passlib[bcrypt]) — pbkdf2_sha256 yerine. GPU brute-force'a daha
    dayanikli; passlib auto-rehash ile eski PBKDF2 hash'leri verify ettikten
    sonra otomatik bcrypt'e yeniler.

JWT:
  * `jti` (UUID) her token'a eklenir — logout'ta blacklist'e konur.
  * `exp` (expiry) JWT spec ile dogrulanir.
  * Revocation in-memory `_REVOKED_JTI` setinde tutulur; max yas Settings'tan
    okunur (eski jti'ler expiry'lerinde silinir, set sinirsiz buyumesin).
    Multi-replica deploy'da Redis backend gerekir (TODO).
"""

from __future__ import annotations

import threading
import time
from datetime import datetime, timedelta, timezone
from uuid import uuid4

from jose import jwt
from passlib.context import CryptContext

from app.core.config import settings

# bcrypt + pbkdf2_sha256 fallback: eski kullanicilarin PBKDF2 hash'i
# verify edilir, ardindan needs_update bcrypt'e isaret eder ve uygulama
# tarafindan yeniden hash'lenir (auth/me endpoint'i bunu yapabilir).
pwd_context = CryptContext(
    schemes=["bcrypt", "pbkdf2_sha256"],
    default="bcrypt",
    deprecated=["pbkdf2_sha256"],
    # bcrypt cost factor: 12 production icin makul (~250ms/hash).
    bcrypt__rounds=12,
)


def verify_password(plain_password: str, hashed_password: str) -> bool:
    return pwd_context.verify(plain_password, hashed_password)


def needs_password_rehash(hashed_password: str) -> bool:
    """passlib auto-upgrade — PBKDF2 hash'leri bcrypt'e tasimak icin."""
    return pwd_context.needs_update(hashed_password)


def get_password_hash(password: str) -> str:
    return pwd_context.hash(password)


# ---- JWT revocation (logout blacklist) -------------------------------------
# In-memory set + lock. Production'da multi-replica icin Redis gerek.
_REVOKED_JTI: dict[str, float] = {}  # jti -> exp epoch
_REVOKED_LOCK = threading.Lock()


def _purge_expired_jti(now_epoch: float) -> None:
    """Expiry'leri gecmis jti'leri set'ten cikar — memory leak onlemi."""
    expired = [jti for jti, exp in _REVOKED_JTI.items() if exp < now_epoch]
    for jti in expired:
        _REVOKED_JTI.pop(jti, None)


def revoke_jti(jti: str, exp_epoch: float) -> None:
    """Token logout'ta jti'yi blacklist'e ekle. Expiry'sine kadar saklanir."""
    if not jti:
        return
    with _REVOKED_LOCK:
        _REVOKED_JTI[jti] = exp_epoch
        if len(_REVOKED_JTI) > 50_000:
            _purge_expired_jti(time.time())


def is_jti_revoked(jti: str | None) -> bool:
    if not jti:
        return False
    now = time.time()
    with _REVOKED_LOCK:
        exp = _REVOKED_JTI.get(jti)
        if exp is None:
            return False
        if exp < now:
            # Suresi gecmis; cleanup
            _REVOKED_JTI.pop(jti, None)
            return False
        return True


def create_access_token(subject: str, remember_me: bool = False) -> tuple[str, int]:
    """JWT olusturur. `jti` (UUID) revocation icin sart; `exp` JWT spec'i.

    `remember_me=True` ise `remember_me_token_minutes` (default 7 gun)
    TTL kullanilir; aksi takdirde `access_token_minutes` (default 8 saat).
    Donus: (token, ttl_seconds).
    """
    minutes = (
        settings.remember_me_token_minutes if remember_me else settings.access_token_minutes
    )
    expire = datetime.now(timezone.utc) + timedelta(minutes=minutes)
    jti = uuid4().hex
    payload = {"sub": subject, "exp": expire, "jti": jti}
    token = jwt.encode(payload, settings.secret_key, algorithm=settings.algorithm)
    return token, minutes * 60


# ---- WebSocket ticket store (URL'de JWT yerine kisa-omurlu ticket) ---------
# In-memory: ticket -> (username, exp_epoch). Multi-replica deploy'da Redis
# gerek. 30sn TTL; consume sonrasi revoke (tek kullanim).
_WS_TICKET_TTL_SEC = 30
_ws_tickets: dict[str, tuple[str, float]] = {}
_ws_tickets_lock = threading.Lock()


def issue_ws_ticket(username: str) -> tuple[str, int]:
    """Yeni WS ticket uret + cache'le. Returns (ticket, ttl_sec)."""
    ticket = uuid4().hex
    exp = time.time() + _WS_TICKET_TTL_SEC
    with _ws_tickets_lock:
        # Periyodik cleanup — expired ticket'lari at (cache leak onlemi)
        now = time.time()
        if len(_ws_tickets) > 1000:
            expired = [t for t, (_, e) in _ws_tickets.items() if e < now]
            for t in expired:
                _ws_tickets.pop(t, None)
        _ws_tickets[ticket] = (username, exp)
    return ticket, _WS_TICKET_TTL_SEC


def consume_ws_ticket(ticket: str) -> str | None:
    """Ticket'i pop edip username don. Gecersiz/expired → None.

    Tek kullanim: pop edildiginde cache'ten silinir; ayni ticket ikinci
    kez kullanilamaz (WS replay attack korumasi).
    """
    if not ticket:
        return None
    now = time.time()
    with _ws_tickets_lock:
        entry = _ws_tickets.pop(ticket, None)
    if entry is None:
        return None
    username, exp = entry
    if exp < now:
        return None
    return username
