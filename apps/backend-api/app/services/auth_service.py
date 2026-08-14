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


def create_access_token(subject: str, remember_me: bool = False) -> tuple[str, int, str]:
    """JWT olusturur. `jti` (UUID) revocation icin sart; `exp` JWT spec'i.

    `remember_me=True` ise `remember_me_token_minutes` (default 7 gun)
    TTL kullanilir; aksi takdirde `access_token_minutes` (default 8 saat).
    Donus: (token, ttl_seconds, jti).
    """
    minutes = (
        settings.remember_me_token_minutes if remember_me else settings.access_token_minutes
    )
    expire = datetime.now(timezone.utc) + timedelta(minutes=minutes)
    jti = uuid4().hex
    payload = {"sub": subject, "exp": expire, "jti": jti}
    token = jwt.encode(payload, settings.secret_key, algorithm=settings.algorithm)
    return token, minutes * 60, jti


# ---- WebSocket ticket store (URL'de JWT yerine kisa-omurlu ticket) ---------
# DB'de (`ws_tickets` tablosu), surec-ici bir dict'te DEGIL: bilet
# `/auth/ws-ticket` ile uretilip AYRI bir TCP baglantisi olan WS upgrade
# istegiyle tuketiliyor. `E1_API_WORKERS>1` iken bu iki istek farkli
# uvicorn sureclerine dusebilir; surec-ici saklama bileti "kayip"
# gosteriyor ve baglanti 1008 ile kapaniyordu (bkz. models/ws_ticket.py).
#
# 30sn TTL; consume sonrasi silinir (tek kullanim).
#
# `jti` NEDEN tasiniyor: WS baglantisi uzun omurlu. Bilet uretilirken oturum
# gecerliydi ama installer "oturumu at" dedikten sonra da soket akmaya devam
# ederse iptal islevsiz kalir. Bilet jti'yi tasidigi icin WS endpoint'i hem
# handshake aninda hem de baglanti boyunca periyodik olarak
# `UserSession.revoked_at` / `is_jti_revoked` kontrolu yapabiliyor.
_WS_TICKET_TTL_SEC = 30


def issue_ws_ticket(username: str, jti: str | None = None) -> tuple[str, int]:
    """Yeni WS ticket uret + kaydet. Returns (ticket, ttl_sec).

    `jti` bileti ureten oturumun token kimligi; WS tarafinda iptal kontrolu
    icin saklanir. None ise (eski cagri sekli) iptal kontrolu yapilamaz.

    Kisa-omurlu kendi session'ini acar: cagiran uclarin imzasi degismesin
    diye (`_is_session_revoked` ile ayni desen).
    """
    from sqlalchemy import delete as _delete

    from app.db.session import SessionLocal
    from app.models.ws_ticket import WsTicket

    ticket = uuid4().hex
    now = datetime.now(timezone.utc)
    expires_at = now + timedelta(seconds=_WS_TICKET_TTL_SEC)
    db = SessionLocal()
    try:
        # Firsatci temizlik: suresi dolmuslari at. Bilet omru 30sn oldugu
        # icin tablo normalde birkac satir; ayri bir retention isi gerekmez.
        db.execute(_delete(WsTicket).where(WsTicket.expires_at < now))
        db.add(WsTicket(ticket=ticket, username=username, jti=jti, expires_at=expires_at))
        db.commit()
    finally:
        db.close()
    return ticket, _WS_TICKET_TTL_SEC


def consume_ws_ticket(ticket: str) -> tuple[str, str | None] | None:
    """Ticket'i tuketip (username, jti) don. Gecersiz/expired → None.

    Tek kullanim: silme `rowcount` ile dogrulanir, yani ayni bileti iki
    surec es zamanli tuketmeye calisirsa YALNIZCA BIRI kazanir (WS replay
    attack korumasi surecler arasinda da gecerli).
    """
    if not ticket:
        return None
    from sqlalchemy import delete as _delete

    from app.db.session import SessionLocal
    from app.models.ws_ticket import WsTicket

    db = SessionLocal()
    try:
        row = db.get(WsTicket, ticket)
        if row is None:
            return None
        username, jti, expires_at = row.username, row.jti, row.expires_at
        # Yarisi kazanan tek surec: rowcount 0 ise bileti baskasi kapmis.
        deleted = db.execute(_delete(WsTicket).where(WsTicket.ticket == ticket)).rowcount
        db.commit()
        if not deleted:
            return None
        if expires_at is not None:
            # SQLite gibi tz tasimayan backend'lerde naive donebilir; naive
            # degeri UTC sayiyoruz (yazarken UTC yazildi).
            if expires_at.tzinfo is None:
                expires_at = expires_at.replace(tzinfo=timezone.utc)
            if expires_at < datetime.now(timezone.utc):
                return None
        return username, jti
    finally:
        db.close()
