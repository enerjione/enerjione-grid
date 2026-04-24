import asyncio
import logging

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import require_role
from app.db.session import get_db
from app.models.enums import UserRole
from app.models.outbound_target import OutboundTarget
from app.models.user import User
from app.schemas.outbound import OutboundTargetCreate, OutboundTargetRead, OutboundTargetUpdate
from app.services.iec104.bootstrap import redeploy_target
from app.services.iec104.server import manager as iec104_manager

router = APIRouter(prefix="/outbound-targets", tags=["outbound-targets"])
logger = logging.getLogger(__name__)


def _schedule_iec104_redeploy(db: Session, target_id: int) -> None:
    """Event loop'a threadsafe redeploy gorevini birakir.

    CRUD sync bir Session ile calisir ama IEC 104 server'i asyncio loop
    ustunde. `manager._loop` startup'ta bind edilmis olmali. Uygulama hic
    lifespan'a girmediyse sessizce geciyoruz (dev modunda HTTP client
    startup event'inden once istek atmadigi surece problem yok).
    """
    loop = iec104_manager._loop  # noqa: SLF001
    if loop is None:
        return

    async def _run() -> None:
        # Redeploy sirasinda yeni bir Session kullan, cunku gelen db thread-local.
        from app.db.session import SessionLocal

        scoped = SessionLocal()
        try:
            await redeploy_target(scoped, target_id)
        finally:
            scoped.close()

    try:
        asyncio.run_coroutine_threadsafe(_run(), loop)
    except RuntimeError:
        logger.debug("iec104_redeploy_schedule_failed target_id=%s", target_id)


@router.get("", response_model=list[OutboundTargetRead])
def list_outbound_targets(
    _: User = Depends(require_role(UserRole.INSTALLER)),
    db: Session = Depends(get_db),
):
    stmt = select(OutboundTarget).order_by(OutboundTarget.name.asc())
    return list(db.scalars(stmt).all())


@router.post("", response_model=OutboundTargetRead, status_code=status.HTTP_201_CREATED)
def create_outbound_target(
    payload: OutboundTargetCreate,
    _: User = Depends(require_role(UserRole.INSTALLER)),
    db: Session = Depends(get_db),
):
    existing = db.scalar(select(OutboundTarget).where(OutboundTarget.name == payload.name))
    if existing:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Outbound target already exists")
    row = OutboundTarget(**payload.model_dump())
    db.add(row)
    db.commit()
    db.refresh(row)
    if row.protocol == "iec104":
        _schedule_iec104_redeploy(db, row.id)
    return row


@router.patch("/{target_id}", response_model=OutboundTargetRead)
def update_outbound_target(
    target_id: int,
    payload: OutboundTargetUpdate,
    _: User = Depends(require_role(UserRole.INSTALLER)),
    db: Session = Depends(get_db),
):
    row = db.get(OutboundTarget, target_id)
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Outbound target not found")
    updates = payload.model_dump(exclude_unset=True)
    for key, value in updates.items():
        setattr(row, key, value)
    db.commit()
    db.refresh(row)
    if row.protocol == "iec104":
        _schedule_iec104_redeploy(db, row.id)
    return row


@router.delete("/{target_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_outbound_target(
    target_id: int,
    _: User = Depends(require_role(UserRole.INSTALLER)),
    db: Session = Depends(get_db),
):
    row = db.get(OutboundTarget, target_id)
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Outbound target not found")
    was_iec104 = row.protocol == "iec104"
    saved_id = row.id
    db.delete(row)
    db.commit()
    if was_iec104:
        # undeploy ayni IEC 104 manager uzerinde calisir; schedule threadsafe.
        loop = iec104_manager._loop  # noqa: SLF001
        if loop is not None:
            asyncio.run_coroutine_threadsafe(iec104_manager.undeploy(saved_id), loop)
    return None
