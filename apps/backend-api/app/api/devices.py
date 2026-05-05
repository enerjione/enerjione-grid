from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from app.api.deps import get_current_user, require_roles
from app.db.session import get_db
from app.models.enums import UserRole
from app.models.user import User
from app.repositories.device_repository import DeviceRepository
from app.schemas.device import DeviceCreate, DeviceRead, DeviceUpdate
from app.services.event_service import record_event
from app.services.scope_service import get_visible_device_ids

router = APIRouter(prefix="/devices", tags=["devices"])


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
    if visible_ids is None:
        return rows
    return [d for d in rows if d.id in visible_ids]


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
    )
    db.commit()
    return device


@router.patch("/{device_code}", response_model=DeviceRead)
def update_device(
    device_code: str,
    payload: DeviceUpdate,
    current_user: User = Depends(require_roles([UserRole.ENGINEER, UserRole.INSTALLER])),
    db: Session = Depends(get_db),
):
    repository = DeviceRepository(db)
    device = repository.get_by_code(device_code)
    if device is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Device not found")
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
        message=f"Cihaz güncellendi: {updated.name} ({updated.code})",
        metadata={"device_id": updated.id, "fields": list(changes.keys())},
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
    )
    db.commit()
    return None
