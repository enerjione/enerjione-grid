from datetime import datetime, timezone
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select, text
from sqlalchemy.exc import ProgrammingError
from sqlalchemy.orm import Session

from app.api.deps import get_current_user, require_role, require_roles
from app.db.session import get_db
from app.models.device import Device
from app.models.device_command import DeviceCommand
from app.models.enums import UserRole
from app.models.gateway import Gateway
from app.models.signal_catalog import SignalCatalog
from app.models.telemetry_history import TelemetryHistory
from app.models.user import User
from app.repositories.device_repository import DeviceRepository
from app.schemas.device import (
    DeviceCommandQueued,
    DeviceCommandRequest,
    DeviceCommandRow,
    DeviceCreate,
    DeviceRead,
    DeviceUpdate,
)
from app.schemas.telemetry import TelemetryAggregatePoint, TelemetryHistoryPoint
from app.services.event_service import record_event
from app.services.scope_service import get_visible_device_ids

# Config-turu komut slug'lari — installer-only. Genel + alarm reset komutlari
# engineer+installer'da kalir. Frontend DeviceCommandsPanel meta'daki 'config'
# grubuyla ayni mantik (backend guvenlik siniri, UI ikincil).
_CONFIG_COMMAND_SLUGS = frozenset(
    {
        "config_update",
        "dnp3_config_update",
        "trigger_config_download",
        "trigger_dnp3_config_download",
        "start_csv_file_upload",
    }
)

router = APIRouter(prefix="/devices", tags=["devices"])


# NOT: Onceden burada bir "stale online -> offline" override calisiyordu;
# DB'de "online" yazmasina ragmen son telemetri esikten eskiyse OFFLINE'a
# dusuruyordu. Ancak:
#   1) datetime karsilastirmasi (timezone offset edge-case'leri) flicker
#      yaratiyordu — kullanici "sayfa yenilendikce farkli sonuc" dedi,
#   2) bagliyken yavas telemetri gonderen cihazlari da offline gosteriyordu,
#   3) tag-engine zaten her telemetri'de communication_status'u dogru
#      sekilde guncelliyor (offline/comm_lost/restart -> OFFLINE; iyi
#      okuma -> ONLINE).
# Frontend tarafinda gateway-down kontrolu (effectiveCommStatus) ek
# koruma sagliyor. DB'deki tek gercege guvenmek en stabil yaklasim.


@router.get("", response_model=list[DeviceRead])
def list_devices(
    gateway_code: str | None = Query(default=None),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    repository = DeviceRepository(db)
    if gateway_code:
        rows = repository.list_devices_by_gateway(gateway_code)
    else:
        rows = repository.list_devices()
    # Operator scope: sadece kendi sorumluluk alanlarindaki cihazlari gosterir.
    visible_ids = get_visible_device_ids(db, current_user)
    if visible_ids is not None:
        rows = [d for d in rows if d.id in visible_ids]
    return rows


@router.post("", response_model=DeviceRead, status_code=status.HTTP_201_CREATED)
def create_device(
    payload: DeviceCreate,
    current_user: User = Depends(require_roles([UserRole.ENGINEER, UserRole.INSTALLER])),
    db: Session = Depends(get_db),
):
    repository = DeviceRepository(db)
    existing = repository.get_by_code(payload.code)
    if existing:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Device code already exists")
    device = repository.create(payload)
    record_event(
        db,
        category="device",
        event_type="device_created",
        severity="info",
        actor_username=current_user.username,
        device_code=device.code,
        message=f"Cihaz eklendi: {device.name} ({device.code})",
        metadata={"device_id": device.id, "gateway_code": device.gateway_code},
        i18n_key="device_created",
        i18n_params={"name": device.name, "code": device.code},
    )
    db.commit()
    return device


def _ensure_device_visible(db: Session, current_user: User, device) -> None:
    """Object-level authz: engineer kendi sorumluluk alanindaki cihazlari
    duzenleyebilir; installer her cihaza erisebilir.

    `get_visible_device_ids` engineer icin `responsibility_areas` tablosundan
    izinli device id'leri doner. Installer icin None doner (sinirsiz).
    IDOR koruma: device_code bilen engineer baska bolgenin cihazini
    update/delete edemez.
    """
    visible = get_visible_device_ids(db, current_user)
    if visible is not None and device.id not in visible:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Bu cihaza erişim yetkiniz yok (responsibility scope dışı)",
        )


@router.patch("/{device_code}", response_model=DeviceRead)
def update_device(
    device_code: str,
    payload: DeviceUpdate,
    # Cihaz config'i (DNP3 ayarlari, IP/adres, extended) sadece INSTALLER
    # degistirebilir. Engineer artik PATCH edemez (yanlis config saha
    # haberlesmesini bozabilir; en ust rol siniri).
    current_user: User = Depends(require_role(UserRole.INSTALLER)),
    db: Session = Depends(get_db),
):
    repository = DeviceRepository(db)
    device = repository.get_by_code(device_code)
    if device is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Device not found")
    _ensure_device_visible(db, current_user, device)
    # Hangi alanlar degisti — operator/muhendis paneli icin event'e koy.
    changes = payload.model_dump(exclude_none=True)
    updated = repository.update(device, payload)
    record_event(
        db,
        category="device",
        event_type="device_updated",
        severity="info",
        actor_username=current_user.username,
        device_code=updated.code,
        message=f"Device updated: {updated.name} ({updated.code})",
        metadata={"device_id": updated.id, "fields": list(changes.keys())},
        i18n_key="device_updated",
        i18n_params={"name": updated.name, "code": updated.code},
    )
    db.commit()
    return updated


@router.delete("/{device_code}", status_code=status.HTTP_204_NO_CONTENT)
def delete_device(
    device_code: str,
    current_user: User = Depends(require_roles([UserRole.ENGINEER, UserRole.INSTALLER])),
    db: Session = Depends(get_db),
):
    repository = DeviceRepository(db)
    device = repository.get_by_code(device_code)
    if device is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Device not found")
    _ensure_device_visible(db, current_user, device)
    name = device.name
    code = device.code
    device_id = device.id
    repository.delete(device)
    record_event(
        db,
        category="device",
        event_type="device_deleted",
        severity="warning",
        actor_username=current_user.username,
        device_code=code,
        message=f"Cihaz silindi: {name} ({code})",
        metadata={"device_id": device_id},
        i18n_key="device_deleted",
        i18n_params={"name": name, "code": code},
    )
    db.commit()
    return None


@router.post("/{device_code}/command", response_model=DeviceCommandQueued)
def queue_device_command(
    device_code: str,
    payload: DeviceCommandRequest,
    current_user: User = Depends(require_roles([UserRole.ENGINEER, UserRole.INSTALLER])),
    db: Session = Depends(get_db),
):
    """Cihaza DNP3 binary output (CROB) komutu KUYRUGA ALIR.

    Yetki: ENGINEER, INSTALLER. Engineer scope disi cihaza komut gonderemez.

    Gateway NAT arkasinda oldugundan backend gateway'e ulasamaz. Komut config-poll
    ile iletilir: burada `device_commands` tablosuna status='pending' satir yazilir,
    gateway her config poll'de (~config_refresh_sec, default 30sn) pending komutlari
    ceker, CROB gonderir ve sonucu `POST /gateways/{code}/command-results` ile bildirir.

    Yanit ANLIK DEGIL: {id, status:'pending'} doner. Gercek sonuc
    `GET /devices/{code}/commands` ile takip edilir.

    Akis: cihaz bul -> gateway bul -> slug'i SignalCatalog binary_output'tan
    dnp3_index'e cevir (allowlist; ham index yok) -> pending satir + audit.
    """
    device = DeviceRepository(db).get_by_code(device_code)
    if device is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Device not found")
    _ensure_device_visible(db, current_user, device)

    if not device.gateway_code:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Cihaz bir gateway'e bagli degil; komut gonderilemez.",
        )
    gateway = db.scalar(select(Gateway).where(Gateway.code == device.gateway_code))
    if gateway is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Gateway bulunamadi: {device.gateway_code}",
        )

    # Config-turu komutlar installer-only. Genel + alarm reset komutlari
    # engineer+installer'da (endpoint seviyesi). Slug config grubundaysa
    # engineer 403 alir.
    slug = payload.command.strip()
    if slug in _CONFIG_COMMAND_SLUGS and current_user.role != UserRole.INSTALLER:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Config komutlari yalnizca installer tarafindan gonderilebilir",
        )

    # Allowlist: komut slug'i SignalCatalog'da aktif bir binary_output olmali.
    # Ham index kabul edilmez; adres DB'den (dnp3_index) yonetilir.
    signal = db.scalar(
        select(SignalCatalog).where(
            SignalCatalog.key == f"master.{slug}",
            SignalCatalog.data_type == "binary_output",
            SignalCatalog.is_active.is_(True),
        )
    )
    if signal is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Gecersiz veya pasif komut: {slug}",
        )
    index = int(signal.dnp3_index)

    cmd = DeviceCommand(
        gateway_code=gateway.code,
        device_code=device.code,
        command=slug,
        dnp3_index=index,
        count=payload.count,
        on_time_ms=payload.on_time_ms,
        off_time_ms=payload.off_time_ms,
        status="pending",
        actor_username=current_user.username,
    )
    db.add(cmd)
    db.flush()  # id icin
    record_event(
        db,
        category="device",
        event_type="device_command_queued",
        severity="info",
        actor_username=current_user.username,
        device_code=device.code,
        message=f"Komut kuyruga alindi: {signal.label} ({device.code}) #{cmd.id}",
        metadata={"command": slug, "index": index, "command_id": cmd.id},
        i18n_key="device_command_queued",
        i18n_params={"command": signal.label, "code": device.code},
    )
    db.commit()
    return DeviceCommandQueued(
        id=cmd.id, status=cmd.status, command=slug, dnp3_index=index
    )


@router.get("/{device_code}/commands", response_model=list[DeviceCommandRow])
def list_device_commands(
    device_code: str,
    limit: int = Query(default=20, ge=1, le=100),
    current_user: User = Depends(require_roles([UserRole.ENGINEER, UserRole.INSTALLER])),
    db: Session = Depends(get_db),
):
    """Cihazin son komutlari + durumlari (UI takip listesi). En yeni once."""
    device = DeviceRepository(db).get_by_code(device_code)
    if device is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Device not found")
    _ensure_device_visible(db, current_user, device)
    rows = db.scalars(
        select(DeviceCommand)
        .where(DeviceCommand.device_code == device.code)
        .order_by(DeviceCommand.id.desc())
        .limit(limit)
    ).all()
    return list(rows)


@router.get(
    "/{device_code}/history",
    response_model=list[TelemetryHistoryPoint] | list[TelemetryAggregatePoint],
)
def device_signal_history(
    device_code: str,
    signal_key: str = Query(..., description="Sinyal key'i (orn. master.actual_current)"),
    bucket: Literal["raw", "1m", "1h"] = Query(
        "raw", description="raw=ham historian; 1m/1h=continuous aggregate ozet"
    ),
    since: datetime | None = Query(None, description="Bu zamandan sonra (UTC)"),
    until: datetime | None = Query(None, description="Bu zamana kadar (UTC)"),
    limit: int = Query(1000, ge=1, le=10000),
    # Tum roller okuyabilir (salt-okuma grafik). Operator kendi scope'undaki
    # cihazi gorur; _ensure_device_visible IDOR korur.
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Historian zaman serisi — cihaz detay grafikleri icin.

    `telemetry_history` hypertable (bucket=raw) veya continuous aggregate
    view'lari (bucket=1m/1h). Aggregate view'lar sadece TimescaleDB'de var;
    vanilla postgres'te (dev) 1m/1h istegi otomatik ham veriye duser.
    """
    device = db.scalar(select(Device).where(Device.code == device_code))
    if device is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Device not found")
    _ensure_device_visible(db, current_user, device)

    if since is not None and since.tzinfo is None:
        since = since.replace(tzinfo=timezone.utc)
    if until is not None and until.tzinfo is None:
        until = until.replace(tzinfo=timezone.utc)

    if bucket == "raw":
        stmt = select(TelemetryHistory).where(
            TelemetryHistory.device_id == device.id,
            TelemetryHistory.signal_key == signal_key,
        )
        if since is not None:
            stmt = stmt.where(TelemetryHistory.source_timestamp >= since)
        if until is not None:
            stmt = stmt.where(TelemetryHistory.source_timestamp <= until)
        stmt = stmt.order_by(TelemetryHistory.source_timestamp.asc()).limit(limit)
        rows = db.scalars(stmt).all()
        return [
            TelemetryHistoryPoint(
                signal_key=r.signal_key,
                value=r.value,
                value_string=r.value_string,
                quality=r.quality,
                source_timestamp=r.source_timestamp,
            )
            for r in rows
        ]

    # Aggregate: continuous aggregate view'indan oku. View adi bucket'a gore.
    view = "telemetry_history_1m" if bucket == "1m" else "telemetry_history_1h"
    sql = text(
        f"SELECT signal_key, bucket, avg_value, min_value, max_value, sample_count"
        f" FROM {view}"
        " WHERE device_id = :device_id AND signal_key = :signal_key"
        "   AND (:since IS NULL OR bucket >= :since)"
        "   AND (:until IS NULL OR bucket <= :until)"
        " ORDER BY bucket ASC"
        " LIMIT :limit"
    )
    try:
        result = db.execute(
            sql,
            {
                "device_id": device.id,
                "signal_key": signal_key,
                "since": since,
                "until": until,
                "limit": limit,
            },
        )
        agg_rows = result.mappings().all()
    except ProgrammingError:
        # View yok (vanilla postgres / migration uygulanmadi) -> ham veriye dus.
        db.rollback()
        return device_signal_history(
            device_code=device_code,
            signal_key=signal_key,
            bucket="raw",
            since=since,
            until=until,
            limit=limit,
            current_user=current_user,
            db=db,
        )
    return [
        TelemetryAggregatePoint(
            signal_key=m["signal_key"],
            bucket=m["bucket"],
            avg_value=m["avg_value"],
            min_value=m["min_value"],
            max_value=m["max_value"],
            sample_count=m["sample_count"],
        )
        for m in agg_rows
    ]
