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

    # SMTP password decrypt (DB'de `enc:v1:...` Fernet sifreli).
    from app.services.notification_settings_service import decrypt_notification_credentials

    smtp_password = decrypt_notification_credentials(settings_row).smtp_password or ""

    if settings_row.smtp_port == 465:
        with smtplib.SMTP_SSL(settings_row.smtp_host, settings_row.smtp_port, context=ssl.create_default_context()) as server:
            if settings_row.smtp_username:
                server.login(settings_row.smtp_username, smtp_password)
            server.send_message(mail)
        return

    with smtplib.SMTP(settings_row.smtp_host, settings_row.smtp_port) as server:
        server.ehlo()
        if settings_row.smtp_username:
            server.starttls(context=ssl.create_default_context())
            server.login(settings_row.smtp_username, smtp_password)
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
    # telegram_bot_token DB'de Fernet sifreli; decrypt et.
    from app.services.notification_settings_service import decrypt_notification_credentials

    token = (decrypt_notification_credentials(settings_row).telegram_bot_token or "").strip()
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


def discover_telegram_chats(bot_token: str) -> list[dict]:
    """Telegram Bot API'nin /getUpdates metodu uzerinden bot'a yazilmis
    son mesajlardan benzersiz chat'leri toplar.

    Telegram getUpdates calismak icin sart: bot Polling modunda olmali —
    yani webhook ayarlanmamis olmali. Webhook tanimliysa getUpdates 409
    Conflict doner; bu durumu kullaniciya net bildiririz.

    Donus formati:
      [
        {"id": "<chat_id>", "type": "group|private|channel|supergroup",
         "title": "<grup adi veya kullanici adi>"},
        ...
      ]
    Benzersiz chat_id'ler. En son mesajdan eski uzeri sirali (yeni en
    ustte).
    """
    token = (bot_token or "").strip()
    if not token:
        raise ValueError("Telegram bot token boş.")

    # offset=-100 son 100 update'i alir; limit 100 max. allowed_updates
    # bos -> tum tipleri al.
    url = f"https://api.telegram.org/bot{token}/getUpdates?limit=100&timeout=0"
    try:
        with urllib.request.urlopen(url, timeout=12) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as exc:
        try:
            err_body = exc.read().decode("utf-8", errors="replace")
            err_json = json.loads(err_body)
            desc = err_json.get("description") or err_body
            if exc.code == 409 or "webhook" in desc.lower():
                raise RuntimeError(
                    "Bot webhook moduna ayarlanmış — getUpdates kullanılamaz. "
                    "Önce webhook'u silin (deleteWebhook) veya BotFather'dan "
                    "bot ayarlarını kontrol edin."
                ) from exc
            if exc.code == 401:
                raise RuntimeError("Bot Token geçersiz (Telegram 401).") from exc
            raise RuntimeError(f"Telegram API hatası: {desc}") from exc
        except (ValueError, json.JSONDecodeError):
            raise RuntimeError(f"Telegram HTTP {exc.code}") from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"Telegram'a ulaşılamadı: {exc}") from exc

    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        raise RuntimeError("Telegram cevabı JSON parse edilemedi.")
    if not isinstance(data, dict) or not data.get("ok"):
        desc = (data or {}).get("description", "bilinmeyen hata")
        raise RuntimeError(f"Telegram getUpdates başarısız: {desc}")

    results = data.get("result") or []
    seen: dict[str, dict] = {}
    # En yeni update'ler listenin sonunda; ters sirayla itererek 'son mesaj
    # bilgisi' her chat icin korunur.
    for upd in reversed(results):
        # Birden fazla mesaj turune bak: message, edited_message,
        # channel_post, edited_channel_post, my_chat_member (gruba ekleme).
        msg = (
            upd.get("message")
            or upd.get("edited_message")
            or upd.get("channel_post")
            or upd.get("edited_channel_post")
            or upd.get("my_chat_member")
            or {}
        )
        chat = msg.get("chat") if isinstance(msg, dict) else None
        if not isinstance(chat, dict):
            continue
        chat_id = chat.get("id")
        if chat_id is None:
            continue
        cid = str(chat_id)
        if cid in seen:
            continue
        chat_type = str(chat.get("type") or "private")
        # Baslik tercihi: grup/kanal => 'title'; ozel => username veya
        # 'first_name last_name'.
        title = chat.get("title")
        if not title:
            uname = chat.get("username")
            first = chat.get("first_name") or ""
            last = chat.get("last_name") or ""
            full = (first + " " + last).strip()
            title = (uname and f"@{uname}") or full or cid
        seen[cid] = {"id": cid, "type": chat_type, "title": title}
    # En yeni mesajdan eskiye sirali liste (reversed sonrasi insertion
    # sirasi).
    return list(seen.values())


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

    # Generic JSON-POST (netgsm, vb) — sms_api_key DB'de sifreli.
    if not settings_row.sms_api_url:
        raise ValueError("SMS API URL boş.")
    if not settings_row.sms_api_key:
        raise ValueError("SMS API Key boş.")
    from app.services.notification_settings_service import decrypt_notification_credentials

    api_key = decrypt_notification_credentials(settings_row).sms_api_key or ""
    payload = json.dumps(
        {
            "api_key": api_key,
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
    # Twilio account_sid + auth_token (sms_api_key) DB'de Fernet sifreli.
    from app.services.notification_settings_service import decrypt_notification_credentials

    _creds = decrypt_notification_credentials(settings_row)
    account_sid = (_creds.sms_account_sid or "").strip()
    auth_token = (_creds.sms_api_key or "").strip()
    from_number = (settings_row.sms_from_number or "").strip()
    use_wa = bool(getattr(settings_row, "sms_twilio_use_whatsapp", False))
    content_sid = (getattr(settings_row, "sms_twilio_content_sid", "") or "").strip()
    content_vars_raw = (getattr(settings_row, "sms_twilio_content_vars", "") or "").strip()

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
        # Template (Business-initiated) mesaj: ContentSid + ContentVariables.
        # Kullanici JSON yazmissa olduğu gibi geçir; bos veya bozuksa {}
        # gonder (degisken icermeyen template'ler icin yeterli).
        fields["ContentSid"] = content_sid
        cv: str = "{}"
        if content_vars_raw:
            try:
                # Geçerli JSON oldugunu dogrula — gecersiz JSON Twilio
                # tarafindan reddedilir; biz dogrulayip bos olana dusurelim.
                parsed = json.loads(content_vars_raw)
                if isinstance(parsed, dict):
                    cv = json.dumps(parsed, ensure_ascii=False)
            except (ValueError, json.JSONDecodeError):
                # Bozuk JSON — kullaniciya net hata mesaji ver.
                raise ValueError(
                    "Twilio Content Variables alanı geçerli JSON değil. "
                    "Örnek: {\"1\": \"değer\", \"2\": \"değer2\"}"
                )
        fields["ContentVariables"] = cv
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
        # kullaniciya anlamli hata mesaji yansit. WhatsApp'a ozel bilinen
        # hata kodlari icin ek aciklama ekle.
        try:
            err_body = exc.read().decode("utf-8", errors="replace")
            err_json = json.loads(err_body)
            msg = err_json.get("message") or err_body
            code = err_json.get("code")
            extra = ""
            if code == 63016:
                # 24-saat conversation window disinda free-form Body.
                extra = (
                    " — WhatsApp 24 saatlik konuşma penceresi dışında "
                    "Body gönderilemez; onaylanmış bir Content Template "
                    "(HX...) kullanın."
                )
            elif code == 63018:
                extra = " — Twilio hız limiti aşıldı (kısa süre sonra tekrar deneyin)."
            elif code == 63007:
                extra = " — From numarası WhatsApp için kayıtlı/onaylanmış değil."
            raise RuntimeError(f"Twilio API hatası ({code}): {msg}{extra}") from exc
        except (ValueError, json.JSONDecodeError):
            raise RuntimeError(f"Twilio HTTP {exc.code}") from exc
