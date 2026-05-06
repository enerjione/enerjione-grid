import json
import smtplib
import ssl
import urllib.request
from email.message import EmailMessage

from app.models.notification_settings import NotificationSettings


def send_smtp_test(
    settings_row: NotificationSettings,
    *,
    recipient_email: str,
    subject: str,
    message: str,
    html_body: str | None = None,
) -> None:
    """SMTP gonderim. html_body verilirse multipart/alternative gonderilir
    (plain text fallback + HTML), aksi halde sadece plain text.
    """
    if not settings_row.smtp_host:
        raise ValueError("SMTP sunucu adresi boş.")
    if settings_row.smtp_port <= 0:
        raise ValueError("SMTP port geçersiz.")

    sender = settings_row.smtp_from_email or settings_row.smtp_username or "noreply@horstman.local"
    mail = EmailMessage()
    mail["From"] = sender
    mail["To"] = recipient_email
    mail["Subject"] = subject
    # Plain text body default — eski clientlar veya HTML render edemeyenler icin.
    mail.set_content(message or "")
    if html_body:
        # multipart/alternative: HTML versiyonu ekle. Modern clientlar HTML'i
        # tercih eder; eski clientlar plain text'e duser.
        mail.add_alternative(html_body, subtype="html")

    if settings_row.smtp_port == 465:
        with smtplib.SMTP_SSL(settings_row.smtp_host, settings_row.smtp_port, context=ssl.create_default_context()) as server:
            if settings_row.smtp_username:
                server.login(settings_row.smtp_username, settings_row.smtp_password)
            server.send_message(mail)
        return

    with smtplib.SMTP(settings_row.smtp_host, settings_row.smtp_port) as server:
        server.ehlo()
        if settings_row.smtp_username:
            server.starttls(context=ssl.create_default_context())
            server.login(settings_row.smtp_username, settings_row.smtp_password)
        server.send_message(mail)


def send_telegram_test(
    settings_row: NotificationSettings,
    *,
    chat_id: str,
    message: str,
    parse_mode: str = "HTML",
) -> None:
    """Telegram Bot API uzerinden tek chat'e mesaj gonderir.

    settings_row.telegram_bot_token zorunlu. parse_mode "HTML" veya
    "MarkdownV2" olabilir; default "HTML" cunku alarm sablonlari HTML uretir.
    """
    token = (settings_row.telegram_bot_token or "").strip()
    if not token:
        raise ValueError("Telegram bot token boş.")
    if not chat_id:
        raise ValueError("Telegram chat ID boş.")
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    payload = json.dumps(
        {
            "chat_id": chat_id,
            "text": message,
            "parse_mode": parse_mode,
            "disable_web_page_preview": False,
        }
    ).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=12) as resp:
        if resp.status != 200:
            raise RuntimeError(f"Telegram API HTTP {resp.status}")


def send_sms_test(
    settings_row: NotificationSettings,
    *,
    recipient_phone: str,
    message: str,
) -> None:
    if settings_row.sms_provider == "mock":
        return
    if not settings_row.sms_api_url:
        raise ValueError("SMS API URL boş.")
    if not settings_row.sms_api_key:
        raise ValueError("SMS API Key boş.")

    payload = json.dumps(
        {
            "api_key": settings_row.sms_api_key,
            "to": [recipient_phone],
            "message": message,
        }
    ).encode("utf-8")
    req = urllib.request.Request(
        settings_row.sms_api_url,
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=12):
        pass
