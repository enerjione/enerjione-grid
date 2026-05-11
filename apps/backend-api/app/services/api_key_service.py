"""API Key (Personal Access Token) servis katmani.

Token uretimi, dogrulama, scope kontrolu burada toplanir.

Token formati:  `hsl_pat_<43-char-base64url>`
  - Prefix `hsl_pat_` GitHub PAT tarzi: kazara push edilen token'lar GitHub
    secret scanning vb. tarafindan kolayca tespit edilir.
  - 43 karakter = 32 byte raw secret, base64url'de (yastik karakter olmadan).

Saklama:
  - DB'de yalnizca SHA-256 hash tutulur (64 hex karakter).
  - Plain token, olusturulma yanitinda bir kerelik gosterilir; daha sonra
    sahibinden dahi tekrar erisilemez (kazara loglara dusmemesi icin).

Dogrulama:
  - Authorization: Bearer <token> header'i parse edilir.
  - Token'in sha256'si DB'de aranir.
  - revoked_at NULL olmali, is_active=True olmali, expires_at gecmemis olmali.
  - allowed_ips dolu ise istemci IP'si listede olmali.
  - Scope check: `require_scope("telemetry:read")` ile endpoint tarafinda yapilir.
"""

from __future__ import annotations

import hashlib
import hmac
import secrets
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.api_key import ApiKey

# Token prefix — kazara push edilen anahtarlar icin string match kolaylasir.
TOKEN_PREFIX = "hsl_pat_"

# Desteklenen scope'lar. Yeni scope eklerken bu listeyi guncelle ve
# /api/v1/public/* endpoint'lerinde require_scope ile kontrol et.
ALLOWED_SCOPES: frozenset[str] = frozenset(
    {
        # Cihaz ve sinyal kataloglarini okuma.
        "devices:read",
        "signals:read",
        # Telemetri (son deger / gecmis) okuma.
        "telemetry:read",
        # Alarm event listesi okuma.
        "alarms:read",
        # Sistem durumu (gateway listesi, sistem saglik metrigi) okuma.
        "system:read",
    }
)

DEFAULT_SCOPES = ("telemetry:read", "devices:read", "alarms:read")


def generate_token() -> tuple[str, str, str]:
    """Yeni token uret. Doner: (plain_token, sha256_hash, prefix_for_display)."""
    secret = secrets.token_urlsafe(32)  # 43 char base64url
    plain = f"{TOKEN_PREFIX}{secret}"
    token_hash = hashlib.sha256(plain.encode("utf-8")).hexdigest()
    # UI'da listenirken hangi token oldugunu hatirlamak icin ilk birkac karakter.
    # GitHub gibi: ilk 8 + son 4 karakter. Toplam <= 20.
    prefix_display = f"{plain[:12]}…{plain[-4:]}"
    return plain, token_hash, prefix_display


def hash_token(plain: str) -> str:
    """Bir plain token'in sha256 hash'ini hesaplar."""
    return hashlib.sha256(plain.encode("utf-8")).hexdigest()


def validate_scopes(scopes: list[str] | None) -> str:
    """Verilen scope listesini kontrol eder; CSV string olarak doner.

    None veya bos liste → DEFAULT_SCOPES kullanilir.
    Bilinmeyen scope varsa ValueError fırlatir (FastAPI 422 yapar).
    """
    if not scopes:
        return ",".join(DEFAULT_SCOPES)
    cleaned: list[str] = []
    for s in scopes:
        if s not in ALLOWED_SCOPES:
            raise ValueError(f"Bilinmeyen scope: {s}. İzin verilen: {sorted(ALLOWED_SCOPES)}")
        cleaned.append(s)
    if not cleaned:
        return ",".join(DEFAULT_SCOPES)
    # Tekrarlar temizlenir.
    return ",".join(sorted(set(cleaned)))


def csv_to_list(csv: str | None) -> list[str]:
    """`"a,b,c"` → `["a", "b", "c"]`. None/bos → []."""
    if not csv:
        return []
    return [s.strip() for s in csv.split(",") if s.strip()]


def find_active_by_token(db: Session, plain_token: str) -> ApiKey | None:
    """Plain token'i SHA-256 hash'leyip DB'de ara.

    Anahtar bulunmasa bile sabit zaman gecirelim diye sembolik bir compare
    yapariz; timing attack riski minimal.
    """
    target = hash_token(plain_token)
    row = db.scalar(select(ApiKey).where(ApiKey.token_hash == target))
    if row is None:
        # Constant-time fake compare; saldirgan "anahtar var mi yok mu" timing'i
        # ile cikarmamali.
        hmac.compare_digest(target, "0" * 64)
        return None
    return row


def is_usable(row: ApiKey, now: datetime | None = None) -> tuple[bool, str | None]:
    """Token'in **simdi** kullanilabilir olup olmadigini doner.

    Returns: (ok, reason_if_not). reason istemciye gonderilmez; loga yazilir.
    """
    if now is None:
        now = datetime.now(timezone.utc)
    if row.revoked_at is not None:
        return False, "revoked"
    if not row.is_active:
        return False, "deactivated"
    if row.expires_at is not None and row.expires_at <= now:
        return False, "expired"
    return True, None


def ip_allowed(row: ApiKey, client_ip: str | None) -> bool:
    """allowed_ips bos = serbest; doluysa client_ip listede olmali."""
    if not row.allowed_ips:
        return True
    if not client_ip:
        return False
    allowed = {s.strip() for s in row.allowed_ips.split(",") if s.strip()}
    return client_ip in allowed


def has_scope(row: ApiKey, scope: str) -> bool:
    if not row.scopes:
        return False
    scopes = {s.strip() for s in row.scopes.split(",") if s.strip()}
    return scope in scopes


def touch_last_used(db: Session, row: ApiKey, now: datetime | None = None) -> None:
    """Token kullanildi — last_used_at'i guncelle.

    Debounce: 60 sn'den daha yeni bir guncelleme varsa atla. Boylece her
    request'te DB write olmaz.
    """
    if now is None:
        now = datetime.now(timezone.utc)
    if row.last_used_at is not None:
        if (now - row.last_used_at).total_seconds() < 60:
            return
    row.last_used_at = now
    db.add(row)
    db.commit()
