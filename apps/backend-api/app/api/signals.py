from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import and_, func, select
from sqlalchemy.orm import Session

from app.api.deps import get_current_user, require_role
from app.db.session import get_db
from app.data.device_models import is_valid_model
from app.models.device import Device
from app.models.enums import UserRole
from app.models.signal_catalog import SignalCatalog
from app.models.telemetry import Telemetry
from app.models.user import User
from app.schemas.signal_catalog import (
    SignalCatalogCreate,
    SignalCatalogRead,
    SignalCatalogUpdate,
    SignalLiveValue,
)
from app.services.signal_catalog_seed import load_default_signals, seed_default_signals

router = APIRouter(prefix="/signals", tags=["signals"])


@router.get("", response_model=list[SignalCatalogRead])
def list_signals(
    model: str | None = Query(default=None, description="Cihaz modeli koduna gore filtrele"),
    _: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Tum roller standart sinyal listesini okuyabilir.

    `model` query parametresi gonderilirse sadece o modele ait sinyaller doner.
    """
    stmt = select(SignalCatalog)
    if model:
        if not is_valid_model(model):
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Unknown device model")
        stmt = stmt.where(SignalCatalog.model == model)
    stmt = stmt.order_by(SignalCatalog.display_order.asc(), SignalCatalog.key.asc())
    return list(db.scalars(stmt).all())


@router.post("", response_model=SignalCatalogRead, status_code=status.HTTP_201_CREATED)
def create_signal(
    payload: SignalCatalogCreate,
    _: User = Depends(require_role(UserRole.INSTALLER)),
    db: Session = Depends(get_db),
):
    existing = db.scalar(select(SignalCatalog).where(SignalCatalog.key == payload.key))
    if existing:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Signal key already exists")
    row = SignalCatalog(**payload.model_dump())
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


@router.patch("/{signal_key}", response_model=SignalCatalogRead)
def update_signal(
    signal_key: str,
    payload: SignalCatalogUpdate,
    _: User = Depends(require_role(UserRole.INSTALLER)),
    db: Session = Depends(get_db),
):
    row = db.scalar(select(SignalCatalog).where(SignalCatalog.key == signal_key))
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Signal not found")
    for key, value in payload.model_dump(exclude_none=True).items():
        setattr(row, key, value)
    db.commit()
    db.refresh(row)
    return row


@router.delete("/{signal_key}", status_code=status.HTTP_204_NO_CONTENT)
def delete_signal(
    signal_key: str,
    _: User = Depends(require_role(UserRole.INSTALLER)),
    db: Session = Depends(get_db),
):
    row = db.scalar(select(SignalCatalog).where(SignalCatalog.key == signal_key))
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Signal not found")
    db.delete(row)
    db.commit()
    return None


@router.post("/reset-to-defaults")
def reset_signals_to_defaults(
    _: User = Depends(require_role(UserRole.INSTALLER)),
    db: Session = Depends(get_db),
):
    """Sinyal katalogunu Horstmann SN2 fabrika listesine dondurur.

    - `horstmann_sn2_signals.json` icindeki key'ler disindaki TUM sinyaller silinir.
    - Eksik kayitlar eklenir, mevcut alanlar JSON ile senkronize edilir.
    """
    defaults = load_default_signals()
    if not defaults:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Horstmann SN2 seed dosyasi bulunamadi.",
        )
    stats = seed_default_signals(db, strict=True)
    return {
        "removed": stats.get("removed", 0),
        "inserted": stats.get("inserted", 0),
        "updated": stats.get("updated", 0),
        "total_defaults": len(defaults),
    }


@router.get("/live", response_model=list[SignalLiveValue])
def list_live_values(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Aktif sinyal kataloğu × tüm cihazlar. Telemetri yoksa değer/kalite/zaman boş döner.

    Operator rolü için kapsam (scope) uygulanır: yalnızca kendi sorumluluk
    alanlarındaki cihazların satırları döner."""
    from app.services.scope_service import get_visible_device_ids

    device_rows: list[Device] = list(db.scalars(select(Device).order_by(Device.name.asc())).all())
    visible_ids = get_visible_device_ids(db, current_user)
    if visible_ids is not None:
        device_rows = [d for d in device_rows if d.id in visible_ids]
    catalog_rows: list[SignalCatalog] = list(
        db.scalars(
            select(SignalCatalog)
            .where(SignalCatalog.is_active.is_(True))
            .order_by(SignalCatalog.display_order.asc(), SignalCatalog.key.asc())
        ).all()
    )

    # Cihaz varken katalog hic doldurulmadiysa (ilk kurulum, seed atlanmis vb.) self-heal.
    if device_rows and not catalog_rows:
        defaults = load_default_signals()
        if defaults:
            seed_default_signals(db, strict=False)
            catalog_rows = list(
                db.scalars(
                    select(SignalCatalog)
                    .where(SignalCatalog.is_active.is_(True))
                    .order_by(SignalCatalog.display_order.asc(), SignalCatalog.key.asc())
                ).all()
            )

    if not device_rows or not catalog_rows:
        return []

    subq = (
        select(
            Telemetry.device_id,
            Telemetry.signal_key,
            func.max(Telemetry.source_timestamp).label("mx"),
        )
        .group_by(Telemetry.device_id, Telemetry.signal_key)
        .subquery()
    )
    latest_telemetry_stmt = select(Telemetry).join(
        subq,
        and_(
            Telemetry.device_id == subq.c.device_id,
            Telemetry.signal_key == subq.c.signal_key,
            Telemetry.source_timestamp == subq.c.mx,
        ),
    )
    latest_by_pair: dict[tuple[int, str], Telemetry] = {}
    for row in db.scalars(latest_telemetry_stmt).all():
        latest_by_pair[(row.device_id, row.signal_key)] = row

    # Modele gore on-grupla, ki her cihaz icin sadece kendi modelinin sinyallerini iterate edelim.
    catalog_by_model: dict[str, list[SignalCatalog]] = {}
    for signal in catalog_rows:
        catalog_by_model.setdefault(signal.model, []).append(signal)

    result: list[SignalLiveValue] = []
    for device in device_rows:
        device_signals = catalog_by_model.get(device.model, [])
        for signal in device_signals:
            key = (device.id, signal.key)
            row = latest_by_pair.get(key)
            if row is not None:
                result.append(
                    SignalLiveValue(
                        signal_key=signal.key,
                        signal_label=signal.label,
                        unit=signal.unit,
                        source=signal.source,
                        device_id=device.id,
                        device_code=device.code,
                        device_name=device.name,
                        value=row.value,
                        quality=row.quality,
                        source_timestamp=row.source_timestamp.isoformat(),
                    )
                )
            else:
                result.append(
                    SignalLiveValue(
                        signal_key=signal.key,
                        signal_label=signal.label,
                        unit=signal.unit,
                        source=signal.source,
                        device_id=device.id,
                        device_code=device.code,
                        device_name=device.name,
                        value=None,
                        quality=None,
                        source_timestamp=None,
                    )
                )
    result.sort(key=lambda item: (item.device_code, item.source, item.signal_key))
    return result
