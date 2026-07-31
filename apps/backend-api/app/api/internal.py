import hmac
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, Header, HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.db.session import get_db
from app.models.alarm import AlarmEvent
from app.models.alarm_rule import AlarmRule
from app.models.device import Device
from app.models.gateway import Gateway
from app.models.outbound_target import OutboundTarget
from app.models.signal_catalog import SignalCatalog
from app.schemas.alarm_rule import AlarmRuleRead
from app.schemas.device import DeviceRead
from app.schemas.gateway import GatewayRead
from app.schemas.internal import InternalAlarmClear, InternalAlarmIngest
from app.schemas.modbus import ModbusPlanRead
from app.schemas.outbound import OutboundTargetRead
from app.schemas.signal_catalog import SignalCatalogRead
from app.services import modbus_plan_service
from app.services.event_service import record_event
from app.services.notification_service import create_notification

router = APIRouter(prefix="/internal", tags=["internal"])


def _extract_service_name(request) -> str:
    """X-Service-Name header'indan caller servisini cikar (FastAPI Request)."""
    try:
        return request.headers.get("x-service-name", "unknown")
    except Exception:  # noqa: BLE001
        return "unknown"


def _require_service_token(token: str | None, service_name: str | None = None) -> None:
    """Timing-safe `INTERNAL_SERVICE_TOKEN` dogrulamasi.

    `hmac.compare_digest` karakter-karakter eslesme yerine sabit-zamanli
    karsilastirma yapar; saldirgan timing-attack ile token enumerate edemez.
    `token` None ise bos string ile karsilastirilir — yine 401 doner.

    `service_name` parametresi `X-Service-Name` header'indan gelir. Tek
    paylasilan internal token oldugu icin breach forensic'inde hangi servis
    geldi log'a yazilir. Worker'lar `_validate_required_secrets`'lerde
    bu header'i set etmeli (alarm-service, notification-worker, iec104-outbound).
    """
    expected = (settings.internal_service_token or "").encode("utf-8")
    actual = (token or "").encode("utf-8")
    if not hmac.compare_digest(actual, expected):
        import logging as _logging
        _logging.getLogger(__name__).warning(
            "internal_token_invalid svc=%s", service_name or "unknown"
        )
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid service token")
    # Basarili cagri — caller'i logla (debug/audit icin)
    import logging as _logging
    _logging.getLogger(__name__).info(
        "internal_call_ok svc=%s", service_name or "unknown"
    )


@router.get("/alarm-rules", response_model=list[AlarmRuleRead])
def list_alarm_rules_internal(
    db: Session = Depends(get_db),
    x_service_token: str | None = Header(default=None),
):
    """Alarm-service'in aktif kurallari cekmesi icin internal endpoint."""
    _require_service_token(x_service_token)
    # _row_to_read composite kurallarda expression_json'i parse edip
    # AlarmRuleRead.expression alanini doldurur. alarm-service worker'i
    # bu alani kullanarak AND/OR mantiksal kuralları degerlendirir.
    from app.api.alarm_rules import _row_to_read
    stmt = select(AlarmRule).where(AlarmRule.is_active.is_(True))
    return [_row_to_read(r) for r in db.scalars(stmt).all()]


@router.get("/signals", response_model=list[SignalCatalogRead])
def list_signals_internal(
    db: Session = Depends(get_db),
    x_service_token: str | None = Header(default=None),
):
    """Ic servislerin standart sinyal listesini (supports_alarm dahil) cekmesi icin."""
    _require_service_token(x_service_token)
    stmt = select(SignalCatalog).where(SignalCatalog.is_active.is_(True))
    return list(db.scalars(stmt).all())


@router.get("/devices", response_model=list[DeviceRead])
def list_devices_internal(
    db: Session = Depends(get_db),
    x_service_token: str | None = Header(default=None),
):
    """iec104-outbound gibi ic servislerin cihaz listesini cekmesi icin.

    IEC 104 outbound servisi point registry'sini insa ederken cihaz code'larini
    deterministik bir sirayla bilmek zorunda; bu endpoint bunu saglar.
    """
    _require_service_token(x_service_token)
    stmt = select(Device).order_by(Device.code.asc())
    return list(db.scalars(stmt).all())


@router.get("/outbound-targets", response_model=list[OutboundTargetRead])
def list_outbound_targets_internal(
    db: Session = Depends(get_db),
    x_service_token: str | None = Header(default=None),
):
    """iec104-outbound servisinin aktif IEC 104 hedeflerini cekmesi icin.

    Sadece `is_active=True` olanlar; servis kendi protocol filtrelemesini yapar
    (genel REST/MQTT hedeflerini gormesi sorun degildir, sessizce atlar)."""
    _require_service_token(x_service_token)
    stmt = (
        select(OutboundTarget)
        .where(OutboundTarget.is_active.is_(True))
        .order_by(OutboundTarget.id.asc())
    )
    return list(db.scalars(stmt).all())


@router.get("/modbus-plans", response_model=list[ModbusPlanRead])
def list_modbus_plans_internal(
    db: Session = Depends(get_db),
    x_service_token: str | None = Header(default=None),
):
    """modbus-outbound worker'inin yayinlayacagi adres planlari.

    Worker adresleri KENDISI HESAPLAMAZ; backend'in urettigi plani birebir
    uygular. Boylece web arayuzunde gosterilen / CSV ile disa aktarilan adres
    tablosu ile sahada yayinlanan adres arasinda ayrisma olamaz.

    Eksik cihaz slotlari bu cagri sirasinda atanip kalici yazilir (mevcut
    slotlar korunur), yani yeni bir cihaz eklendiginde worker'in bir sonraki
    refresh'inde otomatik yayina girer.
    """
    _require_service_token(x_service_token)
    targets = list(
        db.scalars(
            select(OutboundTarget)
            .where(OutboundTarget.is_active.is_(True))
            .where(OutboundTarget.protocol == "modbus")
            .order_by(OutboundTarget.id.asc())
        ).all()
    )
    return [
        ModbusPlanRead(**modbus_plan_service.serialize_plan(db, target))
        for target in targets
    ]


_IDEMPOTENCY_CONSUMER_INTERNAL_ALARMS = "internal-alarms"


@router.post("/alarms", status_code=status.HTTP_202_ACCEPTED)
def ingest_alarm(
    payload: InternalAlarmIngest,
    db: Session = Depends(get_db),
    x_service_token: str | None = Header(default=None),
):
    _require_service_token(x_service_token)

    # Idempotency: alarm-service 5xx sonrasi retry yaparsa ayni mesaji tekrar
    # yollar; mevcut content-dedup (device + level + title + signal_key) farkli
    # signal_key veya kararsiz title icin yetersiz kalir. processed_messages
    # tablosu uzerinden message_id bazli dedup ekle. Worker yeni mesajda her
    # zaman unique message_id ureteceginden duplicate alarm satiri olusmaz.
    if payload.message_id:
        from app.services.idempotency_service import is_processed

        if is_processed(
            db,
            consumer_name=_IDEMPOTENCY_CONSUMER_INTERNAL_ALARMS,
            message_id=payload.message_id,
        ):
            return {"status": "duplicate_ignored", "message_id": payload.message_id}

    device_id = payload.device_id
    if device_id is None and payload.device_code:
        device = db.scalar(select(Device).where(Device.code == payload.device_code))
        device_id = device.id if device else None
    if device_id is None:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="device_id or valid device_code required")

    # Dedup: ayni cihaz + ayni seviye + ayni baslik (+ varsa ayni signal_key)
    # icin halen acik (reset edilmemis) bir alarm varsa yeni satir UPRETME.
    # Bunun yerine mevcut alarmin description'unu gunceller. Boylece ayni
    # hata sebebiyle 100 kez ayni alarm uretilmesini engeller — ama farkli
    # sinyal/level kombinasyonlari birbirini eziyor olmaz.
    dedup_stmt = (
        select(AlarmEvent)
        .where(AlarmEvent.device_id == device_id)
        .where(AlarmEvent.level == payload.level)
        .where(AlarmEvent.title == payload.title)
        .where(AlarmEvent.reset.is_(False))
        .order_by(AlarmEvent.created_at.desc())
        .limit(1)
    )
    if payload.signal_key:
        # Ayni signal_key ile (veya henuz signal_key kaydedilmemis eski satirla)
        # daha sıkı eslesme — boylece farklı sinyallerin ayni baslikli kurallari
        # yanlislikla birbirini ezmez.
        dedup_stmt = dedup_stmt.where(
            (AlarmEvent.signal_key == payload.signal_key) | AlarmEvent.signal_key.is_(None)
        )
    existing = db.scalar(dedup_stmt)

    if existing is not None:
        # Mevcut acik alarm icin yeni bilgi geldi → sadece description'u guncelle.
        existing.description = payload.description
        # Eski kayit signal_key olmadan acilmissa, yeni payload'tan tamamla.
        if payload.signal_key and not existing.signal_key:
            existing.signal_key = payload.signal_key
        # Kural produces_fault'u sonradan degismis olabilir; mevcut acik alarmi
        # da senkronla. Boylece kural "ariza uretmez"e cevrilince acik alarm da
        # haritadan/fault'tan duser (recompute sonraki tetikte yansitir).
        existing.produces_fault = payload.produces_fault
        if payload.message_id:
            from app.services.idempotency_service import mark_processed

            mark_processed(
                db,
                consumer_name=_IDEMPOTENCY_CONSUMER_INTERNAL_ALARMS,
                message_id=payload.message_id,
            )
        db.commit()
        return {"status": "deduplicated", "alarm_id": existing.id}

    # Ayni sinyal icin "Normale Donen - Onay Bekliyor" listesinde bekleyen
    # onaylanmamis kayit varsa, alarm tekrar tetiklendigi icin onu sil. Kullanici
    # "alarmim normale dondu, kabul edeyim" demeden sinyal yine gitti — bu
    # durumda alt panelde tutmaya gerek yok, ust panele yeni satir gelir.
    #
    # ESLESME ONCELIK SIRASI:
    #   1) signal_key varsa: ayni signal_key (level/title uyusmasa bile siler).
    #      Ayni sinyal icin baska kural varsa onlar zaten farkli title ile
    #      kendi kaydina sahip; burada ayni signal_key + reset=true + !ack tek
    #      bir kayit olacak (alt panelde her kural icin tek satir).
    #   2) signal_key yok (eski kayit) → title + level esit-az duyarli eslesme.
    base_stale = (
        select(AlarmEvent)
        .where(AlarmEvent.device_id == device_id)
        .where(AlarmEvent.reset.is_(True))
        .where(AlarmEvent.acknowledged.is_(False))
    )

    deleted_any = False
    if payload.signal_key:
        # 1) Yeni kayitlar (signal_key dolu) — aynı sinyalin tüm pending'leri.
        for stale in db.scalars(
            base_stale.where(AlarmEvent.signal_key == payload.signal_key)
        ).all():
            db.delete(stale)
            deleted_any = True
        # 2) Eski kayitlar (signal_key NULL) ama ayni title+level → onları da sil.
        for stale in db.scalars(
            base_stale.where(AlarmEvent.signal_key.is_(None))
            .where(AlarmEvent.title == payload.title)
            .where(AlarmEvent.level == payload.level)
        ).all():
            db.delete(stale)
            deleted_any = True
    else:
        # Payload'da signal_key gelmediyse (eski alarm-service uretimi),
        # title + level esitligi ile sil.
        for stale in db.scalars(
            base_stale.where(AlarmEvent.title == payload.title)
            .where(AlarmEvent.level == payload.level)
        ).all():
            db.delete(stale)
            deleted_any = True

    if deleted_any:
        db.flush()

    # ZAMAN OTORITESI: alarm saati DAIMA backend'in olayi ALGILADIGI andir.
    #
    # `payload.source_timestamp` gelir ama created_at'e ASLA yazilmaz; yalnizca
    # asagidaki olay metadata'sinda teshis amaciyla saklanir. Gerekce:
    #   * Cihaz/gateway saati kayabilir (RTC pili biter, saat 2000-01-01'e
    #     doner). O damgayla acilan alarm listede 26 yil once gorunur, operator
    #     onu HIC gormez.
    #   * Ileri kaymis bir saat alarmi listenin tepesine cakar ve gercek yeni
    #     alarmlari bastirir.
    #   * SLA/mudahale suresi olcumu cihaz saatine baglanamaz: mudahale suresi
    #     "biz ne zaman haberdar olduk"tan itibaren isler.
    # Cihazin kendi zamani teshis icin ayri kolonlarda duruyor (bkz.
    # telemetry.device_event_at) ve alarm akisina KARISMAZ.
    # Bu kural tests/test_alarm_time_authority.py ile kilitlidir.
    alarm = AlarmEvent(
        device_id=device_id,
        level=payload.level,
        title=payload.title,
        description=payload.description,
        signal_key=payload.signal_key,
        produces_fault=payload.produces_fault,
        created_at=datetime.now(timezone.utc),
    )
    db.add(alarm)
    db.flush()  # alarm.id'yi notification metadata'sinda kullanabilmek icin
    record_event(
        db,
        category="alarm",
        event_type="alarm_triggered",
        severity="warning",
        device_code=payload.device_code,
        message=f"Alarm rule triggered: {payload.title}",
        metadata={
            "message_id": payload.message_id,
            "correlation_id": payload.correlation_id,
            "source_gateway": payload.source_gateway,
            "signal_key": payload.signal_key,
            "alarm_id": alarm.id,
        },
        i18n_key="alarm_triggered",
        i18n_params={"title": payload.title},
    )
    # Broadcast bildirim — recipient_username=None ile tum aktif kullanicilara
    # zilde gosterilir. Spesifik atama assign_alarm'da yapilir; o zaman ek bir
    # notification olusturulur (atanan kisiye spesifik).
    severity_for_notif = "critical" if (payload.level or "").lower() == "critical" else (
        "error" if (payload.level or "").lower() in ("error", "high") else "warning"
    )
    # Cihaz adini bildirimde gostermek icin Device row'undan cek (yoksa code).
    # Notification metadata zenginlestirilir: frontend bunu okur ve "hangi
    # cihaz, hangi kaynak (master/sat01), hangi hat / hangi bolge" detaylarini
    # gorsel kart olarak gosterir.
    device_name = None
    if device_id is not None:
        dev_row = db.get(Device, device_id)
        if dev_row is not None:
            device_name = dev_row.name
    # Sinyal kaynagi: signal_key prefix'inden turet (master/sat01/sat02).
    signal_source = None
    if payload.signal_key and "." in payload.signal_key:
        signal_source = payload.signal_key.split(".", 1)[0].lower()
    # Hat ve bolge bilgisi — cihaz LineSegment'e atanmissa Line + Region
    # adlarini alabiliriz. Cihaz hicbir hata atanmamissa null kalir; frontend
    # o satiri gostermez.
    line_name: str | None = None
    line_code: str | None = None
    region_name: str | None = None
    if device_id is not None:
        try:
            from app.models.grid_topology import Line, LineSegment, Region
            seg = db.scalar(
                select(LineSegment).where(LineSegment.device_id == device_id).limit(1)
            )
            if seg is not None:
                line_row = db.get(Line, seg.line_id)
                if line_row is not None:
                    line_name = line_row.name
                    line_code = line_row.code
                    region_row = db.get(Region, line_row.region_id)
                    if region_row is not None:
                        region_name = region_row.name
        except Exception:  # noqa: BLE001
            # Topoloji hatasi alarm akisini bozmasin — sadece hat/bolge alanlari
            # eksik kalir; bildirim yine ulasir.
            pass
    create_notification(
        db,
        recipient_username=None,  # broadcast
        category="alarm",
        severity=severity_for_notif,
        title=f"Yeni alarm: {payload.title}",
        body=payload.description,
        actor_username=None,
        link=f"/alarms#alarm-{alarm.id}",
        metadata={
            "alarm_id": alarm.id,
            "device_code": payload.device_code,
            "device_name": device_name,
            "level": payload.level,
            "signal_key": payload.signal_key,
            "signal_source": signal_source,
            "source_gateway": payload.source_gateway,
            "line_name": line_name,
            "line_code": line_code,
            "region_name": region_name,
            "value": payload.value,
            "value_string": payload.value_string,
            "threshold": payload.threshold,
            "operator": payload.operator,
            "source_timestamp": payload.source_timestamp.isoformat()
            if payload.source_timestamp
            else None,
            "_title_i18n": {"key": "alarm_new", "params": {"title": payload.title}},
        },
    )
    # Ariza listesini yeniden hesapla — debounced (alarm firtinasinda
    # her cagrida calismaz; 500ms min interval + coalescing).
    try:
        from app.services.fault_recompute_service import recompute_faults_debounced
        recompute_faults_debounced(db)
    except Exception:  # noqa: BLE001
        # Fault recompute hatasi alarm akisini bozmasin — log yeterli.
        import logging as _logging
        _logging.getLogger(__name__).exception("fault_recompute_failed_after_ingest")
    # Notification dispatcher: ilgili kullanicilara web/email/sms gonder.
    # Feature flag ile devre disi birakilabilir — notification-worker
    # production'a alindiginda buradaki cagri kapanir ve dispatch
    # sorumlulugu tek olarak notification-worker'da olur.
    if settings.notification_inline_dispatch_enabled:
        try:
            from app.services.notification_dispatch_service import dispatch_alarm_notifications
            dispatch_alarm_notifications(db, alarm)
        except Exception:  # noqa: BLE001
            import logging as _logging
            _logging.getLogger(__name__).exception("notification_dispatch_failed")

    # Outbound dispatcher: REST webhook / MQTT / IEC 104 hedeflerine alarm
    # payload'unu yolla. OutboundTarget'lar (is_active=true) icin retry'li
    # gonderim yapar; tablonun is_active=false ya da event_filter='telemetry'
    # olanlari otomatik atlar. Bu cagri tasarim eksiğiydi — webhook
    # kurulu olsa bile alarm gelse hicbir POST atilmiyordu.
    try:
        from app.services.outbound_dispatch_service import dispatch_event
        outbound_payload = {
            "message_id": f"alarm-{alarm.id}",
            "correlation_id": f"alarm-{alarm.id}",
            "event_kind": "alarm",
            "alarm_id": alarm.id,
            "device_code": payload.device_code,
            "device_name": device_name,
            "signal_key": payload.signal_key,
            "signal_source": signal_source,
            "source_gateway": payload.source_gateway,
            "title": payload.title,
            "description": payload.description,
            "level": payload.level,
            "severity": severity_for_notif,
            "value": payload.value,
            "value_string": payload.value_string,
            "threshold": payload.threshold,
            "operator": payload.operator,
            "line_name": line_name,
            "line_code": line_code,
            "region_name": region_name,
            "source_timestamp": payload.source_timestamp.isoformat()
            if payload.source_timestamp
            else None,
            "created_at": alarm.created_at.isoformat() if alarm.created_at else None,
        }
        dispatch_event(db, event_kind="alarm", payload=outbound_payload)
    except Exception:  # noqa: BLE001
        # Outbound hatasi alarm akisini bozmasin — log + devam.
        import logging as _logging
        _logging.getLogger(__name__).exception("outbound_dispatch_failed alarm_id=%s", alarm.id)

    if payload.message_id:
        from app.services.idempotency_service import mark_processed

        mark_processed(
            db,
            consumer_name=_IDEMPOTENCY_CONSUMER_INTERNAL_ALARMS,
            message_id=payload.message_id,
        )
    db.commit()
    return {"status": "accepted", "alarm_id": alarm.id}


@router.post("/alarms/clear", status_code=status.HTTP_202_ACCEPTED)
def clear_alarm(
    payload: InternalAlarmClear,
    db: Session = Depends(get_db),
    x_service_token: str | None = Header(default=None),
):
    """Alarm-service kosul artik karsilanmiyor dediginde acik alarmi reset=True yapar.

    UI'da bu kayit 'Normale Donen - Onay Bekliyor' panelinde gozukmeye baslar;
    kullanici onaylayinca tamamen gizlenir. Boylece `reset` ardisik tetiklerde
    yeni alarm uretimi tekrar mumkun olur (alarm-service'in dedup state'i de
    ayni anda False'a duser).
    """
    _require_service_token(x_service_token)

    device_id = payload.device_id
    if device_id is None and payload.device_code:
        device = db.scalar(select(Device).where(Device.code == payload.device_code))
        device_id = device.id if device else None
    if device_id is None:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="device_id or valid device_code required")

    # Eslesen acik alarmi bul: ayni cihaz + reset=False.
    # Eslesme oncelikleri:
    #   1) signal_key varsa once OnA gore esles (sat01 vs sat02 karismasin)
    #   2) signal_key yoksa veya eski kayit signal_key'siz ise title ile esles
    base_stmt = (
        select(AlarmEvent)
        .where(AlarmEvent.device_id == device_id)
        .where(AlarmEvent.reset.is_(False))
        .order_by(AlarmEvent.created_at.desc())
    )

    existing: AlarmEvent | None = None

    # Tercih 1: signal_key + title tam eslesmesi (en kesin, sat01/sat02 ayrimi)
    if payload.signal_key and payload.title:
        existing = db.scalar(
            base_stmt.where(AlarmEvent.signal_key == payload.signal_key)
            .where(AlarmEvent.title == payload.title)
            .limit(1)
        )

    # Tercih 2: sadece signal_key (geriye uyum: title format degistiyse)
    if existing is None and payload.signal_key:
        existing = db.scalar(
            base_stmt.where(AlarmEvent.signal_key == payload.signal_key).limit(1)
        )

    # Tercih 3: title tam eslesmesi (signal_key gondermeyen eski cagrilar)
    if existing is None and payload.title:
        existing = db.scalar(
            base_stmt.where(AlarmEvent.title == payload.title)
            .where(AlarmEvent.signal_key.is_(None))
            .limit(1)
        )
        # Daha agresif fallback: title tam eslesmesi (signal_key sart degil).
        # Sadece payload.signal_key da yoksa kullanilir; aksi halde yanlis
        # sinyali resetleme riski var.
        if existing is None and not payload.signal_key:
            existing = db.scalar(base_stmt.where(AlarmEvent.title == payload.title).limit(1))

    # Tercih 4: hicbir filtre yoksa son acik alarm
    if existing is None and not payload.title and not payload.signal_key:
        existing = db.scalar(base_stmt.limit(1))

    if existing is None:
        return {"status": "no_match"}

    # Önemli kural: alarm zaten ONAYLANMIS ise normale donduğunde tarihçeye
    # düşmeden direkt SİLİNİR. Kullanıcı zaten onaylamıştı, bilgilendi → alt
    # panelde gereksiz yer kaplamasın. Sadece olay log'una gider.
    was_acknowledged = bool(existing.acknowledged)
    alarm_id = existing.id
    alarm_title = existing.title
    if was_acknowledged:
        db.delete(existing)
        record_event(
            db,
            category="alarm",
            event_type="alarm_auto_cleared",
            severity="info",
            device_code=payload.device_code,
            message=f"Acknowledged alarm cleared and removed: {alarm_title}",
            metadata={
                "alarm_id": alarm_id,
                "rule_id": payload.rule_id,
                "signal_key": payload.signal_key,
                "source_gateway": payload.source_gateway,
                "auto_deleted": True,
            },
            i18n_key="alarm_auto_cleared_acked",
            i18n_params={"title": alarm_title},
        )
        try:
            from app.services.fault_recompute_service import recompute_faults_debounced
            recompute_faults_debounced(db)
        except Exception:  # noqa: BLE001
            import logging as _logging
            _logging.getLogger(__name__).exception("fault_recompute_failed_after_clear_ack")
        db.commit()
        return {"status": "cleared_and_deleted", "alarm_id": alarm_id}

    # Onaylanmamis aktif alarm normale dondu → reset=True (alt panele duser)
    existing.reset = True
    existing.reset_at = datetime.now(timezone.utc)
    record_event(
        db,
        category="alarm",
        event_type="alarm_auto_cleared",
        severity="info",
        device_code=payload.device_code,
        message=f"Alarm cleared on site: {alarm_title}",
        metadata={
            "alarm_id": alarm_id,
            "rule_id": payload.rule_id,
            "signal_key": payload.signal_key,
            "source_gateway": payload.source_gateway,
        },
        i18n_key="alarm_auto_cleared",
        i18n_params={"title": alarm_title},
    )
    try:
        from app.services.fault_recompute_service import recompute_faults_debounced
        recompute_faults_debounced(db)
    except Exception:  # noqa: BLE001
        import logging as _logging
        _logging.getLogger(__name__).exception("fault_recompute_failed_after_clear")
    db.commit()
    return {"status": "cleared", "alarm_id": alarm_id}


_IDEMPOTENCY_CONSUMER_INTERNAL_DISPATCH = "internal-dispatch"


@router.post("/notifications/dispatch/{alarm_id}", status_code=status.HTTP_202_ACCEPTED)
def dispatch_notification_for_alarm(
    alarm_id: int,
    db: Session = Depends(get_db),
    x_service_token: str | None = Header(default=None),
    x_message_id: str | None = Header(default=None, alias="X-Message-Id"),
):
    """notification-worker bu endpoint'i `alarm.created` event'i RabbitMQ'dan
    geldiginde cagirir; backend tarafindaki dispatch akisini yeniden tetikler.

    Idempotency: worker requeue + retry sonrasi ayni alarm icin tekrar
    cagrilirsa duplicate mail/SMS/FCM gondermesin diye `X-Message-Id`
    header'i ile dedup. Worker her mesaj icin unique id uretir; backend
    `processed_messages` tablosunda hash'i tutar. Header eksik gelirse
    fallback olarak `alarm_id` ile dedup (eski davranis — bir alarm icin
    sadece bir kez dispatch).

      1. RabbitMQ'dan alarm.created mesajini tuketir (acl/audit korumasi)
      2. Mesajda yer alan `alarm_id` icin bu endpoint'i cagirir + X-Message-Id
      3. Backend `dispatch_alarm_notifications` ile SMTP/Telegram/SMS/FCM gonderir

    Eski "sadece print" davranisi yerine gercek dispatch — production'da
    bildirimler asla kaybolmasin ama duplicate da gitmesin.
    """
    _require_service_token(x_service_token)
    alarm = db.scalar(select(AlarmEvent).where(AlarmEvent.id == alarm_id))
    if alarm is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="alarm not found")

    # Idempotency check — X-Message-Id varsa onu kullan, yoksa alarm_id ile
    # dedup et (her alarm icin tek dispatch).
    msg_id = (x_message_id or "").strip() or f"alarm-{alarm_id}"
    from app.services.idempotency_service import is_processed, mark_processed

    if is_processed(
        db,
        consumer_name=_IDEMPOTENCY_CONSUMER_INTERNAL_DISPATCH,
        message_id=msg_id,
    ):
        return {"status": "duplicate_ignored", "alarm_id": alarm_id, "message_id": msg_id}

    try:
        from app.services.notification_dispatch_service import dispatch_alarm_notifications
        dispatch_alarm_notifications(db, alarm)
    except Exception:  # noqa: BLE001
        import logging as _logging

        # Stack trace sadece server log'una; client'a generic mesaj.
        _logging.getLogger(__name__).exception(
            "notification_dispatch_via_worker_failed alarm_id=%s", alarm_id
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="notification dispatch failed",
        )
    mark_processed(
        db,
        consumer_name=_IDEMPOTENCY_CONSUMER_INTERNAL_DISPATCH,
        message_id=msg_id,
    )
    db.commit()
    return {"status": "dispatched", "alarm_id": alarm_id}


@router.get("/gateways", response_model=list[GatewayRead])
def list_gateways_internal(
    db: Session = Depends(get_db),
    x_service_token: str | None = Header(default=None),
):
    """Kontrol paneli ve diger ic servislerin gateway listesini (is_active dahil)
    cekebilmesi icin token korumali endpoint. Installer login'e gerek kalmaz."""
    _require_service_token(x_service_token)
    stmt = select(Gateway).order_by(Gateway.name.asc())
    return list(db.scalars(stmt).all())


@router.post("/gateways/{gateway_code}/enable", response_model=GatewayRead)
def enable_gateway_internal(
    gateway_code: str,
    db: Session = Depends(get_db),
    x_service_token: str | None = Header(default=None),
):
    """Kontrol panelinin uzak gateway'i aktiflestirmesi icin servis token'li endpoint."""
    _require_service_token(x_service_token)
    row = db.scalar(select(Gateway).where(Gateway.code == gateway_code))
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Gateway not found")
    row.is_active = True
    db.commit()
    db.refresh(row)
    return row


@router.post("/gateways/{gateway_code}/disable", response_model=GatewayRead)
def disable_gateway_internal(
    gateway_code: str,
    db: Session = Depends(get_db),
    x_service_token: str | None = Header(default=None),
):
    """Kontrol panelinin uzak gateway'i pasiflestirmesi icin servis token'li endpoint."""
    _require_service_token(x_service_token)
    row = db.scalar(select(Gateway).where(Gateway.code == gateway_code))
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Gateway not found")
    row.is_active = False
    db.commit()
    db.refresh(row)
    return row
