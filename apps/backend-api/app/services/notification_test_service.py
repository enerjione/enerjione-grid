import base64
import json
import smtplib
import ssl
import urllib.error
import urllib.parse
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
    attachments: list[dict] | None = None,
) -> None:
    """SMTP gonderim. html_body verilirse multipart/alternative gonderilir
    (plain text fallback + HTML), aksi halde sadece plain text.

    attachments: opsiyonel liste; her eleman dict olarak
      {"filename": str, "content": bytes, "mime": "image/png"|...,
       "cid": str | None}.
    cid verilirse inline (HTML icindeki <img src="cid:..."> referans
    edilebilir) olarak eklenir; aksi halde regular ek olarak.
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

    # Ek dosyalar (image/png vb)
    for att in attachments or []:
        try:
            content = att.get("content")
            if not content:
                continue
            mime = att.get("mime", "application/octet-stream")
            maintype, _, subtype = mime.partition("/")
            if not subtype:
                subtype = "octet-stream"
            filename = att.get("filename") or "attachment.bin"
            kwargs: dict = {"maintype": maintype, "subtype": subtype, "filename": filename}
            cid = att.get("cid")
            if cid:
                # Inline (HTML body icindeki cid: referansi icin)
                kwargs["disposition"] = "inline"
                kwargs["cid"] = f"<{cid}>"
            mail.add_attachment(content, **kwargs)
        except Exception:  # noqa: BLE001
            # Tek attachment hatasi tum maili dusurmesin
            continue

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
    """SMS test gonderir. Provider'a gore farkli akis kullanir:

    - 'mock'    : hicbir sey yapma (lokal test akisi).
    - 'twilio'  : Twilio Programmable Messaging REST API. HTTPS form-encoded
                  POST + HTTP Basic Auth (Account SID : Auth Token).
                  URL Twilio API'sinden hesaplanir (sms_api_url kullanilmaz).
    - 'netgsm'/'generic' veya digerleri: eski JSON-POST davranisi —
                  sms_api_url'e JSON body gonder (api_key + to + message).
    """
    provider = (settings_row.sms_provider or "mock").strip().lower()
    if provider == "mock":
        return

    if provider == "twilio":
        _send_sms_via_twilio(settings_row, recipient_phone=recipient_phone, message=message)
        return

    # Generic JSON-POST (netgsm, vb)
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


def _send_sms_via_twilio(
    settings_row: NotificationSettings,
    *,
    recipient_phone: str,
    message: str,
) -> None:
    """Twilio Programmable Messaging REST API uzerinden tek mesaj gonderir.

    Iki mod destekli — settings_row.sms_twilio_use_whatsapp:
      - False (SMS): Body parametresi ile duz SMS. To/From dogrudan E.164.
      - True (WhatsApp): 'whatsapp:' prefixi otomatik eklenir. Eger
        sms_twilio_content_sid dolu ise template mesaj atilir
        (ContentSid + bos ContentVariables); aksi halde Body ile sade
        WhatsApp mesaji (24h pencereyi gerektirir).

    Curl esdegerleri:
      SMS:
        curl 'https://api.twilio.com/2010-04-01/Accounts/<SID>/Messages.json' -X POST \\
          --data-urlencode 'To=<E.164>' \\
          --data-urlencode 'From=<E.164>' \\
          --data-urlencode 'Body=test' \\
          -u <SID>:<AuthToken>
      WhatsApp (template):
        curl 'https://api.twilio.com/2010-04-01/Accounts/<SID>/Messages.json' -X POST \\
          --data-urlencode 'To=whatsapp:<E.164>' \\
          --data-urlencode 'From=whatsapp:<E.164>' \\
          --data-urlencode 'ContentSid=HX...' \\
          --data-urlencode 'ContentVariables={}' \\
          -u <SID>:<AuthToken>

    sms_account_sid          = Account SID (AC...)
    sms_api_key              = Auth Token
    sms_from_number          = Sender (E.164, prefix otomatik eklenir)
    sms_twilio_use_whatsapp  = SMS yerine WhatsApp gonder
    sms_twilio_content_sid   = (opsiyonel) onaylanmis template ID (HX...)
    """
    account_sid = (settings_row.sms_account_sid or "").strip()
    auth_token = (settings_row.sms_api_key or "").strip()
    from_number = (settings_row.sms_from_number or "").strip()
    use_wa = bool(getattr(settings_row, "sms_twilio_use_whatsapp", False))
    content_sid = (getattr(settings_row, "sms_twilio_content_sid", "") or "").strip()

    if not account_sid:
        raise ValueError("Twilio Account SID boş.")
    if not auth_token:
        raise ValueError("Twilio Auth Token boş.")
    if not from_number:
        raise ValueError("Twilio gönderen numarası (From) boş.")
    if not recipient_phone:
        raise ValueError("Alıcı numarası boş.")

    # 'whatsapp:' prefixi: kullanici hem ham E.164 hem 'whatsapp:+...' yazmis
    # olabilir. WhatsApp modunda eksikse ekle, varsa olduğu gibi kullan.
    def _wa(num: str) -> str:
        s = num.strip()
        if not use_wa:
            return s
        return s if s.lower().startswith("whatsapp:") else f"whatsapp:{s}"

    fields: dict[str, str] = {
        "To": _wa(recipient_phone),
        "From": _wa(from_number),
    }
    if use_wa and content_sid:
        # Template (Business-initiated) mesaj: ContentSid + bos
        # ContentVariables. Degisken icermeyen template'ler icin yeterli.
        fields["ContentSid"] = content_sid
        fields["ContentVariables"] = "{}"
    else:
        fields["Body"] = message or ""

    url = f"https://api.twilio.com/2010-04-01/Accounts/{urllib.parse.quote(account_sid, safe='')}/Messages.json"
    body = urllib.parse.urlencode(fields).encode("utf-8")
    basic = base64.b64encode(f"{account_sid}:{auth_token}".encode("utf-8")).decode("ascii")
    req = urllib.request.Request(
        url,
        data=body,
        headers={
            "Content-Type": "application/x-www-form-urlencoded",
            "Authorization": f"Basic {basic}",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            # Twilio basarili gonderimde 201 doner. 200/202 de kabul edilir.
            if resp.status not in (200, 201, 202):
                raise RuntimeError(f"Twilio HTTP {resp.status}")
    except urllib.error.HTTPError as exc:
        # Twilio hata gövdesinde JSON {code, message, more_info} doner —
        # kullaniciya anlamli hata mesaji yansit.
        try:
            err_body = exc.read().decode("utf-8", errors="replace")
            err_json = json.loads(err_body)
            msg = err_json.get("message") or err_body
            code = err_json.get("code")
            raise RuntimeError(f"Twilio API hatası ({code}): {msg}") from exc
        except (ValueError, json.JSONDecodeError):
            raise RuntimeError(f"Twilio HTTP {exc.code}") from exc
