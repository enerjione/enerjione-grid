"""User davet token uretimi + opsiyonel SMTP mail gonderimi.

Davet akisi:
  1) Admin POST /users/invite -> backend uniq token uretir (32 byte hex)
  2) Token SHA-256 hash'i DB'de saklanir (raw token sadece response/mail'de)
  3) Kullanici setup-password sayfasinda token + yeni sifre POST eder
  4) Backend hash eslestir + sifre set + token sil (tek kullanim)

Token TTL: 7 gun. Operator daveti yeniden gonderirse eski token gecersiz
olur (yeni token uretildiginde overwrite).
"""

from __future__ import annotations

import hashlib
import logging
import secrets
import smtplib
from datetime import datetime, timedelta, timezone
from email.message import EmailMessage

from app.core.config import settings
from app.models.user import User

logger = logging.getLogger(__name__)

# Token gecerlilik suresi — kisa olmayacak ki saha operatorel maili kaybedip
# tekrar isteyebilsin; uzun olmayacak ki ele gecirilirse zarar minimum.
INVITATION_TTL = timedelta(days=7)


def generate_invitation_token(user: User) -> str:
    """Yeni davet token'i uret + SHA-256 hash'ini user satirina yaz.

    Donen raw token sadece bu fonksiyonun caller'inda gorunur — mail'e
    konup linke gomulur, sonra unutulur. DB'de SHA-256 hash kalir.

    Caller'in db.commit() yapmasi gerekir.
    """
    raw = secrets.token_urlsafe(32)  # ~43 char, URL-safe
    user.password_reset_token_hash = hashlib.sha256(raw.encode("utf-8")).hexdigest()
    user.password_reset_token_expires_at = datetime.now(timezone.utc) + INVITATION_TTL
    return raw


def build_setup_url(token: str, *, frontend_base_url: str = "") -> str:
    """Davet linkini olustur. Frontend route: /setup-password?token=...

    `frontend_base_url` operator kurulumunda set edilir (env: FRONTEND_BASE_URL);
    bos ise relative path doner — admin link'i kendi domain'i ile birlestirip
    kullaniciya iletmek zorunda. Best-effort: same-origin Docker kurulumda
    frontend backend'le ayni origin'de oldugu icin relative path is gorur.
    """
    base = (frontend_base_url or "").rstrip("/")
    if base:
        return f"{base}/setup-password?token={token}"
    # Frontend base bilinmiyor — relative path; admin elle host ekler.
    return f"/setup-password?token={token}"


def send_invitation_email(user: User, setup_url: str) -> bool:
    """SMTP yapilandirilmissa davet mail'i gonder. Return: True basariliysa.

    SMTP_ENABLED=false ise sessizce False doner — caller link'i baska yolla
    (panodan kopyala vb.) iletecek.

    Bu fonksiyon best-effort: SMTP hatasi caller'a propagate edilmez,
    sadece log'a yazilir. User yaratma akisi mail gonderilmese de basarili
    sayilir (admin link'i panodan kopyalar).
    """
    if not settings.smtp_enabled:
        logger.info("invitation_email_skipped reason=smtp_disabled user=%s", user.username)
        return False
    if not user.email or "@" not in user.email:
        logger.warning("invitation_email_skipped reason=invalid_email user=%s", user.username)
        return False

    try:
        msg = EmailMessage()
        msg["Subject"] = "EnerjiOne Grid - Hesap aktivasyonu"
        msg["From"] = settings.smtp_from_email
        msg["To"] = user.email
        msg.set_content(
            f"Merhaba {user.full_name},\n\n"
            f"EnerjiOne Grid sistemine davet edildiniz. Hesabinizi aktive "
            f"etmek ve sifrenizi belirlemek icin asagidaki bagi tiklayin:\n\n"
            f"{setup_url}\n\n"
            f"Bu bag 7 gun sonra gecersiz olacaktir.\n\n"
            f"Bu maili siz beklemediginizse goz ardi edebilirsiniz.\n\n"
            f"Iyi calismalar,\n"
            f"EnerjiOne Grid"
        )

        host = settings.smtp_host or "localhost"
        port = int(settings.smtp_port or 25)
        with smtplib.SMTP(host, port, timeout=10) as smtp:
            if settings.smtp_username:
                smtp.starttls()
                smtp.login(settings.smtp_username, settings.smtp_password)
            smtp.send_message(msg)
        logger.info("invitation_email_sent user=%s to=%s", user.username, user.email)
        return True
    except Exception:  # noqa: BLE001
        logger.exception("invitation_email_failed user=%s", user.username)
        return False
