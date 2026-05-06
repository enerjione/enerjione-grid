"""Hat Arizalari (Fault) API endpoint'leri.

UI'daki "Hat Arizalari" sayfasi bu uclar uzerinden:
  GET    /faults                   -> liste (status filtresi: open/all)
  GET    /faults/{id}              -> tek ariza detayi
  PATCH  /faults/{id}/assign       -> atanani degistir
  PATCH  /faults/{id}/status       -> status degistir (in_progress, closed)
  PATCH  /faults/{id}/note         -> kisa not guncelle
  GET    /faults/{id}/comments     -> ticket yorumlari
  POST   /faults/{id}/comments     -> yorum/rapor ekle

Yetki:
  - Operator: sadece kendi sorumluluk alanindaki bolge/hatlardaki fault'lari
    gorur (scope_service.get_visible_line_ids).
  - Engineer/Installer: tum fault'lar.
"""

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api.deps import get_current_user, require_roles
from app.db.session import get_db
from app.models.device import Device
from app.models.enums import UserRole
from app.models.fault import FaultComment, FaultEvent
from app.models.grid_topology import Line, Region
from app.models.user import User
from app.schemas.fault import (
    FaultCommentCreate,
    FaultCommentRead,
    FaultEventAssignUpdate,
    FaultEventNoteUpdate,
    FaultEventRead,
    FaultEventStatusUpdate,
)
from app.services.event_service import record_event
from app.services.scope_service import get_visible_line_ids

router = APIRouter(prefix="/faults", tags=["faults"])


def _serialize_fault(db: Session, f: FaultEvent) -> FaultEventRead:
    line = db.get(Line, f.line_id)
    region = db.get(Region, f.region_id)
    last_red = db.get(Device, f.last_red_device_id) if f.last_red_device_id else None
    first_green = db.get(Device, f.first_green_device_id) if f.first_green_device_id else None
    assigned_user = (
        db.scalar(select(User).where(User.username == f.assigned_to_username))
        if f.assigned_to_username
        else None
    )
    comment_count = (
        db.scalar(
            select(func.count()).select_from(FaultComment).where(FaultComment.fault_id == f.id)
        )
        or 0
    )
    return FaultEventRead(
        id=f.id,
        line_id=f.line_id,
        line_name=line.name if line else "",
        region_id=f.region_id,
        region_name=region.name if region else "",
        last_red_device_id=f.last_red_device_id,
        last_red_device_code=last_red.code if last_red else None,
        last_red_device_name=last_red.name if last_red else None,
        first_green_device_id=f.first_green_device_id,
        first_green_device_code=first_green.code if first_green else None,
        first_green_device_name=first_green.name if first_green else None,
        from_pole_id=f.from_pole_id,
        to_pole_id=f.to_pole_id,
        from_pole_seq=f.from_pole_seq,
        to_pole_seq=f.to_pole_seq,
        status=f.status,
        opened_at=f.opened_at,
        resolved_at=f.resolved_at,
        closed_at=f.closed_at,
        note=f.note,
        assigned_to_username=f.assigned_to_username,
        assigned_at=f.assigned_at,
        assigned_to_full_name=assigned_user.full_name if assigned_user else None,
        comment_count=int(comment_count),
    )


@router.get("", response_model=list[FaultEventRead])
def list_faults(
    status_filter: str = Query(default="active", alias="status"),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """status: 'active' (open|assigned|in_progress|resolved) veya 'all'."""
    stmt = select(FaultEvent).order_by(FaultEvent.opened_at.desc())
    if status_filter == "active":
        # closed olmayan tum kayitlar (open/assigned/in_progress/resolved)
        stmt = stmt.where(FaultEvent.status != "closed")
    elif status_filter == "open":
        stmt = stmt.where(FaultEvent.status.in_(["open", "assigned", "in_progress"]))
    elif status_filter == "closed":
        stmt = stmt.where(FaultEvent.status == "closed")
    # "all" -> filtre yok

    line_scope = get_visible_line_ids(db, current_user)
    if line_scope is not None:
        if not line_scope:
            return []
        stmt = stmt.where(FaultEvent.line_id.in_(line_scope))

    rows = list(db.scalars(stmt).all())
    return [_serialize_fault(db, r) for r in rows]


@router.get("/{fault_id}", response_model=FaultEventRead)
def get_fault(
    fault_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    f = db.get(FaultEvent, fault_id)
    if f is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Ariza bulunamadi.")
    line_scope = get_visible_line_ids(db, current_user)
    if line_scope is not None and f.line_id not in line_scope:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Bu arizaya erisim yetkiniz yok.")
    return _serialize_fault(db, f)


@router.patch("/{fault_id}/assign", response_model=FaultEventRead)
def assign_fault(
    fault_id: int,
    payload: FaultEventAssignUpdate,
    current_user: User = Depends(require_roles([UserRole.ENGINEER, UserRole.INSTALLER])),
    db: Session = Depends(get_db),
):
    f = db.get(FaultEvent, fault_id)
    if f is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Ariza bulunamadi.")
    target_username = payload.assigned_to_username or None
    if target_username:
        target = db.scalar(select(User).where(User.username == target_username))
        if target is None:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Atanan kullanici bulunamadi.")
    f.assigned_to_username = target_username
    f.assigned_at = datetime.now(timezone.utc) if target_username else None
    if target_username and f.status == "open":
        f.status = "assigned"
    record_event(
        db,
        category="fault",
        event_type="fault_assigned",
        severity="info",
        actor_username=current_user.username,
        message=f"Ariza atandi: fault {fault_id} -> {target_username or '(boş)'}",
        metadata={"fault_id": fault_id, "assigned_to": target_username},
    )
    db.commit()
    db.refresh(f)
    return _serialize_fault(db, f)


@router.patch("/{fault_id}/status", response_model=FaultEventRead)
def update_fault_status(
    fault_id: int,
    payload: FaultEventStatusUpdate,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    f = db.get(FaultEvent, fault_id)
    if f is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Ariza bulunamadi.")
    # Operator sadece kendine atanmis fault'larin durumunu degistirebilir.
    if current_user.role == UserRole.OPERATOR and f.assigned_to_username != current_user.username:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Bu arizaya yetkiniz yok.")
    new_status = payload.status
    allowed = {"in_progress", "resolved", "closed", "open", "assigned"}
    if new_status not in allowed:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Gecersiz status.")
    f.status = new_status
    if new_status == "resolved" and f.resolved_at is None:
        f.resolved_at = datetime.now(timezone.utc)
    if new_status == "closed":
        f.closed_at = datetime.now(timezone.utc)
        if f.resolved_at is None:
            f.resolved_at = f.closed_at
    record_event(
        db,
        category="fault",
        event_type="fault_status_changed",
        severity="info",
        actor_username=current_user.username,
        message=f"Ariza durumu: fault {fault_id} -> {new_status}",
        metadata={"fault_id": fault_id, "status": new_status},
    )
    db.commit()
    db.refresh(f)
    return _serialize_fault(db, f)


@router.patch("/{fault_id}/note", response_model=FaultEventRead)
def update_fault_note(
    fault_id: int,
    payload: FaultEventNoteUpdate,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    f = db.get(FaultEvent, fault_id)
    if f is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Ariza bulunamadi.")
    if current_user.role == UserRole.OPERATOR and f.assigned_to_username != current_user.username:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Bu arizaya yetkiniz yok.")
    f.note = (payload.note or "").strip() or None
    db.commit()
    db.refresh(f)
    return _serialize_fault(db, f)


@router.get("/{fault_id}/comments", response_model=list[FaultCommentRead])
def list_fault_comments(
    fault_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    f = db.get(FaultEvent, fault_id)
    if f is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Ariza bulunamadi.")
    line_scope = get_visible_line_ids(db, current_user)
    if line_scope is not None and f.line_id not in line_scope:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Erisim yetkiniz yok.")
    rows = list(
        db.scalars(
            select(FaultComment)
            .where(FaultComment.fault_id == fault_id)
            .order_by(FaultComment.created_at.asc())
        ).all()
    )
    return [FaultCommentRead.model_validate(r, from_attributes=True) for r in rows]


@router.post(
    "/{fault_id}/comments",
    response_model=FaultCommentRead,
    status_code=status.HTTP_201_CREATED,
)
def create_fault_comment(
    fault_id: int,
    payload: FaultCommentCreate,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    f = db.get(FaultEvent, fault_id)
    if f is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Ariza bulunamadi.")
    line_scope = get_visible_line_ids(db, current_user)
    if line_scope is not None and f.line_id not in line_scope:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Erisim yetkiniz yok.")
    body = (payload.body or "").strip()
    if not body:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Yorum bos olamaz.")
    comment = FaultComment(
        fault_id=fault_id,
        author_username=current_user.username,
        body=body,
        created_at=datetime.now(timezone.utc),
    )
    db.add(comment)
    record_event(
        db,
        category="fault",
        event_type="fault_comment_added",
        severity="info",
        actor_username=current_user.username,
        message=f"Ariza yorumu eklendi: fault {fault_id}",
        metadata={"fault_id": fault_id},
    )
    db.commit()
    db.refresh(comment)
    return FaultCommentRead.model_validate(comment, from_attributes=True)
