"""Sorumluluk alani CRUD ve uye yonetimi endpoint'leri.

Yetki:
  - INSTALLER ve ENGINEER: tum operasyonlar (olustur/sil/duzenle/uye ekle).
  - OPERATOR: sadece okur (filtreleme icin liste).

UI tarafindaki "sola/saga tasi" pattern'i:
  - sol panel: tum kullanicilar/cihazlar
  - sag panel: bu alanin uyeleri
  - tek tikla `POST .../users/{id}` veya `DELETE .../users/{id}`
  - cihazlar icin de ayni: `POST .../devices/{id}`, `DELETE .../devices/{id}`
"""

from fastapi import APIRouter, Depends, HTTPException, Response, status
from sqlalchemy import delete as sqlalchemy_delete, select
from sqlalchemy.orm import Session

from app.api.deps import get_current_user, require_roles
from app.db.session import get_db
from app.models.device import Device
from app.models.enums import UserRole
from app.models.responsibility_area import (
    ResponsibilityArea,
    responsibility_area_devices,
    responsibility_area_users,
)
from app.models.user import User
from app.schemas.responsibility_area import (
    ResponsibilityAreaCreate,
    ResponsibilityAreaDetail,
    ResponsibilityAreaDeviceRead,
    ResponsibilityAreaRead,
    ResponsibilityAreaUpdate,
    ResponsibilityAreaUserRead,
)
from app.services.event_service import record_event

router = APIRouter(prefix="/responsibility-areas", tags=["responsibility-areas"])


def _can_edit(role: str) -> bool:
    return role in (UserRole.INSTALLER.value, UserRole.ENGINEER.value)


def _require_edit(current_user: User) -> None:
    if not _can_edit(current_user.role.value if hasattr(current_user.role, "value") else str(current_user.role)):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Sorumluluk alanini duzenlemek icin muhendis veya kurulumcu yetkisi gerekir.",
        )


def _build_read(db: Session, area: ResponsibilityArea) -> ResponsibilityAreaRead:
    from sqlalchemy import func

    user_count = db.scalar(
        select(func.count())
        .select_from(responsibility_area_users)
        .where(responsibility_area_users.c.area_id == area.id)
    ) or 0
    device_count = db.scalar(
        select(func.count())
        .select_from(responsibility_area_devices)
        .where(responsibility_area_devices.c.area_id == area.id)
    ) or 0
    return ResponsibilityAreaRead(
        id=area.id,
        code=area.code,
        name=area.name,
        description=area.description,
        is_active=area.is_active,
        created_at=area.created_at,
        user_count=int(user_count),
        device_count=int(device_count),
    )


@router.get("", response_model=list[ResponsibilityAreaRead])
def list_areas(
    _: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    rows = list(db.scalars(select(ResponsibilityArea).order_by(ResponsibilityArea.name.asc())).all())
    return [_build_read(db, row) for row in rows]


@router.post("", response_model=ResponsibilityAreaRead, status_code=status.HTTP_201_CREATED)
def create_area(
    payload: ResponsibilityAreaCreate,
    current_user: User = Depends(require_roles([UserRole.ENGINEER, UserRole.INSTALLER])),
    db: Session = Depends(get_db),
):
    existing = db.scalar(select(ResponsibilityArea).where(ResponsibilityArea.code == payload.code))
    if existing:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Sorumluluk alani kodu zaten kullaniliyor.")
    row = ResponsibilityArea(**payload.model_dump())
    db.add(row)
    db.flush()
    record_event(
        db,
        category="responsibility-area",
        event_type="area_created",
        severity="info",
        actor_username=current_user.username,
        message=f"Sorumluluk alanı eklendi: {row.name} ({row.code})",
        metadata={"area_id": row.id},
    )
    db.commit()
    db.refresh(row)
    return _build_read(db, row)


@router.get("/{area_id}", response_model=ResponsibilityAreaDetail)
def get_area(
    area_id: int,
    _: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    area = db.get(ResponsibilityArea, area_id)
    if area is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Sorumluluk alani bulunamadi.")
    user_ids = [
        row[0]
        for row in db.execute(
            select(responsibility_area_users.c.user_id).where(responsibility_area_users.c.area_id == area_id)
        ).all()
    ]
    device_ids = [
        row[0]
        for row in db.execute(
            select(responsibility_area_devices.c.device_id).where(responsibility_area_devices.c.area_id == area_id)
        ).all()
    ]
    users = (
        list(db.scalars(select(User).where(User.id.in_(user_ids))).all()) if user_ids else []
    )
    devices = (
        list(db.scalars(select(Device).where(Device.id.in_(device_ids))).all()) if device_ids else []
    )
    base = _build_read(db, area)
    return ResponsibilityAreaDetail(
        id=base.id,
        code=base.code,
        name=base.name,
        description=base.description,
        is_active=base.is_active,
        created_at=base.created_at,
        user_count=base.user_count,
        device_count=base.device_count,
        users=[
            ResponsibilityAreaUserRead(
                id=u.id, username=u.username, full_name=u.full_name, email=u.email
            )
            for u in users
        ],
        devices=[
            ResponsibilityAreaDeviceRead(id=d.id, code=d.code, name=d.name) for d in devices
        ],
    )


@router.patch("/{area_id}", response_model=ResponsibilityAreaRead)
def update_area(
    area_id: int,
    payload: ResponsibilityAreaUpdate,
    current_user: User = Depends(require_roles([UserRole.ENGINEER, UserRole.INSTALLER])),
    db: Session = Depends(get_db),
):
    area = db.get(ResponsibilityArea, area_id)
    if area is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Sorumluluk alani bulunamadi.")
    changes = payload.model_dump(exclude_none=True)
    for field, value in changes.items():
        setattr(area, field, value)
    record_event(
        db,
        category="responsibility-area",
        event_type="area_updated",
        severity="info",
        actor_username=current_user.username,
        message=f"Sorumluluk alanı güncellendi: {area.name}",
        metadata={"area_id": area.id, "fields": list(changes.keys())},
    )
    db.commit()
    db.refresh(area)
    return _build_read(db, area)


@router.delete("/{area_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_area(
    area_id: int,
    current_user: User = Depends(require_roles([UserRole.ENGINEER, UserRole.INSTALLER])),
    db: Session = Depends(get_db),
):
    area = db.get(ResponsibilityArea, area_id)
    if area is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Sorumluluk alani bulunamadi.")
    name = area.name
    code = area.code
    # Junction tablolari ON DELETE CASCADE ile temizleniyor.
    db.delete(area)
    record_event(
        db,
        category="responsibility-area",
        event_type="area_deleted",
        severity="warning",
        actor_username=current_user.username,
        message=f"Sorumluluk alanı silindi: {name} ({code})",
        metadata={"area_id": area_id},
    )
    db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# ---- Uye yonetimi: kullanicilar ----

@router.post("/{area_id}/users/{user_id}", status_code=status.HTTP_204_NO_CONTENT)
def add_user_to_area(
    area_id: int,
    user_id: int,
    _: User = Depends(require_roles([UserRole.ENGINEER, UserRole.INSTALLER])),
    db: Session = Depends(get_db),
):
    area = db.get(ResponsibilityArea, area_id)
    user = db.get(User, user_id)
    if area is None or user is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Alan veya kullanici bulunamadi.")
    # Sorumluluk alanlarına yalnızca operator rolündeki kullanıcılar atanabilir.
    # Mühendis ve kurulumcunun zaten tüm cihazlara erişimi olduğundan kapsam
    # tanımı anlam taşımaz; UI tarafında da bu kullanıcılar listede görünmez.
    if user.role != UserRole.OPERATOR:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Sorumluluk alanlarına yalnızca operatör hesapları atanabilir.",
        )
    exists = db.execute(
        select(responsibility_area_users).where(
            responsibility_area_users.c.area_id == area_id,
            responsibility_area_users.c.user_id == user_id,
        )
    ).first()
    if not exists:
        db.execute(responsibility_area_users.insert().values(area_id=area_id, user_id=user_id))
        db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.delete("/{area_id}/users/{user_id}", status_code=status.HTTP_204_NO_CONTENT)
def remove_user_from_area(
    area_id: int,
    user_id: int,
    _: User = Depends(require_roles([UserRole.ENGINEER, UserRole.INSTALLER])),
    db: Session = Depends(get_db),
):
    db.execute(
        sqlalchemy_delete(responsibility_area_users).where(
            responsibility_area_users.c.area_id == area_id,
            responsibility_area_users.c.user_id == user_id,
        )
    )
    db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# ---- Uye yonetimi: cihazlar ----

@router.post("/{area_id}/devices/{device_id}", status_code=status.HTTP_204_NO_CONTENT)
def add_device_to_area(
    area_id: int,
    device_id: int,
    _: User = Depends(require_roles([UserRole.ENGINEER, UserRole.INSTALLER])),
    db: Session = Depends(get_db),
):
    area = db.get(ResponsibilityArea, area_id)
    device = db.get(Device, device_id)
    if area is None or device is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Alan veya cihaz bulunamadi.")
    exists = db.execute(
        select(responsibility_area_devices).where(
            responsibility_area_devices.c.area_id == area_id,
            responsibility_area_devices.c.device_id == device_id,
        )
    ).first()
    if not exists:
        db.execute(responsibility_area_devices.insert().values(area_id=area_id, device_id=device_id))
        db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.delete("/{area_id}/devices/{device_id}", status_code=status.HTTP_204_NO_CONTENT)
def remove_device_from_area(
    area_id: int,
    device_id: int,
    _: User = Depends(require_roles([UserRole.ENGINEER, UserRole.INSTALLER])),
    db: Session = Depends(get_db),
):
    db.execute(
        sqlalchemy_delete(responsibility_area_devices).where(
            responsibility_area_devices.c.area_id == area_id,
            responsibility_area_devices.c.device_id == device_id,
        )
    )
    db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)
