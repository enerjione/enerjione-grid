"""IEC 104 server'larinin FastAPI lifespan'da ayaga kaldirilmasi/indirilmesi.

`deploy_all_active_targets` uygulama basladiginda veritabanindan aktif
IEC 104 outbound hedeflerini okur ve her biri icin bir TCP server baslar.
`undeploy_all` kapanista tum server'lari drops eder.

Cihazlar ya da sinyaller UI uzerinden degistikce `redeploy_target` ile tekil
target yeniden kurulur. (Ileride outbound router'i bu fonksiyonu cagirabilir.)
"""

from __future__ import annotations

import asyncio
import logging

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.device import Device
from app.models.outbound_target import OutboundTarget
from app.models.signal_catalog import SignalCatalog
from app.services.iec104.registry import build_point_registry
from app.services.iec104.server import manager

logger = logging.getLogger(__name__)


def _load_active_targets(db: Session) -> list[OutboundTarget]:
    stmt = select(OutboundTarget).where(
        OutboundTarget.is_active.is_(True),
        OutboundTarget.protocol == "iec104",
    )
    return list(db.scalars(stmt).all())


def _load_devices_and_signals(db: Session) -> tuple[list[Device], list[SignalCatalog]]:
    devices = list(db.scalars(select(Device)).all())
    signals = list(db.scalars(select(SignalCatalog)).all())
    return devices, signals


async def deploy_all_active_targets(db: Session, *, loop: asyncio.AbstractEventLoop) -> int:
    manager.bind_loop(loop)
    targets = _load_active_targets(db)
    if not targets:
        return 0
    devices, signals = _load_devices_and_signals(db)
    deployed = 0
    for target in targets:
        try:
            await _deploy_single(target=target, devices=devices, signals=signals)
            deployed += 1
        except Exception:  # noqa: BLE001
            logger.exception("iec104_deploy_failed target_id=%s name=%s", target.id, target.name)
    return deployed


async def _deploy_single(
    *,
    target: OutboundTarget,
    devices: list[Device],
    signals: list[SignalCatalog],
) -> None:
    host = target.listen_host or "0.0.0.0"
    port = target.listen_port or 2404
    default_ca = target.iec104_common_address or 1
    registry = build_point_registry(
        target_id=target.id,
        default_common_address=default_ca,
        devices=devices,
        signals=signals,
    )
    allowed_peers_raw = (target.iec104_allowed_peers or "").strip()
    allowed_peers: tuple[str, ...] = (
        tuple(p.strip() for p in allowed_peers_raw.split(",") if p.strip())
        if allowed_peers_raw
        else ()
    )
    await manager.deploy(
        target_id=target.id,
        name=target.name,
        host=host,
        port=port,
        registry=registry,
        allowed_peers=allowed_peers,
    )
    logger.info(
        "iec104_deployed target_id=%s name=%s host=%s port=%s default_ca=%s points=%d distinct_ca=%d",
        target.id, target.name, host, port, default_ca,
        len(registry.points), len(registry.unique_common_addresses()),
    )


async def undeploy_all() -> None:
    await manager.undeploy_all()


async def redeploy_target(db: Session, target_id: int) -> None:
    await manager.undeploy(target_id)
    target = db.get(OutboundTarget, target_id)
    if target is None or not target.is_active or target.protocol != "iec104":
        return
    devices, signals = _load_devices_and_signals(db)
    await _deploy_single(target=target, devices=devices, signals=signals)
