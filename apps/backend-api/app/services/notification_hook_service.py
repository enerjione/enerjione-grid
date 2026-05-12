import json
import smtplib
import ssl
import urllib.request
from email.message import EmailMessage
from typing import Any

from sqlalchemy import select

from app.db.session import SessionLocal
from app.models.notification_settings import NotificationSettings
from app.models.user import User
from app.services.event_service import record_event


def handle_alarm_created(payload: dict[str, Any]) -> None:
    db = SessionLocal()
    try:
        settings_row = db.get(NotificationSettings, 1)
        if settings_row is None:
            return
        users = list(db.scalars(select(User)).all())
        message = (
            f"Alarm: {payload.get('device_name', 'Bilinmeyen cihaz')} - "
            f"{payload.get('signal_key', 'sinyal')} ({payload.get('quality', 'unknown')})"
        )
        if settings_row.smtp_enabled:
            _send_email_notifications(settings_row, users, message)
        if settings_row.sms_enabled:
            _send_sms_notifications(settings_row, users, message)
        record_event(
            db,
            category="notification",
            event_type="alarm_notification_dispatched",
            severity="info",
            message="Alarm bildirimi dağıtımı tamamlandı",
            metadata={"device_code": payload.get("device_code")},
        )
        db.commit()
    except Exception as ex:
        record_event(
            db,
            category="notification",
            event_type="alarm_notification_failed",
            severity="error",
            message=f"Alarm notification delivery failed: {ex}",
        )
        db.commit()
    finally:
        db.close()


def _send_email_notifications(settings_row: NotificationSettings, users: list[User], body: str) -> None:
    recipients = [user.email for user in users if user.email]
    if not recipients or not settings_row.smtp_host:
        return

    # SMTP password DB'de Fernet sifreli (`enc:v1:...`) saklanir; dispatch
    # oncesi plaintext'e decrypt ediyoruz. Row mutate edilmez.
    from app.services.notification_settings_service import decrypt_notification_credentials

    creds = decrypt_notification_credentials(settings_row)
    smtp_password = creds.smtp_password or ""

    mail = EmailMessage()
    mail["From"] = settings_row.smtp_from_email
    mail["To"] = ", ".join(recipients)
    mail["Subject"] = "Horstman Alarm Bildirimi"
    mail.set_content(body)

    if settings_row.smtp_port == 465:
        with smtplib.SMTP_SSL(settings_row.smtp_host, settings_row.smtp_port, context=ssl.create_default_context()) as server:
            if settings_row.smtp_username:
                server.login(settings_row.smtp_username, smtp_password)
            server.send_message(mail)
    else:
        with smtplib.SMTP(settings_row.smtp_host, settings_row.smtp_port) as server:
            server.ehlo()
            if settings_row.smtp_username:
                server.starttls(context=ssl.create_default_context())
                server.login(settings_row.smtp_username, smtp_password)
            server.send_message(mail)


def _send_sms_notifications(settings_row: NotificationSettings, users: list[User], body: str) -> None:
    provider = (settings_row.sms_provider or "mock").strip().lower()
    if provider == "mock":
        return
    recipients = [user.phone_number for user in users if user.phone_number]
    if not recipients:
        return

    if provider == "twilio":
        # Twilio API tek alici kabul eder — her recipient icin ayri POST.
        # send_sms_test fonksiyonu tek mesaj icin tum logigi tasidigi icin
        # onu yeniden cagirip recipient basina dongu yapariz; hata tek
        # numarayi kessin, digerleri devam etsin (best effort).
        from app.services.notification_test_service import _send_sms_via_twilio
        for phone in recipients:
            try:
                _send_sms_via_twilio(settings_row, recipient_phone=phone, message=body)
            except Exception:  # noqa: BLE001
                import logging as _logging
                _logging.getLogger(__name__).exception(
                    "twilio_sms_failed phone=%s", phone
                )
        return

    # Generic JSON-POST (netgsm vb)
    if not settings_row.sms_api_url or not settings_row.sms_api_key:
        return
    # api_key DB'de sifreli; plaintext'e decrypt et.
    from app.services.notification_settings_service import decrypt_notification_credentials

    creds = decrypt_notification_credentials(settings_row)
    payload = json.dumps(
        {
            "api_key": creds.sms_api_key or "",
            "to": recipients,
            "message": body,
        }
    ).encode("utf-8")
    req = urllib.request.Request(
        settings_row.sms_api_url,
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=10):
        pass
