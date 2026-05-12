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
from app.models.grid_topology import Line, Region
from app.models.responsibility_area import (
    ResponsibilityArea,
    responsibility_area_devices,
    responsibility_area_lines,
    responsibility_area_regions,
    responsibility_area_users,
)
from app.models.user import User
from app.schemas.responsibility_area import (
    ResponsibilityAreaCreate,
    ResponsibilityAreaDetail,
    ResponsibilityAreaDeviceRead,
    ResponsibilityAreaLineRead,
    ResponsibilityAreaRead,
    ResponsibilityAreaRegionRead,
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
    region_count = db.scalar(
        select(func.count())
        .select_from(responsibility_area_regions)
        .where(responsibility_area_regions.c.area_id == area.id)
    ) or 0
    line_count = db.scalar(
        select(func.count())
        .select_from(responsibility_area_lines)
        .where(responsibility_area_lines.c.area_id == area.id)
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
        region_count=int(region_count),
        line_count=int(line_count),
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
        message=f"Team created: {row.name} ({row.code})",
        metadata={"area_id": row.id},
        i18n_key="area_created",
        i18n_params={"name": row.name, "code": row.code},
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
    region_ids = [
        row[0]
        for row in db.execute(
            select(responsibility_area_regions.c.region_id).where(responsibility_area_regions.c.area_id == area_id)
        ).all()
    ]
    line_ids = [
        row[0]
        for row in db.execute(
            select(responsibility_area_lines.c.line_id).where(responsibility_area_lines.c.area_id == area_id)
        ).all()
    ]
    users = (
        list(db.scalars(select(User).where(User.id.in_(user_ids))).all()) if user_ids else []
    )
    devices = (
        list(db.scalars(select(Device).where(Device.id.in_(device_ids))).all()) if device_ids else []
    )
    regions = (
        list(db.scalars(select(Region).where(Region.id.in_(region_ids))).all()) if region_ids else []
    )
    lines = (
        list(db.scalars(select(Line).where(Line.id.in_(line_ids))).all()) if line_ids else []
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
        region_count=base.region_count,
        line_count=base.line_count,
        users=[
            ResponsibilityAreaUserRead(
                id=u.id, username=u.username, full_name=u.full_name, email=u.email
            )
            for u in users
        ],
        devices=[
            ResponsibilityAreaDeviceRead(id=d.id, code=d.code, name=d.name) for d in devices
        ],
        regions=[
            ResponsibilityAreaRegionRead(id=r.id, code=r.code, name=r.name) for r in regions
        ],
        lines=[
            ResponsibilityAreaLineRead(id=l.id, code=l.code, name=l.name, region_id=l.region_id)
            for l in lines
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
        message=f"Team updated: {area.name}",
        metadata={"area_id": area.id, "fields": list(changes.keys())},
        i18n_key="area_updated",
        i18n_params={"name": area.name},
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
        message=f"Team deleted: {name} ({code})",
        metadata={"area_id": area_id},
        i18n_key="area_deleted",
        i18n_params={"name": name, "code": code},
    )
    db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# ---- Uye yonetimi: kullanicilar ----

@router.post("/{area_id}/users/{user_id}", status_code=status.HTTP_204_NO_CONTENT)
def add_user_to_area(
    area_id: int,
    user_id: int,
    current_user: User = Depends(require_roles([UserRole.ENGINEER, UserRole.INSTALLER])),
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
        record_event(
            db,
            category="responsibility_area",
            event_type="area_user_added",
            severity="info",
            actor_username=current_user.username,
            message=f"User {user.username} added to team '{area.name}'",
            metadata={"area_id": area_id, "area_name": area.name, "user_id": user_id, "username": user.username},
            i18n_key="area_user_added",
            i18n_params={"user": user.username, "area": area.name},
        )
        db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.delete("/{area_id}/users/{user_id}", status_code=status.HTTP_204_NO_CONTENT)
def remove_user_from_area(
    area_id: int,
    user_id: int,
    current_user: User = Depends(require_roles([UserRole.ENGINEER, UserRole.INSTALLER])),
    db: Session = Depends(get_db),
):
    area = db.get(ResponsibilityArea, area_id)
    user = db.get(User, user_id)
    db.execute(
        sqlalchemy_delete(responsibility_area_users).where(
            responsibility_area_users.c.area_id == area_id,
            responsibility_area_users.c.user_id == user_id,
        )
    )
    if area is not None and user is not None:
        record_event(
            db,
            category="responsibility_area",
            event_type="area_user_removed",
            severity="info",
            actor_username=current_user.username,
            message=f"User {user.username} removed from team '{area.name}'",
            metadata={"area_id": area_id, "area_name": area.name, "user_id": user_id, "username": user.username},
            i18n_key="area_user_removed",
            i18n_params={"user": user.username, "area": area.name},
        )
    db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# ---- Uye yonetimi: cihazlar ----

@router.post("/{area_id}/devices/{device_id}", status_code=status.HTTP_204_NO_CONTENT)
def add_device_to_area(
    area_id: int,
    device_id: int,
    current_user: User = Depends(require_roles([UserRole.ENGINEER, UserRole.INSTALLER])),
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
        record_event(
            db,
            category="responsibility_area",
            event_type="area_device_added",
            severity="info",
            actor_username=current_user.username,
            device_code=device.code,
            message=f"Device {device.name} assigned to team '{area.name}'",
            metadata={"area_id": area_id, "area_name": area.name, "device_id": device_id, "device_code": device.code},
            i18n_key="area_device_added",
            i18n_params={"device": device.name, "area": area.name},
        )
        db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.delete("/{area_id}/devices/{device_id}", status_code=status.HTTP_204_NO_CONTENT)
def remove_device_from_area(
    area_id: int,
    device_id: int,
    current_user: User = Depends(require_roles([UserRole.ENGINEER, UserRole.INSTALLER])),
    db: Session = Depends(get_db),
):
    area = db.get(ResponsibilityArea, area_id)
    device = db.get(Device, device_id)
    db.execute(
        sqlalchemy_delete(responsibility_area_devices).where(
            responsibility_area_devices.c.area_id == area_id,
            responsibility_area_devices.c.device_id == device_id,
        )
    )
    if area is not None and device is not None:
        record_event(
            db,
            category="responsibility_area",
            event_type="area_device_removed",
            severity="info",
            actor_username=current_user.username,
            device_code=device.code,
            message=f"Device {device.name} unassigned from team '{area.name}'",
            metadata={"area_id": area_id, "area_name": area.name, "device_id": device_id, "device_code": device.code},
            i18n_key="area_device_removed",
            i18n_params={"device": device.name, "area": area.name},
        )
    db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# ============= Region (Bolge) atamasi =============

@router.post("/{area_id}/regions/{region_id}", status_code=status.HTTP_204_NO_CONTENT)
def add_region_to_area(
    area_id: int,
    region_id: int,
    current_user: User = Depends(require_roles([UserRole.ENGINEER, UserRole.INSTALLER])),
    db: Session = Depends(get_db),
):
    area = db.get(ResponsibilityArea, area_id)
    region = db.get(Region, region_id)
    if area is None or region is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Alan veya bölge bulunamadı.")
    exists = db.execute(
        select(responsibility_area_regions).where(
            responsibility_area_regions.c.area_id == area_id,
            responsibility_area_regions.c.region_id == region_id,
        )
    ).first()
    if not exists:
        db.execute(responsibility_area_regions.insert().values(area_id=area_id, region_id=region_id))
        # Bolge eklendiginde, o bolgeye bagli tum hatlari da otomatik olarak
        # ekibin kapsamina ekle. Kullanici daha sonra istemedigi hatti tek tek
        # kaldirabilir (Hatlar tabindan).
        existing_line_ids = {
            row[0]
            for row in db.execute(
                select(responsibility_area_lines.c.line_id).where(
                    responsibility_area_lines.c.area_id == area_id
                )
            ).all()
        }
        region_lines = list(
            db.scalars(select(Line).where(Line.region_id == region_id)).all()
        )
        added_line_count = 0
        for ln in region_lines:
            if ln.id in existing_line_ids:
                continue
            db.execute(
                responsibility_area_lines.insert().values(area_id=area_id, line_id=ln.id)
            )
            added_line_count += 1
        record_event(
            db,
            category="responsibility_area",
            event_type="area_region_added",
            severity="info",
            actor_username=current_user.username,
            message=f"Region '{region.name}' assigned to team '{area.name}'",
            metadata={
                "area_id": area_id,
                "area_name": area.name,
                "region_id": region_id,
                "region_name": region.name,
                "auto_added_lines": added_line_count,
            },
            i18n_key="area_region_added",
            i18n_params={"region": region.name, "area": area.name},
        )
        db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.delete("/{area_id}/regions/{region_id}", status_code=status.HTTP_204_NO_CONTENT)
def remove_region_from_area(
    area_id: int,
    region_id: int,
    current_user: User = Depends(require_roles([UserRole.ENGINEER, UserRole.INSTALLER])),
    db: Session = Depends(get_db),
):
    area = db.get(ResponsibilityArea, area_id)
    region = db.get(Region, region_id)
    db.execute(
        sqlalchemy_delete(responsibility_area_regions).where(
            responsibility_area_regions.c.area_id == area_id,
            responsibility_area_regions.c.region_id == region_id,
        )
    )
    if area is not None and region is not None:
        record_event(
            db,
            category="responsibility_area",
            event_type="area_region_removed",
            severity="info",
            actor_username=current_user.username,
            message=f"Region '{region.name}' unassigned from team '{area.name}'",
            metadata={"area_id": area_id, "area_name": area.name, "region_id": region_id, "region_name": region.name},
            i18n_key="area_region_removed",
            i18n_params={"region": region.name, "area": area.name},
        )
    db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# ============= Line (Hat) atamasi =============

@router.post("/{area_id}/lines/{line_id}", status_code=status.HTTP_204_NO_CONTENT)
def add_line_to_area(
    area_id: int,
    line_id: int,
    current_user: User = Depends(require_roles([UserRole.ENGINEER, UserRole.INSTALLER])),
    db: Session = Depends(get_db),
):
    area = db.get(ResponsibilityArea, area_id)
    line = db.get(Line, line_id)
    if area is None or line is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Alan veya hat bulunamadı.")
    exists = db.execute(
        select(responsibility_area_lines).where(
            responsibility_area_lines.c.area_id == area_id,
            responsibility_area_lines.c.line_id == line_id,
        )
    ).first()
    if not exists:
        db.execute(responsibility_area_lines.insert().values(area_id=area_id, line_id=line_id))
        record_event(
            db,
            category="responsibility_area",
            event_type="area_line_added",
            severity="info",
            actor_username=current_user.username,
            message=f"Line '{line.name}' assigned to team '{area.name}'",
            metadata={"area_id": area_id, "area_name": area.name, "line_id": line_id, "line_name": line.name},
            i18n_key="area_line_added",
            i18n_params={"line": line.name, "area": area.name},
        )
        db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.delete("/{area_id}/lines/{line_id}", status_code=status.HTTP_204_NO_CONTENT)
def remove_line_from_area(
    area_id: int,
    line_id: int,
    current_user: User = Depends(require_roles([UserRole.ENGINEER, UserRole.INSTALLER])),
    db: Session = Depends(get_db),
):
    area = db.get(ResponsibilityArea, area_id)
    line = db.get(Line, line_id)
    db.execute(
        sqlalchemy_delete(responsibility_area_lines).where(
            responsibility_area_lines.c.area_id == area_id,
            responsibility_area_lines.c.line_id == line_id,
        )
    )
    if area is not None and line is not None:
        record_event(
            db,
            category="responsibility_area",
            event_type="area_line_removed",
            severity="info",
            actor_username=current_user.username,
            message=f"Line '{line.name}' unassigned from team '{area.name}'",
            metadata={"area_id": area_id, "area_name": area.name, "line_id": line_id, "line_name": line.name},
            i18n_key="area_line_removed",
            i18n_params={"line": line.name, "area": area.name},
        )
    db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)
