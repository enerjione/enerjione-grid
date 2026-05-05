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
from app.schemas.outbound import OutboundTargetRead
from app.schemas.signal_catalog import SignalCatalogRead
from app.services.event_service import record_event
from app.services.notification_service import create_notification

router = APIRouter(prefix="/internal", tags=["internal"])


def _require_service_token(token: str | None) -> None:
    if token != settings.internal_service_token:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid service token")


@router.get("/alarm-rules", response_model=list[AlarmRuleRead])
def list_alarm_rules_internal(
    db: Session = Depends(get_db),
    x_service_token: str | None = Header(default=None),
):
    """Alarm-service'in aktif kurallari cekmesi icin internal endpoint."""
    _require_service_token(x_service_token)
    stmt = select(AlarmRule).where(AlarmRule.is_active.is_(True))
    return list(db.scalars(stmt).all())


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


@router.post("/alarms", status_code=status.HTTP_202_ACCEPTED)
def ingest_alarm(
    payload: InternalAlarmIngest,
    db: Session = Depends(get_db),
    x_service_token: str | None = Header(default=None),
):
    _require_service_token(x_service_token)

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

    alarm = AlarmEvent(
        device_id=device_id,
        level=payload.level,
        title=payload.title,
        description=payload.description,
        signal_key=payload.signal_key,
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
        message=f"Alarm kuralı gerçekleşti: {payload.title}",
        metadata={
            "message_id": payload.message_id,
            "correlation_id": payload.correlation_id,
            "source_gateway": payload.source_gateway,
            "signal_key": payload.signal_key,
            "alarm_id": alarm.id,
        },
    )
    # Broadcast bildirim — recipient_username=None ile tum aktif kullanicilara
    # zilde gosterilir. Spesifik atama assign_alarm'da yapilir; o zaman ek bir
    # notification olusturulur (atanan kisiye spesifik).
    severity_for_notif = "critical" if (payload.level or "").lower() == "critical" else (
        "error" if (payload.level or "").lower() in ("error", "high") else "warning"
    )
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
            "level": payload.level,
            "signal_key": payload.signal_key,
        },
    )
    db.commit()
    return {"status": "accepted"}


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
            message=f"Onaylanmış alarm normale döndü ve silindi: {alarm_title}",
            metadata={
                "alarm_id": alarm_id,
                "rule_id": payload.rule_id,
                "signal_key": payload.signal_key,
                "source_gateway": payload.source_gateway,
                "auto_deleted": True,
            },
        )
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
        message=f"Alarm sahada normale döndü: {alarm_title}",
        metadata={
            "alarm_id": alarm_id,
            "rule_id": payload.rule_id,
            "signal_key": payload.signal_key,
            "source_gateway": payload.source_gateway,
        },
    )
    db.commit()
    return {"status": "cleared", "alarm_id": alarm_id}


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
