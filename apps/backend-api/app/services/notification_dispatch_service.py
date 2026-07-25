"""Alarm bildirimi fan-out servisi.

Yeni bir AlarmEvent oluştuğunda (POST /internal/alarms) cagrilir.

Akis:
  1) Alarmin device_id'sinden, ilgili kullanicilari bul
     (scope_service.get_users_in_scope_for_device).
     Engineer/Installer her zaman dahildir; Operator yalnizca cihaza
     dogrudan/dolayli (line/region uzerinden) sorumlu olduğu ekipteyse.
  2) Her kullanici icin tercihlerini oku (user_notification_preferences).
  3) Sistem cap'inda etkin olan kanallar (NotificationSettings) AND
     kullanicinin kendi tercihi acik olan kanallardan gönder:
       - Web: Notification kaydi olustur (zaten broadcast var; burada
         hedef kullanici icin kisisel olarak da ekliyoruz; bu UI'da
         "bildirimlerim" listesinde gorunmesini saglar).
       - Email: SMTP gateway uzerinden e-mail.
       - SMS: SMS gateway uzerinden cep.
  4) Hatalar yutulur (loglanır) — alarm yaratimini engellemez.
"""

from __future__ import annotations

import json
import logging
from typing import Iterable

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.alarm import AlarmEvent
from app.models.alarm_rule import AlarmRule
from app.models.device import Device
from app.models.notification_settings import NotificationSettings
from app.models.project_settings import ProjectSettings
from app.models.user import User
from app.models.user_notification_preference import UserNotificationPreference
from app.services.email_templates import render_alarm_email
from app.services.notification_service import create_notification
from app.services.notification_test_service import (
    send_smtp_test,
    send_sms_test,
    send_telegram_test,
)
from app.services import whatsapp_web_client_service
from app.services.scope_service import get_users_in_scope_for_device

logger = logging.getLogger(__name__)


_LEVEL_RANK = {"info": 0, "warning": 1, "error": 2, "critical": 3}


def _level_rank(level: str | None) -> int:
    if not level:
        return 0
    return _LEVEL_RANK.get(level.lower(), 0)


def _resolve_active_rule(db: Session, alarm: AlarmEvent) -> AlarmRule | None:
    """Alarm'i tetikleyen aktif kurali bul (signal_key + name esit).

    Backend ingest_alarm tarafinda dedup ayni name + level + signal_key ile
    yapildigi icin cogu durumda tek aday vardir. Bulamazsa None doner ve
    dispatcher fallback'e (kural-bazli secimi gozardi) duser.
    """
    if not alarm.title:
        return None
    stmt = (
        select(AlarmRule)
        .where(AlarmRule.name == alarm.title)
        .where(AlarmRule.is_active.is_(True))
    )
    if alarm.signal_key:
        stmt = stmt.where(AlarmRule.signal_key == alarm.signal_key)
    return db.scalar(stmt.limit(1))


def _project_title(db: Session) -> str | None:
    row = db.get(ProjectSettings, 1)
    if row is None:
        return None
    return (row.site_title or row.project_name or row.customer_name) or None


def _alarm_metadata(alarm: AlarmEvent) -> dict:
    """Notification metadata'sini AlarmEvent'in (varsa baglantili)
    Notification kaydindan turetir. Bulamazsa bos dict."""
    # Notification metadata icindeki device_name/line/region/value bilgileri
    # ingest_alarm'da zaten cikariliyor; ama burada AlarmEvent'tan dogrudan
    # erisim yok. Kolaylik icin Notification kaydi okumadan, alarm.signal_key
    # uzerinden temel alanlari turetiyoruz.
    return {}


def _get_pref(db: Session, user_id: int) -> UserNotificationPreference:
    pref = db.get(UserNotificationPreference, user_id)
    if pref is None:
        # Default: web ve email acik, sms kapali. Tabloya KAYDETMEYIZ —
        # dispatcher cagrisi sadece okumadir; kullanicinin tercih
        # gostermesi gerekene kadar default'larla calisir.
        pref = UserNotificationPreference(
            user_id=user_id,
            web_enabled=True,
            email_enabled=True,
            sms_enabled=False,
            whatsapp_web_enabled=False,
            min_level_rank=0,
        )
    return pref


def _system_settings(db: Session) -> NotificationSettings | None:
    return db.scalar(select(NotificationSettings).limit(1))


def _build_alarm_email(
    db: Session, alarm: AlarmEvent, project_title: str | None
) -> tuple[str, str, str]:
    """Alarm icin (subject, plain_text, html_body) uretir."""
    # Bagli notification kaydindan zenginlestirilmis metadata'yi cek
    # (device_name, signal_source, line_name, region_name, value, threshold)
    from app.models.notification import Notification
    meta: dict = {}
    notif = db.scalar(
        select(Notification)
        .where(Notification.category == "alarm")
        .where(Notification.recipient_username.is_(None))
        .order_by(Notification.created_at.desc())
        .limit(1)
    )
    # Daha guvenli yol: alarm.id metadata icinde aramak
    if notif is not None and notif.metadata_json:
        try:
            parsed = json.loads(notif.metadata_json)
            if isinstance(parsed, dict) and parsed.get("alarm_id") == alarm.id:
                meta = parsed
        except Exception:  # noqa: BLE001
            pass

    device = db.get(Device, alarm.device_id) if alarm.device_id else None
    subject, html_body = render_alarm_email(
        project_title=project_title,
        rule_name=alarm.title,
        description=alarm.description,
        level=alarm.level,
        device_name=meta.get("device_name") or (device.name if device else None),
        device_code=meta.get("device_code") or (device.code if device else None),
        signal_source=meta.get("signal_source"),
        line_name=meta.get("line_name"),
        region_name=meta.get("region_name"),
        value=meta.get("value"),
        value_string=meta.get("value_string"),
        threshold=meta.get("threshold"),
        operator=meta.get("operator"),
        occurred_at=alarm.created_at,
    )
    plain_text = (
        f"Alarm: {alarm.title}\n"
        f"Seviye: {alarm.level}\n"
        f"Cihaz: {meta.get('device_name') or meta.get('device_code') or '-'}\n"
        f"Aciklama: {alarm.description or '-'}\n"
        f"Tarih: {alarm.created_at}\n"
    )
    return subject, plain_text, html_body


def _resolve_active_fault(db: Session, alarm: AlarmEvent) -> "FaultEvent | None":
    """Bu alarm'in tetikledigi (cihazin oturdugu) hatdaki AKTIF fault'u bul.

    Fault recompute servisi alarm ingest'te zaten cagrildigi icin alarmin
    cihazi bagli oldugu hat'da bir aktif FaultEvent satiri olmasi
    beklenir.
    """
    from app.models.fault import FaultEvent
    from app.models.grid_topology import LineSegment
    if alarm.device_id is None:
        return None
    seg = db.scalar(select(LineSegment).where(LineSegment.device_id == alarm.device_id))
    if seg is None:
        return None
    return db.scalar(
        select(FaultEvent)
        .where(FaultEvent.line_id == seg.line_id)
        .where(FaultEvent.status != "closed")
        .order_by(FaultEvent.opened_at.desc())
        .limit(1)
    )


def _send_email_for_user(
    db: Session,
    settings: NotificationSettings | None,
    user: User,
    alarm: AlarmEvent,
    project_title: str | None,
) -> None:
    if settings is None or not settings.smtp_enabled:
        return
    if not user.email:
        return
    subject, plain_text, html_body = _build_alarm_email(db, alarm, project_title)

    # Mail govdesine harita gorseli ekle. Fault aktifse PNG render edip
    # inline (cid) ek olarak ekleriz; HTML body de <img src="cid:fault-map">
    # referansiyla gosterir. Render hatasi mail'i bozmasin — ek atla.
    attachments: list[dict] = []
    try:
        fault = _resolve_active_fault(db, alarm)
        if fault is not None:
            from app.services.fault_map_render import render_fault_map_png
            png = render_fault_map_png(db, fault)
            if png:
                cid = f"fault-map-{fault.id}"
                attachments.append(
                    {
                        "filename": f"ariza-haritasi-{fault.id}.png",
                        "content": png,
                        "mime": "image/png",
                        "cid": cid,
                    }
                )
                # HTML body'ye haritayi ekle (inline goruntu)
                # Sadece <body> sonuna append edelim ki mevcut sablon
                # bozulmasin.
                if html_body and "</body>" in html_body:
                    inline_block = (
                        f'<div style="margin:18px 0;text-align:center;">'
                        f'<img src="cid:{cid}" alt="Arıza haritası" '
                        f'style="max-width:100%;border-radius:10px;'
                        f'box-shadow:0 2px 8px rgba(0,0,0,.1);" /></div>'
                    )
                    html_body = html_body.replace(
                        "</body>", inline_block + "</body>"
                    )
                elif html_body:
                    html_body = (
                        html_body
                        + f'<div style="margin:18px 0;text-align:center;">'
                          f'<img src="cid:{cid}" alt="Arıza haritası" '
                          f'style="max-width:100%;border-radius:10px;" /></div>'
                    )
    except Exception:  # noqa: BLE001
        logger.exception("fault_map_render_outer_failed")

    try:
        send_smtp_test(
            settings,
            recipient_email=user.email,
            subject=subject,
            message=plain_text,
            html_body=html_body,
            attachments=attachments or None,
        )
        logger.info(
            "alarm_email_sent user=%s alarm_id=%d map_attached=%s",
            user.username, alarm.id, bool(attachments),
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "alarm_email_failed user=%s alarm_id=%d error=%s",
            user.username, alarm.id, exc,
        )


def _send_sms_for_user(
    settings: NotificationSettings | None, user: User, alarm: AlarmEvent
) -> None:
    if settings is None or not settings.sms_enabled:
        return
    if not user.phone_number:
        return
    msg = f"Alarm: {alarm.title} - {alarm.description}"[:300]
    try:
        send_sms_test(settings, recipient_phone=user.phone_number, message=msg)
        logger.info("alarm_sms_sent user=%s alarm_id=%d", user.username, alarm.id)
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "alarm_sms_failed user=%s alarm_id=%d error=%s",
            user.username, alarm.id, exc,
        )


def _send_telegram_broadcast(
    settings: NotificationSettings | None, alarm: AlarmEvent
) -> None:
    """Telegram'a global broadcast — her bir chat_id'ye ayni mesaj.
    Kullanici-bazli tercihe bagli degil; settings.telegram_enabled + chat_ids
    listesi yeterli."""
    if settings is None or not getattr(settings, "telegram_enabled", False):
        return
    chat_ids_raw = getattr(settings, "telegram_chat_ids", "") or ""
    chat_ids = [c.strip() for c in chat_ids_raw.split(",") if c.strip()]
    if not chat_ids:
        return
    # Telegram HTML parse_mode: <b>, <i>, <a> destekler. Sade ozet.
    title = (alarm.title or "Alarm").replace("<", "&lt;").replace(">", "&gt;")
    desc = (alarm.description or "").replace("<", "&lt;").replace(">", "&gt;")
    level = (alarm.level or "warning").upper()
    text = (
        f"⚠ <b>{title}</b>\n"
        f"Seviye: <b>{level}</b>\n"
        f"{desc}"
    )
    for chat_id in chat_ids:
        try:
            send_telegram_test(settings, chat_id=chat_id, message=text)
            logger.info("alarm_telegram_sent chat=%s alarm_id=%d", chat_id, alarm.id)
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "alarm_telegram_failed chat=%s alarm_id=%d error=%s",
                chat_id, alarm.id, exc,
            )


def _send_whatsapp_for_user(
    settings: NotificationSettings | None, user: User, alarm: AlarmEvent
) -> None:
    if settings is None or not settings.whatsapp_web_enabled:
        return
    if not user.phone_number:
        return
    msg = f"Alarm: {alarm.title} - {alarm.description}"[:1000]
    try:
        whatsapp_web_client_service.send_test_message(user.phone_number, msg)
        logger.info("alarm_whatsapp_sent user=%s alarm_id=%d", user.username, alarm.id)
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "alarm_whatsapp_failed user=%s alarm_id=%d error=%s",
            user.username, alarm.id, exc,
        )


def _send_whatsapp_group_broadcast(
    settings: NotificationSettings | None, alarm: AlarmEvent
) -> None:
    """WhatsApp grup/kisi JID listesine global broadcast (Telegram deseni)."""
    if settings is None or not settings.whatsapp_web_enabled:
        return
    jids_raw = getattr(settings, "whatsapp_web_group_jids", "") or ""
    jids = [j.strip() for j in jids_raw.split(",") if j.strip()]
    if not jids:
        return
    title = alarm.title or "Alarm"
    level = (alarm.level or "warning").upper()
    text = f"Alarm: {title}\nSeviye: {level}\n{alarm.description or ''}"
    for jid in jids:
        try:
            whatsapp_web_client_service.send_test_message(jid, text)
            logger.info("alarm_whatsapp_group_sent jid=%s alarm_id=%d", jid, alarm.id)
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "alarm_whatsapp_group_failed jid=%s alarm_id=%d error=%s",
                jid, alarm.id, exc,
            )


def _create_personal_web_notification(
    db: Session, user: User, alarm: AlarmEvent
) -> None:
    # Ek olarak kullaniciya OZEL bir bildirim kaydı acariz; broadcast
    # zaten internal endpoint'te yaratiliyor ama kisiye ozel kayit
    # "bildirimlerim" listesinde isaretsiz gozukmesi icin gerekli.
    severity = (
        "critical" if (alarm.level or "").lower() == "critical"
        else "error" if (alarm.level or "").lower() in ("error", "high")
        else "warning"
    )
    try:
        create_notification(
            db,
            recipient_username=user.username,
            category="alarm",
            severity=severity,
            title=f"Alarm: {alarm.title}",
            body=alarm.description,
            link=f"/alarms#alarm-{alarm.id}",
            metadata={
                "alarm_id": alarm.id,
                "device_id": alarm.device_id,
                "_title_i18n": {"key": "alarm_personal", "params": {"title": alarm.title or ""}},
            },
        )
    except Exception:  # noqa: BLE001
        logger.exception("alarm_personal_web_notif_failed user=%s", user.username)


def dispatch_fault_notifications(
    db: Session,
    *,
    fault_id: int,
    line_id: int,
    region_id: int,
    last_red_device_id: int | None,
    first_green_device_id: int | None,
    from_pole_seq: int | None,
    to_pole_seq: int | None,
    latitude: float | None,
    longitude: float | None,
    opened_at,
    assigned_to_username: str | None = None,
) -> None:
    """Hat arizasi acildiginda mail gonder. Konum varsa harita gorseli + yol
    tarifi linki dahil edilir.

    KAPSAM: Hatta sorumlu olan tum kullanicilara mail. Kural gerek yok —
    fault otomatik bildirim olarak her zaman atilir (kullanici talebi).
    SMTP enabled olmasi yeterli.
    """
    settings = _system_settings(db)
    if settings is None or not settings.smtp_enabled:
        return
    project_title = _project_title(db)

    # Hat + bolge + cihaz adlari
    from app.models.grid_topology import Line, Region
    line = db.get(Line, line_id)
    region = db.get(Region, region_id) if region_id else None
    last_red_dev = db.get(Device, last_red_device_id) if last_red_device_id else None
    first_green_dev = db.get(Device, first_green_device_id) if first_green_device_id else None

    if line is None:
        return

    # Hatta sorumlu tum kullanicilari topla (last_red device uzerinden scope).
    recipients: list[User] = []
    if last_red_device_id is not None:
        recipients = list(get_users_in_scope_for_device(db, last_red_device_id))
    if not recipients:
        logger.info("fault_dispatch_no_recipients fault_id=%d", fault_id)
        return

    from app.services.email_templates import render_fault_email
    subject, html_body = render_fault_email(
        project_title=project_title,
        line_name=line.name,
        line_code=line.code,
        region_name=region.name if region else None,
        last_red_device_name=last_red_dev.name if last_red_dev else None,
        last_red_device_code=last_red_dev.code if last_red_dev else None,
        first_green_device_name=first_green_dev.name if first_green_dev else None,
        first_green_device_code=first_green_dev.code if first_green_dev else None,
        from_pole_seq=from_pole_seq,
        to_pole_seq=to_pole_seq,
        latitude=latitude,
        longitude=longitude,
        opened_at=opened_at,
        fault_link=None,
        assigned_to=assigned_to_username,
    )
    plain_text = (
        f"Hat Arizasi: {line.name}\n"
        f"Bolge: {region.name if region else '-'}\n"
        f"Direk Araligi: #{from_pole_seq} - #{to_pole_seq}\n"
        f"Konum: {latitude}, {longitude}\n"
    )

    for user in recipients:
        if not user.email:
            continue
        try:
            send_smtp_test(
                settings,
                recipient_email=user.email,
                subject=subject,
                message=plain_text,
                html_body=html_body,
            )
            logger.info(
                "fault_email_sent user=%s fault_id=%d", user.username, fault_id
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "fault_email_failed user=%s fault_id=%d error=%s",
                user.username, fault_id, exc,
            )


def dispatch_alarm_notifications(db: Session, alarm: AlarmEvent) -> None:
    """Verilen alarm icin ilgili kullanicilara web/email/sms/telegram gonder.

    KURAL-BAZLI KANAL SECIMI:
      - Web bildirimi: kuraldan bagimsiz; kullanici tercihi (web_enabled)
        acik ise olusturulur.
      - Email/SMS/Telegram: hem kuralda ilgili notify_* AKTIF olmali HEM
        kullanicinin kendi tercihi (email_enabled / sms_enabled /
        telegram_enabled) acik olmali.
      - Telegram: bot tek bir kanaldan broadcast eder ama scope'taki
        kullanicilardan EN AZ BIRI telegram_enabled=True olmali. Hicbiri
        acik degilse Telegram atilmaz (gereksiz bildirim engellenir).
      - Kural bulunamadiysa (eski alarm, manuel test) eski davranis:
        sadece web bildirimi.
    """
    if alarm.device_id is None:
        return
    recipients = get_users_in_scope_for_device(db, alarm.device_id)
    if not recipients:
        logger.info("alarm_dispatch_no_recipients alarm_id=%d", alarm.id)
        return
    settings = _system_settings(db)
    project_title = _project_title(db)
    rule = _resolve_active_rule(db, alarm)
    rule_email = bool(rule and rule.notify_email)
    rule_sms = bool(rule and rule.notify_sms)
    rule_telegram = bool(rule and rule.notify_telegram)
    rule_whatsapp = bool(rule and rule.notify_whatsapp_web)
    alarm_rank = _level_rank(alarm.level)
    any_telegram_optin = False
    any_whatsapp_optin = False
    for user in recipients:
        pref = _get_pref(db, user.id)
        # Min seviye filtresi
        if alarm_rank < (pref.min_level_rank or 0):
            continue
        if pref.web_enabled:
            _create_personal_web_notification(db, user, alarm)
        # Email: kuralda notify_email AND kullanici tercihi acik
        if rule_email and pref.email_enabled:
            _send_email_for_user(db, settings, user, alarm, project_title)
        # SMS: kuralda notify_sms AND kullanici tercihi acik
        if rule_sms and pref.sms_enabled:
            _send_sms_for_user(settings, user, alarm)
        # WhatsApp kisisel: kuralda notify_whatsapp_web AND kullanici tercihi acik
        if rule_whatsapp and getattr(pref, "whatsapp_web_enabled", False):
            _send_whatsapp_for_user(settings, user, alarm)
        # Telegram: kullanici tercihi opt-in oldu mu?
        if getattr(pref, "telegram_enabled", False):
            any_telegram_optin = True
        if getattr(pref, "whatsapp_web_enabled", False):
            any_whatsapp_optin = True
    # Telegram: kural izin verdi + en az 1 kullanici opt-in oldu ise
    # global broadcast. Bot tek bir kanaldan yayin yapar (ayar listesi
    # icinde tanimli chat_ids), bu yuzden kisi-bazli filtre yapamayiz
    # ama opt-in olan yoksa gondermeyiz.
    if rule_telegram and any_telegram_optin:
        _send_telegram_broadcast(settings, alarm)
    # WhatsApp grup: ayni mantik — kural izin verdi + en az 1 kullanici
    # opt-in oldu ise grup/kisi JID listesine broadcast.
    if rule_whatsapp and any_whatsapp_optin:
        _send_whatsapp_group_broadcast(settings, alarm)


def _unused_for_imports(_d: Device, _i: Iterable):  # pragma: no cover
    return None
