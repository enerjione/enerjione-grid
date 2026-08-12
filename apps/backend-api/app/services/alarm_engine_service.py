import logging
from datetime import datetime, timezone
from uuid import uuid4

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.alarm import AlarmComment, AlarmEvent
from app.models.alarm_rule import AlarmRule
from app.models.user import User
from app.services.event_service import record_event
from app.services.notification_service import create_notification, notify_users
from app.services.outbox_service import enqueue_outbox_event

logger = logging.getLogger(__name__)


# Cozulmus (reset=True) alarmlar icin tavan. Bunlar zamanla sinirsiz buyur;
# arayuzde "gecmis" islevi gorurler ve kirpilmalari zararsizdir.
RESOLVED_ALARM_LIMIT = 500

# AKTIF alarmlar icin emniyet supabi. Aktif alarm sayisi dogasi geregi
# sinirlidir (cihaz sayisi x birkac kural), yani bu tavana dayanmak "cok
# alarm var" degil "bir sey ters gidiyor" demektir; o yuzden loglanir.
ACTIVE_ALARM_HARD_CAP = 5000


def list_alarm_events(
    db: Session, visible_device_ids: set[int] | None = None
) -> list[AlarmEvent]:
    """Alarm olaylari: AKTIF olanlarin TAMAMI + en yeni cozulmusler.

    `visible_device_ids` verilirse daraltma SQL'de yapilir. `None` = kisitsiz
    (engineer/installer). Bos KUME = hicbir cihaz gorunmuyor ve dogru cevap BOS
    listedir — `if not visible` gibi bir kontrol bos kumeyi "kisitsiz" saymak
    olurdu ve hicbir sorumluluk alanina atanmamis bir operator TUM alarmlari
    gorurdu.

    NEDEN AKTIF/COZULMUS AYRIMI VAR
    --------------------------------
    Eskiden sorgu tek parcaydi: `order_by(created_at DESC).limit(500)`. Bunun
    600 cihaz olceginde iki agir sonucu vardi:

      1. `alarm_events` tablosunun retention'i YOK ve tek bir haberlesme
         kesintisi 600 satir uretebiliyor. 500'luk pencerenin disina dusen
         ACIK bir alarm API'den HIC donmuyordu.
      2. `DeviceMapTab` marker rengini bu listeden hesapliyor. Yani acik bir
         ariza listeden dusunce HARITADAKI MARKER YESILE DONUYORDU.

         > Cuma aksami bir cihazda kalici hat arizasi olusur. Pazartesi sabahi
         > operator haritaya bakar: marker yesil, "Aktif Alarmlar" sekmesinde
         > de yok. Ariza uc gundur aciktir.

    Bir ariza izleme urununde bu, "yavaslama" degil SESSIZ YANLIS VERIDIR;
    urunden beklenen tek seyin tam tersi. Bu yuzden tavan yalnizca cozulmus
    kayitlara uygulanir — acik alarm asla kirpilmaz.

    KIRPMA KAPSAMDAN SONRA OLMALI
    -----------------------------
    Ayrica LIMIT eskiden kapsam filtresinden ONCE calisiyordu (kapsam Python'da,
    donusten sonra uygulaniyordu). 20 cihazdan sorumlu bir operator, 600 cihazin
    en yeni 500 kaydi icinden kendine denk gelenleri goruyordu. Artik daraltma
    SQL'de, LIMIT'ten once.
    """
    base = select(AlarmEvent)
    if visible_device_ids is not None:
        base = base.where(AlarmEvent.device_id.in_(visible_device_ids))

    aktif = list(
        db.scalars(
            base.where(AlarmEvent.reset.is_(False))
            .order_by(AlarmEvent.created_at.desc())
            .limit(ACTIVE_ALARM_HARD_CAP)
        ).all()
    )
    if len(aktif) >= ACTIVE_ALARM_HARD_CAP:
        # Buraya gelinmesi beklenmez; gelinirse alarm kurallari bir seyi
        # tekrar tekrar tetikliyor demektir ve bu GORUNUR olmali.
        logger.error(
            "alarm_aktif_tavan_asildi count=%d cap=%d — kirpilan acik alarmlar var",
            len(aktif),
            ACTIVE_ALARM_HARD_CAP,
        )

    cozulmus = list(
        db.scalars(
            base.where(AlarmEvent.reset.is_(True))
            .order_by(AlarmEvent.created_at.desc())
            .limit(RESOLVED_ALARM_LIMIT)
        ).all()
    )

    # Arayuz tek bir "en yeni ustte" listesi bekliyor.
    return sorted(aktif + cozulmus, key=lambda a: a.created_at, reverse=True)


def assign_alarm(db: Session, alarm_id: int, assigned_to: str | None, actor_username: str) -> AlarmEvent:
    alarm = db.get(AlarmEvent, alarm_id)
    if alarm is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Alarm not found")
    previous_assignee = alarm.assigned_to
    new_assignee = assigned_to.strip() if assigned_to else None
    alarm.assigned_to = new_assignee
    record_event(
        db,
        category="alarm",
        event_type="alarm_assigned",
        severity="info",
        actor_username=actor_username,
        message=f"Alarm \"{alarm.title}\" assignment updated",
        metadata={"alarm_id": alarm.id, "assigned_to": new_assignee, "previous_assignee": previous_assignee},
        i18n_key="alarm_assigned",
        i18n_params={"title": alarm.title, "user": new_assignee or "—"},
    )
    # Bildirim mantigi:
    #  * Atanmis kisi degisti VE bos degil ise → atanan kisiye bildirim gonder
    #  * Atayan kullanici kendisi olsa bile bildirim olusur (gorsel feedback icin)
    #  * Ayni kisiye yeniden atama (degisiklik yok) → bildirim atlanir (spam onlemi)
    if new_assignee and new_assignee != previous_assignee:
        if new_assignee == actor_username:
            title = f"Bu alarmı kendi üstünüze aldınız: {alarm.title}"
            title_i18n_key = "alarm_assignment_self"
        else:
            title = f"Size yeni bir alarm atandı: {alarm.title}"
            title_i18n_key = "alarm_assignment_other"
        create_notification(
            db,
            recipient_username=new_assignee,
            category="alarm_assignment",
            severity=alarm.level or "info",
            title=title,
            body=alarm.description,
            actor_username=actor_username,
            link=f"/alarms#alarm-{alarm.id}",
            metadata={
                "alarm_id": alarm.id,
                "level": alarm.level,
                "_title_i18n": {"key": title_i18n_key, "params": {"title": alarm.title}},
            },
        )
        # E-posta bildirimi: kullanicinin email_enabled tercihi acik VE
        # NotificationSettings.smtp_enabled ise atanan kisiye HTML mail.
        try:
            _send_assignment_email(
                db,
                recipient_username=new_assignee,
                kind="alarm",
                title=title,
                description=alarm.description,
                level=alarm.level,
                actor_username=actor_username,
                link_path=f"/alarms#alarm-{alarm.id}",
            )
        except Exception:  # noqa: BLE001
            import logging as _logging
            _logging.getLogger(__name__).exception("alarm_assignment_email_failed")
    db.commit()
    db.refresh(alarm)
    return alarm


def _send_assignment_email(
    db: Session,
    *,
    recipient_username: str,
    kind: str,
    title: str,
    description: str | None,
    level: str | None,
    actor_username: str | None,
    link_path: str | None = None,
) -> None:
    """Atama bildirimi (alarm/fault) icin HTML mail.

    Sistem ayarlarinda SMTP aktif VE kullanicinin email tercihi acik (default
    True) ise gonderilir. UserNotificationPreference satiri yoksa default
    web+email True kabul edilir."""
    from app.models.notification_settings import NotificationSettings
    from app.models.project_settings import ProjectSettings
    from app.models.user import User
    from app.models.user_notification_preference import UserNotificationPreference
    from app.services.email_templates import render_assignment_email
    from app.services.notification_test_service import send_smtp_test

    settings = db.scalar(select(NotificationSettings).limit(1))
    if settings is None or not settings.smtp_enabled:
        return
    user = db.scalar(select(User).where(User.username == recipient_username))
    if user is None or not user.email:
        return
    pref = db.get(UserNotificationPreference, user.id)
    # Default: email_enabled = True (kullanici acikca kapatmadi).
    email_enabled = True if pref is None else bool(pref.email_enabled)
    if not email_enabled:
        return
    # Gonderen adi TEK KAYNAKTAN (bkz. notification_settings_service):
    # Mail Ayarlari > Gonderen Adi doluysa o, degilse Proje Ayarlari zinciri.
    from app.services.notification_settings_service import resolve_sender_name

    project_title = resolve_sender_name(db)
    subject, html_body = render_assignment_email(
        project_title=project_title,
        kind=kind,
        recipient_full_name=user.full_name or user.username,
        title=title,
        description=description,
        level=level,
        actor_username=actor_username,
        link_path=link_path,
    )
    plain_text = (
        f"{title}\n\n"
        f"Atayan: {actor_username or '-'}\n"
        f"Aciklama: {description or '-'}\n"
    )
    send_smtp_test(
        settings,
        recipient_email=user.email,
        subject=subject,
        message=plain_text,
        html_body=html_body,
        from_name=project_title,
    )


def list_alarm_comments(db: Session, alarm_id: int) -> list[AlarmComment]:
    alarm = db.get(AlarmEvent, alarm_id)
    if alarm is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Alarm not found")
    stmt = select(AlarmComment).where(AlarmComment.alarm_event_id == alarm_id).order_by(AlarmComment.created_at.desc())
    return list(db.scalars(stmt).all())


def create_alarm_comment(db: Session, alarm_id: int, comment: str, current_user: User) -> AlarmComment:
    alarm = db.get(AlarmEvent, alarm_id)
    if alarm is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Alarm not found")
    comment_text = comment.strip()
    if not comment_text:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Comment cannot be empty")

    row = AlarmComment(
        alarm_event_id=alarm_id,
        author_username=current_user.username,
        comment=comment_text,
        created_at=datetime.now(timezone.utc),
    )
    db.add(row)
    record_event(
        db,
        category="alarm",
        event_type="alarm_comment_added",
        severity="info",
        actor_username=current_user.username,
        message=f"Comment added to alarm \"{alarm.title}\"",
        metadata={"alarm_id": alarm.id},
        i18n_key="alarm_comment_added",
        i18n_params={"title": alarm.title},
    )
    # Yorum bildirimi: alarmin atandigi kullaniciya (yorum yazandan farkli ise).
    # Title: "Yeni yorum: <alarm adi>" -> bildirim panelinde eyebrow+main olur.
    if alarm.assigned_to and alarm.assigned_to != current_user.username:
        notif_title = f"Yeni yorum: {alarm.title}"
        create_notification(
            db,
            recipient_username=alarm.assigned_to,
            category="alarm_comment",
            severity="info",
            title=notif_title,
            body=comment_text,
            actor_username=current_user.username,
            link=f"/alarms#alarm-{alarm.id}",
            metadata={
                "alarm_id": alarm.id,
                "comment_id": None,
                "_title_i18n": {"key": "alarm_comment_new", "params": {"title": alarm.title}},
            },
        )
        # Email bildirimi: kullanici email tercihi acik VE SMTP enabled ise.
        try:
            _send_assignment_email(
                db,
                recipient_username=alarm.assigned_to,
                kind="alarm",
                title=notif_title,
                description=comment_text,
                level="info",
                actor_username=current_user.username,
                link_path=f"/alarms#alarm-{alarm.id}",
            )
        except Exception:  # noqa: BLE001
            import logging as _logging
            _logging.getLogger(__name__).exception("alarm_comment_email_failed")
    db.commit()
    db.refresh(row)
    return row


def acknowledge_alarm(db: Session, alarm_id: int, actor_username: str) -> AlarmEvent:
    alarm = db.get(AlarmEvent, alarm_id)
    if alarm is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Alarm not found")
    now = datetime.now(timezone.utc)
    alarm.acknowledged = True
    alarm.acknowledged_at = now
    record_event(
        db,
        category="alarm",
        event_type="alarm_acknowledged",
        severity="info",
        actor_username=actor_username,
        message=f"Alarm \"{alarm.title}\" acknowledged",
        metadata={"alarm_id": alarm.id},
        i18n_key="alarm_acknowledged",
        i18n_params={"title": alarm.title},
    )
    # SCADA modeli: cihaz zaten normale donmusse (reset) ve simdi onaylandiysa
    # alarm yasam dongusu tamamlanmistir -> kaydi sil (aktif alarm listesinde
    # asili kalmasin). Gecmis event log'da durur (Olaylar sayfasi).
    if alarm.reset:
        return _finalize_acknowledged_reset(db, alarm, actor_username)
    db.commit()
    db.refresh(alarm)
    return alarm


def _finalize_acknowledged_reset(db: Session, alarm: AlarmEvent, actor_username: str) -> AlarmEvent:
    """Onaylanmis + reset olmus alarmi sil, response icin detached kopya don."""
    # Response icin snapshot (silindikten sonra ORM nesnesi kullanilamaz).
    snapshot = AlarmEvent(
        id=alarm.id,
        device_id=alarm.device_id,
        level=alarm.level,
        title=alarm.title,
        description=alarm.description,
        signal_key=alarm.signal_key,
        assigned_to=alarm.assigned_to,
        acknowledged=True,
        reset=True,
        acknowledged_at=alarm.acknowledged_at,
        reset_at=alarm.reset_at,
        produces_fault=alarm.produces_fault,
        created_at=alarm.created_at,
    )
    title = alarm.title
    alarm_id = alarm.id
    db.query(AlarmComment).filter(AlarmComment.alarm_event_id == alarm_id).delete(synchronize_session=False)
    db.delete(alarm)
    record_event(
        db,
        category="alarm",
        event_type="alarm_auto_cleared",
        severity="info",
        actor_username=actor_username,
        message=f"Alarm \"{title}\" onaylandi ve normale donmustu -> temizlendi",
        metadata={"alarm_id": alarm_id, "title": title},
        i18n_key="alarm_auto_cleared_acked",
        i18n_params={"title": title},
    )
    db.commit()
    # snapshot session'a hic eklenmedi -> zaten detached, dogrudan don.
    return snapshot


def reset_alarm(db: Session, alarm_id: int, actor_username: str) -> AlarmEvent:
    alarm = db.get(AlarmEvent, alarm_id)
    if alarm is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Alarm not found")
    alarm.reset = True
    alarm.reset_at = datetime.now(timezone.utc)
    record_event(
        db,
        category="alarm",
        event_type="alarm_reset",
        severity="warning",
        actor_username=actor_username,
        message=f"Alarm \"{alarm.title}\" reset",
        metadata={"alarm_id": alarm.id},
        i18n_key="alarm_reset",
        i18n_params={"title": alarm.title},
    )
    db.commit()
    db.refresh(alarm)
    return alarm


def acknowledge_all_alarms(
    db: Session,
    actor_username: str,
    visible_device_ids: set[int] | None = None,
) -> list[AlarmEvent]:
    """Gorunur alarmlari toplu onaylar.

    KAPSAM BURADA UYGULANIR — cagirandaki yanit filtresinde DEGIL.
    Eskiden mutasyon TUM alarmlara uygulaniyordu ve kapsam yalnizca donen
    listeye vuruluyordu: hicbir sorumluluk alanina atanmamis bir operator bu
    ucu cagirinca sistemdeki TUM alarmlar onaylaniyor, resetlenmis olanlar
    yorumlariyla birlikte KALICI SILINIYORDU — ve yanit bos dondugu icin
    arayuzde hicbir sey olmamis gibi gorunuyordu.
    """
    alarms = list_alarm_events(db, visible_device_ids)
    now = datetime.now(timezone.utc)
    remaining: list[AlarmEvent] = []
    for alarm in alarms:
        alarm.acknowledged = True
        alarm.acknowledged_at = now
        # Zaten normale donmus olanlari onaylayinca sil (SCADA: dongu tamam).
        if alarm.reset:
            db.query(AlarmComment).filter(AlarmComment.alarm_event_id == alarm.id).delete(synchronize_session=False)
            db.delete(alarm)
        else:
            remaining.append(alarm)
    record_event(
        db,
        category="alarm",
        event_type="alarm_acknowledge_all",
        severity="info",
        actor_username=actor_username,
        message="Tüm alarmlar onaylandı",
        metadata={"count": len(alarms)},
        i18n_key="alarm_acknowledge_all",
        i18n_params={"count": len(alarms)},
    )
    db.commit()
    return remaining


def reset_all_alarms(
    db: Session,
    actor_username: str,
    visible_device_ids: set[int] | None = None,
) -> list[AlarmEvent]:
    """Gorunur alarmlari toplu resetler.

    KAPSAM BURADA UYGULANIR. Kapsamsiz haliyle bir operator tum acik
    alarmlari resetleyebiliyordu; `fault_recompute` sonrasinda haritadaki
    GERCEK arizalar kayboluyor ve geriye olay kaydinda tek satir kaliyordu.
    """
    alarms = list_alarm_events(db, visible_device_ids)
    now = datetime.now(timezone.utc)
    for alarm in alarms:
        alarm.reset = True
        alarm.reset_at = now
    record_event(
        db,
        category="alarm",
        event_type="alarm_reset_all",
        severity="warning",
        actor_username=actor_username,
        message="Tüm alarmlar resetlendi",
        metadata={"count": len(alarms)},
        i18n_key="alarm_reset_all",
        i18n_params={"count": len(alarms)},
    )
    db.commit()
    return alarms


def delete_alarm(db: Session, alarm_id: int, actor_username: str) -> None:
    """Reset edilmis (normal'e donmus) bir alarmi sil. Acik alarm silinemez."""
    alarm = db.get(AlarmEvent, alarm_id)
    if alarm is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Alarm not found")
    if not alarm.reset:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Sadece resetlenmis (normal'e donmus) alarmlar silinebilir.",
        )
    alarm_title = alarm.title  # Sildikten sonra erişilemez; once kopyalayalim
    # Yorumlari da sil (FK guvenligi).
    db.query(AlarmComment).filter(AlarmComment.alarm_event_id == alarm_id).delete(synchronize_session=False)
    db.delete(alarm)
    record_event(
        db,
        category="alarm",
        event_type="alarm_deleted",
        severity="info",
        actor_username=actor_username,
        message=f"Alarm \"{alarm_title}\" deleted",
        metadata={"alarm_id": alarm_id, "title": alarm_title},
        i18n_key="alarm_deleted",
        i18n_params={"title": alarm_title},
    )
    db.commit()


#: Kural kaydi HIC yoksa kullanilan varsayilanlar (bkz. `comm_loss_rule`).
COMM_VARSAYILAN_BASLIK = "Haberleşme arızası"
COMM_VARSAYILAN_SEVIYE = "critical"


def comm_loss_rule(db: Session) -> AlarmRule | None:
    """Haberlesme arizasinin STANDART kurali — AKTIF/PASIF ayrimi YAPMADAN.

    Alarmin seviyesi, basligi ve hat arizasi uretip uretmeyecegi artik kodda
    gomulu degil; Alarm Kurallari ekranindan yonetilir.

    Donus None ise kural KAYDI YOK demektir (pasif degil). Ikisi ayni sey
    degil ve cagiran taraf ikisini farkli ele almak zorunda:

      pasif kural -> operator bilerek kapatmis, alarm URETILMEZ.
      kayit yok   -> migration kosmamis ya da kayit elle silinmis; bu bir
                     KURULUM hatasidir ve haberlesme alarmini susturmak
                     cihazin saatlerce sessiz kalmasini gizlerdi (2026-08-07
                     olayi tam olarak buydu). Varsayilanlarla devam edilir.
    """
    return db.scalar(
        select(AlarmRule)
        .where(AlarmRule.rule_kind == "comm_loss")
        .order_by(AlarmRule.is_active.desc(), AlarmRule.id.asc())
        .limit(1)
    )


def handle_telemetry_alarm_event(db: Session, payload: dict) -> None:
    quality = (payload.get("quality") or "good").lower()
    is_fault = quality in {"bad", "offline", "invalid"}
    if not is_fault:
        return
    kural = comm_loss_rule(db)
    if kural is not None and not kural.is_active:
        # Operator standart kurali kapatmis — haberlesme alarmi uretilmez.
        return
    baslik = (kural.name if kural is not None else COMM_VARSAYILAN_BASLIK).strip()
    seviye = (kural.level if kural is not None else None) or COMM_VARSAYILAN_SEVIYE
    ariza_uretir = bool(kural.produces_fault) if kural is not None else False

    device_id = payload.get("device_id")
    device_name = payload.get("device_name") or payload.get("device_code") or "Cihaz"
    signal_key = payload.get("signal_key") or "unknown"
    existing_stmt = (
        select(AlarmEvent)
        .where(AlarmEvent.device_id == device_id)
        .where(AlarmEvent.reset.is_(False))
        .order_by(AlarmEvent.created_at.desc())
        .limit(1)
    )
    existing = db.scalar(existing_stmt)
    if existing is not None:
        return

    # ZAMAN OTORITESI: backend'in algiladigi an. `payload` icinde
    # `source_timestamp` (gateway saati) ve `device_event_at` (cihaz saati)
    # bulunabilir; ikisi de created_at'e YAZILMAZ. Bkz. api/internal.py'deki
    # ayrintili gerekce ve tests/test_alarm_time_authority.py.
    alarm = AlarmEvent(
        device_id=device_id,
        # Seviye ve baslik KURALDAN. Baslik ayrica bildirim kanali secimini
        # de belirliyor (`_resolve_active_rule` alarm basligi ile kural adini
        # eslestirir); cihaz adini basliga gomseydik hicbir alarm kuralla
        # eslesmez ve kanal secimi hep fail-open kalirdi.
        level=seviye,
        title=baslik,
        description=(
            f"{device_name}: {signal_key} sinyalinde kalite '{quality}' olarak geldi."
        ),
        created_at=datetime.now(timezone.utc),
        # Analiz katmani "hangi cihazin haberlesmesi sik kopuyor" sorusunu
        # bu alandan cevapliyor; baslik/`signal_key` guvenilir ayirt edici
        # degil (bkz. AlarmEvent.kind).
        kind="comm_loss",
        # HAT ARIZASI URETIR MI — KURALDAN (varsayilani False).
        #
        # Model varsayilani True idi ve bu alan yazilmiyordu: sessiz kalan
        # cihaz `fault_recompute_service` icin "arizayi GORDUM" diyen bir
        # cihaz sayiliyor, kendisi ile sonraki cihaz arasindaki aralikta hat
        # arizasi aciliyordu. Haberlesmesi kopan cihaz ariza akimi gormus
        # DEGILDIR, sadece bilmiyoruzdur — ama karar artik operatorun.
        produces_fault=ariza_uretir,
    )
    db.add(alarm)
    record_event(
        db,
        category="alarm",
        event_type="alarm_created",
        severity="warning",
        device_code=payload.get("device_code"),
        message=f"Automatic alarm raised for {device_name}",
        metadata={"signal_key": signal_key, "quality": quality},
        i18n_key="alarm_created",
        i18n_params={"device": device_name},
    )
    alarm_event_payload = {
        "message_id": str(uuid4()),
        "correlation_id": payload.get("correlation_id") or payload.get("message_id"),
        "device_id": device_id,
        "device_code": payload.get("device_code"),
        "device_name": device_name,
        "signal_key": signal_key,
        "quality": quality,
    }
    enqueue_outbox_event(
        db,
        topic="alarm.created",
        payload=alarm_event_payload,
        dedup_key=alarm_event_payload["message_id"],
    )
