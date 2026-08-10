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
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Iterable

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.alarm import AlarmEvent

# FaultEvent yalnizca TIP olarak lazim. Calisma zamaninda modul icinde
# fonksiyon govdesinden import ediliyor (dairesel import kacinmasi); burada
# TYPE_CHECKING altinda tanitmak anotasyonu tip denetleyicileri icin
# cozulebilir yapar, calisma zamanina hicbir maliyet getirmez.
if TYPE_CHECKING:
    from app.models.fault import FaultEvent
from app.models.alarm_rule import AlarmRule
from app.models.device import Device
from app.models.notification_settings import NotificationSettings
from app.models.project_settings import ProjectSettings
from app.models.user import User
from app.models.user_notification_preference import UserNotificationPreference
from app.services import chat_templates
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
    """Kullanicinin kanal tercihleri. Satir yoksa HEPSI ACIK kabul edilir.

    KURAL OTORITEDIR. Eskiden burada sms/telegram/whatsapp default'u False'ti
    ve dispatcher "kuralda secili VE kullanici tercihinde acik" seklinde
    AND'liyordu. Sonuc: kurulumcu alarm kuralinda "SMS + WhatsApp" isaretliyor,
    alarm gercek olusuyor, hicbir sey gitmiyordu — ve bunun hicbir izi yoktu.
    Kimin haberdar olacagi zaten iki yerde sinirli:
      * Alici kumesi `get_users_in_scope_for_device` ile (ekip disina cikmaz)
      * Kanal secimi alarm kuralindaki `notify_*` bayraklariyla
    Bu yuzden kullanici tercihi artik yalnizca BILINCLI OPT-OUT'tur: sadece
    kullanici (veya yonetici) o kanali elle kapattiysa gonderim durur.
    Tabloya KAYDETMEYIZ — dispatcher cagrisi sadece okumadir.
    """
    pref = db.get(UserNotificationPreference, user_id)
    if pref is None:
        pref = UserNotificationPreference(
            user_id=user_id,
            web_enabled=True,
            email_enabled=True,
            sms_enabled=True,
            telegram_enabled=True,
            whatsapp_web_enabled=True,
            min_level_rank=0,
        )
    return pref


def _system_settings(db: Session) -> NotificationSettings | None:
    return db.scalar(select(NotificationSettings).limit(1))


def _build_alarm_email(
    db: Session,
    alarm: AlarmEvent,
    project_title: str | None,
    map_cid: str | None = None,
) -> tuple[str, str, str]:
    """Alarm icin (subject, plain_text, html_body) uretir.

    `map_cid` verilirse harita gorseli sablonun KENDI konum blogunda
    render edilir (bkz. `render_alarm_email`); eskiden govde `</body>`
    etiketi string ile degistirilerek eklendigi icin harita kartin ve
    altbilginin altinda, cerceve disinda kaliyordu."""
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
        map_cid=map_cid,
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
    # Harita ekini SABLONDAN ONCE hazirla: `cid` sablona parametre olarak
    # gidiyor, boylece gorsel kartin "Konum" blogunda cerceve icinde
    # render edilir. Render hatasi mail'i bozmasin — ek atlanir, mail
    # haritasiz gider.
    attachments: list[dict] = []
    map_cid: str | None = None
    try:
        fault = _resolve_active_fault(db, alarm)
        if fault is not None:
            from app.services.fault_map_render import render_fault_map_png
            png = render_fault_map_png(db, fault)
            if png:
                map_cid = f"fault-map-{fault.id}"
                attachments.append(
                    {
                        "filename": f"ariza-haritasi-{fault.id}.png",
                        "content": png,
                        "mime": "image/png",
                        "cid": map_cid,
                    }
                )
    except Exception:  # noqa: BLE001
        logger.exception("fault_map_render_outer_failed")

    subject, plain_text, html_body = _build_alarm_email(
        db, alarm, project_title, map_cid=map_cid
    )

    try:
        send_smtp_test(
            settings,
            recipient_email=user.email,
            subject=subject,
            message=plain_text,
            html_body=html_body,
            attachments=attachments or None,
            from_name=project_title,
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


def _alarm_context(db: Session, alarm: AlarmEvent) -> dict:
    """Alarm mesajlarinin ortak govdesi — cihaz, faz, hat ve bolge.

    Mesajlar eskiden yalnizca baslik + seviye tasiyordu; sahadaki ekip
    "hangi cihaz, hangi faz, hangi hat" bilgisini mesajdan alamiyor,
    arayuze bakmak zorunda kaliyordu. Bunlar tek sorguda cikarilabiliyor.
    """
    device = db.get(Device, alarm.device_id) if alarm.device_id else None
    line_name: str | None = None
    region_name: str | None = None
    if alarm.device_id is not None:
        try:
            from app.models.grid_topology import Line, LineSegment, Region

            seg = db.scalar(
                select(LineSegment).where(LineSegment.device_id == alarm.device_id).limit(1)
            )
            if seg is not None:
                line = db.get(Line, seg.line_id)
                if line is not None:
                    line_name = line.name
                    region = db.get(Region, line.region_id)
                    region_name = region.name if region else None
        except Exception:  # noqa: BLE001
            # Topoloji hatasi bildirimi engellemesin; alan bos kalir.
            logger.debug("alarm_context_topology_failed alarm_id=%s", alarm.id)
    return {
        "title": alarm.title or "Alarm",
        "level": alarm.level,
        "description": alarm.description or None,
        "device_name": device.name if device else None,
        "device_code": device.code if device else None,
        "signal_key": alarm.signal_key,
        "line_name": line_name,
        "region_name": region_name,
        "occurred_at": alarm.created_at,
    }


def _send_sms_for_user(
    settings: NotificationSettings | None, user: User, alarm: AlarmEvent, ctx: dict
) -> None:
    if settings is None or not settings.sms_enabled:
        return
    if not user.phone_number:
        return
    try:
        send_sms_test(
            settings,
            recipient_phone=user.phone_number,
            message=chat_templates.alarm_sms(**ctx),
        )
        logger.info("alarm_sms_sent user=%s alarm_id=%d", user.username, alarm.id)
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "alarm_sms_failed user=%s alarm_id=%d error=%s",
            user.username, alarm.id, exc,
        )


def _send_telegram_broadcast(
    settings: NotificationSettings | None, alarm: AlarmEvent, ctx: dict
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
    # Telegram HTML parse_mode: <b>, <i>, <a> destekler.
    text = chat_templates.alarm_telegram_html(**ctx)
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
    settings: NotificationSettings | None, user: User, alarm: AlarmEvent, ctx: dict
) -> None:
    if settings is None or not settings.whatsapp_web_enabled:
        return
    if not user.phone_number:
        return
    try:
        whatsapp_web_client_service.send_test_message(
            user.phone_number, chat_templates.alarm_whatsapp(**ctx)
        )
        logger.info("alarm_whatsapp_sent user=%s alarm_id=%d", user.username, alarm.id)
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "alarm_whatsapp_failed user=%s alarm_id=%d error=%s",
            user.username, alarm.id, exc,
        )


def _send_whatsapp_group_broadcast(
    settings: NotificationSettings | None, alarm: AlarmEvent, ctx: dict
) -> None:
    """WhatsApp grup/kisi JID listesine global broadcast (Telegram deseni)."""
    if settings is None or not settings.whatsapp_web_enabled:
        return
    jids_raw = getattr(settings, "whatsapp_web_group_jids", "") or ""
    jids = [j.strip() for j in jids_raw.split(",") if j.strip()]
    if not jids:
        return
    text = chat_templates.alarm_whatsapp(**ctx)
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
    """Hat arizasi acildiginda bildirim gonder (e-posta + SMS + WhatsApp grup).
    Konum varsa mailde harita gorseli + yol tarifi linki dahil edilir.

    KAPSAM: `get_users_in_scope_for_device` — engineer/installer/ops_manager
    her arizadan haberdar olur; operator YALNIZCA sorumluluk alanindaki
    (cihaz/hat/bolge) arizalari alir. Alarm kurali GEREKMEZ; ariza otomatik
    bildirim olarak her zaman atilir.

    Kanal kapilari birbirinden BAGIMSIZ: SMTP kapaliyken de WhatsApp grubuna
    ariza duser. (Eskiden fonksiyon `smtp_enabled` degilse en basta donuyordu,
    yani e-posta kapaliysa hicbir kanaldan ariza bildirimi cikmiyordu.)
    """
    settings = _system_settings(db)
    if settings is None:
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
    # Bos olmasi akisi DURDURMAZ: WhatsApp grup yayini alici kumesinden
    # bagimsizdir ve grup seciliyse ariza oraya her kosulda dusmelidir.
    recipients: list[User] = []
    if last_red_device_id is not None:
        recipients = list(get_users_in_scope_for_device(db, last_red_device_id))
    if not recipients:
        logger.info("fault_dispatch_no_recipients fault_id=%d", fault_id)

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

    # Mesaj govdesi — SMS / WhatsApp / Telegram ayni bilgiyi paylasir.
    #
    # Arizayi ACAN alarmlarin basliklari da mesaja girer: "hat arizasi var"
    # demek yetmiyor, ekip "hangi olay" bilgisini de mesajdan gormeli.
    # Ariza kaydi olustugunda bu alarmlar acik (reset=False) durumdadir.
    # Tel mesafeleri kayittan okunur — cagiranlarin imzasini genisletmeye
    # gerek yok, `fault_id` zaten elimizde.
    from app.models.fault import FaultEvent as _FaultEvent

    fault_row = db.get(_FaultEvent, fault_id)
    zone_start_m = fault_row.zone_start_m if fault_row else None
    zone_end_m = fault_row.zone_end_m if fault_row else None
    zone_length_m = fault_row.zone_length_m if fault_row else None

    trigger_titles: list[str] = []
    if last_red_device_id is not None:
        seen: set[str] = set()
        for a in db.scalars(
            select(AlarmEvent)
            .where(AlarmEvent.device_id == last_red_device_id)
            .where(AlarmEvent.reset.is_(False))
            .order_by(AlarmEvent.created_at.desc())
            .limit(6)
        ).all():
            faz = chat_templates.source_label(a.signal_key)
            baslik = f"{a.title} ({faz})" if faz else (a.title or "")
            if baslik and baslik not in seen:
                seen.add(baslik)
                trigger_titles.append(baslik)

    fault_ctx = {
        "line_name": line.name,
        "region_name": region.name if region else None,
        "from_pole_seq": from_pole_seq,
        "to_pole_seq": to_pole_seq,
        "last_red_name": last_red_dev.name if last_red_dev else None,
        "first_green_name": first_green_dev.name if first_green_dev else None,
        "zone_start_m": zone_start_m,
        "zone_end_m": zone_end_m,
        "zone_length_m": zone_length_m,
        "trigger_titles": trigger_titles,
        "latitude": latitude,
        "longitude": longitude,
        "opened_at": opened_at,
    }
    sms_metin = chat_templates.fault_sms(**fault_ctx)
    wa_metin = chat_templates.fault_whatsapp(**fault_ctx)

    # ARIZA HARITA GORSELI — WhatsApp icin.
    # Metin "nerede" sorusunu tam cevaplamiyor: koordinat linkini tiklamak,
    # uygulama degistirmek gerekiyor. Harita gorseli sohbette aninda gorunur.
    # Ayni PNG e-postada da kullaniliyor (fault_map_render); burada bir kez
    # uretilip tum WhatsApp alicilarina yeniden kullanilir.
    # Render hatasi bildirimi DUSURMEMELI — gorsel yoksa metin yine gider.
    harita_png: bytes | None = None
    if settings.whatsapp_web_enabled and fault_row is not None:
        try:
            from app.services.fault_map_render import render_fault_map_png

            harita_png = render_fault_map_png(db, fault_row) or None
        except Exception:  # noqa: BLE001
            logger.exception("fault_map_render_failed fault_id=%s", fault_id)

    def _wa_gonder(hedef: str) -> None:
        """Gorsel varsa gorseli alt yaziyla, yoksa duz metni gonder.

        GORSEL BASARISIZ OLURSA METNE DUSER. Gateway'in medya ucu eski
        surumde YOK (`/send-image` 404 doner); bu durumda gorsel denemesinin
        basarisizligi ariza bildirimini tamamen yutardi. Bildirim kaybi,
        gorselsiz bildirimden cok daha kotu.
        """
        if harita_png:
            try:
                whatsapp_web_client_service.send_image(hedef, harita_png, wa_metin)
                return
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "fault_whatsapp_image_failed hedef=%s fault_id=%d error=%s "
                    "— duz metne dusuluyor",
                    hedef, fault_id, exc,
                )
        whatsapp_web_client_service.send_test_message(hedef, wa_metin)

    for user in recipients:
        pref = _get_pref(db, user.id)
        if settings.smtp_enabled and user.email and pref.email_enabled:
            try:
                send_smtp_test(
                    settings,
                    recipient_email=user.email,
                    subject=subject,
                    message=plain_text,
                    html_body=html_body,
                    from_name=project_title,
                )
                logger.info(
                    "fault_email_sent user=%s fault_id=%d", user.username, fault_id
                )
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "fault_email_failed user=%s fault_id=%d error=%s",
                    user.username, fault_id, exc,
                )
        # SMS: ariza = sahaya cikis demek; e-postayi gormeyen ekip icin
        # tek guvenilir kanal. Kullanici elle kapatmadiysa gider.
        if settings.sms_enabled and user.phone_number and pref.sms_enabled:
            try:
                send_sms_test(
                    settings, recipient_phone=user.phone_number, message=sms_metin
                )
                logger.info("fault_sms_sent user=%s fault_id=%d", user.username, fault_id)
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "fault_sms_failed user=%s fault_id=%d error=%s",
                    user.username, fault_id, exc,
                )
        # WhatsApp kisisel — SADECE grup modu kapaliyken.
        if (
            settings.whatsapp_web_enabled
            and not settings.whatsapp_web_group_mode
            and user.phone_number
            and getattr(pref, "whatsapp_web_enabled", True)
        ):
            try:
                _wa_gonder(user.phone_number)
                logger.info(
                    "fault_whatsapp_sent user=%s fault_id=%d", user.username, fault_id
                )
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "fault_whatsapp_failed user=%s fault_id=%d error=%s",
                    user.username, fault_id, exc,
                )

    # WhatsApp grup: grup modu acikken ariza KOSULSUZ secili gruplara duser
    # (alarm yolundaki davranisla ayni). Alici kumesinden bagimsizdir.
    if settings.whatsapp_web_enabled and settings.whatsapp_web_group_mode:
        jids = [
            j.strip()
            for j in (settings.whatsapp_web_group_jids or "").split(",")
            if j.strip()
        ]
        for jid in jids:
            try:
                _wa_gonder(jid)
                logger.info(
                    "fault_whatsapp_group_sent jid=%s fault_id=%d", jid, fault_id
                )
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "fault_whatsapp_group_failed jid=%s fault_id=%d error=%s",
                    jid, fault_id, exc,
                )


def dispatch_pending_fault_notifications(db: Session, *, limit: int = 20) -> int:
    """Bildirimi bekleyen (notified_at IS NULL) acik arizalari gonderir.

    Ariza motoru (`fault_recompute_service`) kaydi acar ama SMTP/HTTP
    yapmaz — yanit vermeyen bir relay ariza kaydinin commit edilmesini
    engelliyordu. Gonderim buraya, yani notification-worker'in tetikledigi
    HTTP istegi icine alindi.

    `notified_at` damgasi idempotency saglar: worker ayni alarmi retry etse
    veya ust uste birden fazla alarm dusse bile bir ariza icin tek bildirim.
    Donus: gonderilen ariza sayisi. Caller commit eder.
    """
    from app.models.fault import FaultEvent
    from app.models.grid_topology import Pole

    pending = db.scalars(
        select(FaultEvent)
        .where(FaultEvent.notified_at.is_(None))
        .where(FaultEvent.status.in_(("open", "assigned", "in_progress")))
        .order_by(FaultEvent.opened_at.asc())
        .limit(limit)
    ).all()
    if not pending:
        return 0

    sent = 0
    for fault in pending:
        from_pole = db.get(Pole, fault.from_pole_id) if fault.from_pole_id else None
        try:
            dispatch_fault_notifications(
                db,
                fault_id=fault.id,
                line_id=fault.line_id,
                region_id=fault.region_id,
                last_red_device_id=fault.last_red_device_id,
                first_green_device_id=fault.first_green_device_id,
                from_pole_seq=fault.from_pole_seq,
                to_pole_seq=fault.to_pole_seq,
                latitude=from_pole.latitude if from_pole else None,
                longitude=from_pole.longitude if from_pole else None,
                opened_at=fault.opened_at,
                assigned_to_username=fault.assigned_to_username,
            )
            sent += 1
        except Exception:  # noqa: BLE001
            logger.exception("fault_dispatch_failed fault_id=%s", fault.id)
        # Damgayi HER durumda basariz: kalici bir hata (orn. yanlis SMTP
        # sifresi) yuzunden ayni ariza her alarmda yeniden denenip dispatch
        # ucunu yavaslatmasin. Kanal bazli hatalar zaten loglaniyor.
        fault.notified_at = datetime.now(timezone.utc)
    logger.info("fault_pending_dispatch count=%d sent=%d", len(pending), sent)
    return sent


def dispatch_alarm_notifications(db: Session, alarm: AlarmEvent) -> None:
    """Verilen alarm icin ilgili kullanicilara web/email/sms/telegram gonder.

    KURAL-BAZLI KANAL SECIMI:
      - Web bildirimi: kuraldan bagimsiz; kullanici tercihi (web_enabled)
        acik ise olusturulur.
      - Email/SMS/Telegram/WhatsApp: kuralda ilgili notify_* AKTIF olmali.
        Kullanici tercihi yalnizca OPT-OUT'tur (bkz. `_get_pref`) — hic
        tercih kaydetmemis kullanici kuralda secilen kanaldan bildirim alir.
      - Telegram: bot tek bir kanaldan broadcast eder ama scope'taki
        kullanicilardan EN AZ BIRI telegram_enabled olmali. Hepsi elle
        kapatmissa Telegram atilmaz.
      - Kural bulunamadiysa (haberlesme/kalite alarmi, manuel test, adi
        degistirilmis kural) FAIL-OPEN: sistemde ETKIN olan tum kanallardan
        gonderilir. Eski davranis "sadece web"di; cihaz haberlesmeden
        dustugunde operatore hicbir sey gitmiyordu.
      - WhatsApp grup modu acikken secili gruplara alarm KOSULSUZ gider;
        grup hedefi operator tarafindan bilincli secilmistir.
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
    # Kural yoksa kanal kapisi acilir (fail-open) — sistem ayarlari ve
    # kullanicinin opt-out'u yine de gecerli, yani spam riski yok.
    rule_email = rule.notify_email if rule is not None else True
    rule_sms = rule.notify_sms if rule is not None else True
    rule_telegram = rule.notify_telegram if rule is not None else True
    rule_whatsapp = rule.notify_whatsapp_web if rule is not None else True
    if rule is None:
        logger.info(
            "alarm_dispatch_kural_yok alarm_id=%d title=%r — tum etkin "
            "kanallardan gonderiliyor",
            alarm.id, alarm.title,
        )
    group_mode = bool(settings is not None and settings.whatsapp_web_group_mode)
    alarm_rank = _level_rank(alarm.level)
    # Mesaj govdesi (cihaz + faz + hat/bolge) bir kez cikarilir; SMS,
    # WhatsApp ve Telegram ayni bilgiyi kendi bicimiyle basar.
    ctx = _alarm_context(db, alarm)
    any_telegram_optin = False
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
            _send_sms_for_user(settings, user, alarm, ctx)
        # WhatsApp kisisel — SADECE grup modu KAPALI iken. Grup modu aciksa
        # kisiye tek tek mesaj ATILMAZ (tek mod: gruba tek mesaj).
        if (
            rule_whatsapp
            and getattr(pref, "whatsapp_web_enabled", True)
            and not group_mode
        ):
            _send_whatsapp_for_user(settings, user, alarm, ctx)
        # Telegram: kullanici bu kanali elle kapatmis mi?
        if getattr(pref, "telegram_enabled", True):
            any_telegram_optin = True
    # Telegram: kural izin verdi + kanali elle kapatmamis en az 1 kullanici
    # varsa global broadcast. Bot tek bir kanaldan yayin yapar (ayar listesi
    # icinde tanimli chat_ids), bu yuzden kisi-bazli filtre yapamayiz.
    if rule_telegram and any_telegram_optin:
        _send_telegram_broadcast(settings, alarm, ctx)
    # WhatsApp grup: grup modu acik ise secili grup JID listesine KOSULSUZ
    # broadcast — kural bazli kanal secimi ARANMAZ. Gerekce: grup hedefi
    # operator tarafindan "buraya her alarm dussun" niyetiyle secilir; tek
    # tek her kuralda WhatsApp'i isaretlemeyi unutmak sessiz kayip demektir.
    # Master switch (whatsapp_web_enabled) ve grup JID listesi kontrolu
    # _send_whatsapp_group_broadcast icinde yapilir.
    if group_mode:
        _send_whatsapp_group_broadcast(settings, alarm, ctx)


def _unused_for_imports(_d: Device, _i: Iterable):  # pragma: no cover
    return None
