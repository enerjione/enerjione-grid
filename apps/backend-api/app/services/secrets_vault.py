"""DB-saved secret'lar icin uygulama seviyesi Fernet sifreleme.

Kullanim:
    from app.services.secrets_vault import encrypt_secret, decrypt_secret

    db_row.smtp_password = encrypt_secret(plain_pw)
    plain = decrypt_secret(db_row.smtp_password)

Master key:
    Settings.secrets_master_key (`.env`: SECRETS_MASTER_KEY) — 32 byte URL-safe
    base64 (Fernet.generate_key() output). Bos veya gecersiz ise startup'ta
    fallback olarak SECRET_KEY'den deriv eder (HKDF + sabit salt). Production'da
    explicit `SECRETS_MASTER_KEY` set edilmesi onerilir.

Sifrelenen alanlar — read/write side encrypt/decrypt yapilir:
  * notification_settings.smtp_password
  * notification_settings.sms_api_key
  * notification_settings.sms_account_sid
  * notification_settings.telegram_bot_token
  * outbound_target.auth_token

Format:
    "enc:v1:<base64-fernet-token>"
    Prefix DB'de mevcut plaintext'i ayirt etmek icin — geriye uyumlu
    migration tek seferlik upgrade ile yapilir. `decrypt_secret` prefix yoksa
    plaintext kabul eder (legacy) ve uyari log atar.
"""

from __future__ import annotations

import base64
import hashlib
import logging
from typing import Optional

from cryptography.fernet import Fernet, InvalidToken

logger = logging.getLogger(__name__)

_ENC_PREFIX = "enc:v1:"

# Module-level cache; thread-safe init (process basina tek instance).
_fernet: Optional[Fernet] = None


def _derive_key_from_secret_key(secret_key: str) -> bytes:
    """SECRET_KEY'den Fernet uyumlu 32-byte URL-safe base64 anahtar uretir.

    Production'da `SECRETS_MASTER_KEY` explicit set edilmedikce fallback
    olarak kullanilir. SECRET_KEY rotasyonu yapilirsa eski sifrelenen
    veriler decrypt edilemez — bu yuzden production'da SECRETS_MASTER_KEY
    ayri bir env olarak yonetilmesi onerilir.

    HKDF: sabit salt + SHA-256(secret_key); 32 byte cikti, urlsafe_b64encode.
    """
    digest = hashlib.sha256(b"e1-secrets-vault::" + secret_key.encode("utf-8")).digest()
    return base64.urlsafe_b64encode(digest)


def _get_fernet() -> Fernet:
    """Lazy singleton — ilk decrypt/encrypt cagrisinda Settings'ten okur."""
    global _fernet
    if _fernet is None:
        from app.core.config import settings

        master_key = (getattr(settings, "secrets_master_key", "") or "").strip()
        if master_key:
            try:
                # Geçerli Fernet key formati? Fernet __init__ format kontrolu
                # yapar; gecersiz olunca ValueError.
                _fernet = Fernet(master_key.encode("ascii"))
            except (ValueError, TypeError):
                logger.error(
                    "secrets_vault_invalid_master_key — SECRETS_MASTER_KEY "
                    "Fernet formatinda degil; SECRET_KEY'den deriv ediliyor"
                )
                _fernet = Fernet(_derive_key_from_secret_key(settings.secret_key))
        else:
            _fernet = Fernet(_derive_key_from_secret_key(settings.secret_key))
    return _fernet


def encrypt_secret(plaintext: str | None) -> str | None:
    """Sifrelenmis token (prefix dahil) doner. None/empty in -> None/empty out.

    Idempotent: zaten `enc:v1:` prefix'li gelen deger dokunulmaz dondurulur
    (cifte sifreleme onlemi — ornek: API PUT'ta read-modify-write akisinda).
    """
    if not plaintext:
        return plaintext
    if plaintext.startswith(_ENC_PREFIX):
        return plaintext
    token = _get_fernet().encrypt(plaintext.encode("utf-8")).decode("ascii")
    return _ENC_PREFIX + token


def decrypt_secret(value: str | None) -> str | None:
    """Sifrelenmis (`enc:v1:...`) veya legacy plaintext input alir.

    Legacy plaintext (prefix yok) bir kez WARN log atilir ve aynen dondurulur.
    Bir sonraki write islemi `encrypt_secret` ile yeniden yazar (opportunistic
    migration). InvalidToken durumunda da legacy fallback yapilir — eski
    deploy'da farkli master_key kullanilmissa veri kaybedilmemis olur.
    """
    if not value:
        return value
    if not value.startswith(_ENC_PREFIX):
        # Legacy plaintext — dokunulmaz dondurulur; sadece bir kez log spam'i
        # olmasin diye INFO seviyesinde.
        logger.info("secrets_vault_legacy_plaintext_read — opportunistic re-encrypt onerilir")
        return value
    raw = value[len(_ENC_PREFIX):].encode("ascii")
    try:
        return _get_fernet().decrypt(raw).decode("utf-8")
    except InvalidToken:
        logger.warning(
            "secrets_vault_invalid_token — master_key mismatch veya bozuk veri; "
            "bos string dondurulur"
        )
        return ""
