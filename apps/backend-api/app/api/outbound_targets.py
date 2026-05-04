import asyncio
import csv
import io
import logging
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Response, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import require_role, require_roles
from app.db.session import get_db
from app.models.device import Device
from app.models.enums import UserRole
from app.models.outbound_target import OutboundTarget
from app.models.signal_catalog import SignalCatalog
from app.models.user import User
from app.schemas.outbound import OutboundTargetCreate, OutboundTargetRead, OutboundTargetUpdate
from app.services.iec104.bootstrap import redeploy_target
from app.services.iec104.registry import build_point_registry
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


# ----- IEC 104 point list CSV -----

# IEC 104 ASDU Type ID -> kisa kod ve ad. SCADA mühendisleri CSV'de bu adi tanir.
_IEC104_TYPE_LABEL: dict[int, str] = {
    1: "M_SP_NA_1",
    3: "M_DP_NA_1",
    9: "M_ME_NA_1",
    11: "M_ME_NB_1",
    13: "M_ME_NC_1",
    15: "M_IT_NA_1",
    30: "M_SP_TB_1",
    31: "M_DP_TB_1",
    34: "M_ME_TD_1",
    35: "M_ME_TE_1",
    36: "M_ME_TF_1",
    37: "M_IT_TB_1",
}


@router.get("/{target_id}/iec104-points.csv")
def export_iec104_points_csv(
    target_id: int,
    _: User = Depends(
        require_roles([UserRole.ENGINEER, UserRole.INSTALLER])
    ),
    db: Session = Depends(get_db),
) -> Response:
    """Bu IEC 104 hedefine ait tum cihaz x sinyal kombinasyonlarinin point list'ini
    CSV olarak doner.

    Saha mühendisi SCADA tarafindaki IEC 104 master'a noktalari (CA, IOA, Type ID,
    sinyal etiketi) bu CSV'den toplu olarak girebilsin diye duz format:

      device_code, device_name, ca, ioa, type_id, type_code, signal_key, label, unit, scale, offset

    Yalniz `protocol=iec104` hedefler icin gecerli; `is_active=False` cihazlar ve
    `iec104_enabled=False` veya `iec104_type_id` bos sinyaller atlanir (yayinda
    olanla CSV ozdes).
    """
    target = db.get(OutboundTarget, target_id)
    if target is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Outbound target not found")
    if target.protocol != "iec104":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Bu hedef IEC 104 degil; point list yalnizca IEC 104 hedefler icin uretilir.",
        )

    devices = list(db.scalars(select(Device).order_by(Device.code)).all())
    signals = list(
        db.scalars(
            select(SignalCatalog)
            .where(SignalCatalog.is_active.is_(True))
            .order_by(SignalCatalog.dnp3_object_group, SignalCatalog.dnp3_index)
        ).all()
    )

    default_ca = target.iec104_common_address or 1
    registry = build_point_registry(
        target_id=target.id,
        default_common_address=default_ca,
        devices=devices,
        signals=signals,
    )

    # SignalCatalog lookup signal_key -> meta (label, unit, scale, offset).
    sig_meta: dict[str, SignalCatalog] = {s.key: s for s in signals}
    dev_meta: dict[str, Device] = {d.code: d for d in devices}

    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(
        [
            "device_code",
            "device_name",
            "common_address",
            "ioa",
            "type_id",
            "type_code",
            "signal_key",
            "label",
            "unit",
            "scale",
            "offset",
        ]
    )
    # CA, IOA artan sira — SCADA tarafinda goz tarafindan kontrol kolay.
    for p in sorted(registry.points, key=lambda x: (x.common_address, x.ioa)):
        sig = sig_meta.get(p.signal_key)
        dev = dev_meta.get(p.device_code)
        writer.writerow(
            [
                p.device_code,
                dev.name if dev else "",
                p.common_address,
                p.ioa,
                p.type_id,
                _IEC104_TYPE_LABEL.get(p.type_id, ""),
                p.signal_key,
                sig.label if sig else "",
                sig.unit if sig and sig.unit else "",
                sig.scale if sig else "",
                sig.offset if sig else "",
            ]
        )

    csv_text = buf.getvalue()
    # Excel UTF-8 BOM ile dogru gostersin
    body = ("﻿" + csv_text).encode("utf-8")
    ts = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    filename = f"iec104-points-{target.name.replace(' ', '_')}-{ts}.csv"
    return Response(
        content=body,
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )
