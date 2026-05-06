"""Bildirim merkezi API'si — Header'daki zil ikonu icin.

Endpoint'ler kullanicinin kendi bildirimlerini doner; broadcast
(recipient_username IS NULL) bildirimleri herkes gorur.
"""
import json
import logging

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import get_current_user
from app.db.session import get_db
from app.models.device import Device
from app.models.user import User
from app.schemas.notification_inbox import (
    NotificationMarkResult,
    NotificationRead,
    NotificationUnreadCount,
)
from app.services import notification_service

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/notifications", tags=["notifications"])


def _enrich_metadata(db: Session, raw: str | None) -> str | None:
    """metadata_json icindeki eksik alanlari (device_name, signal_source,
    line_name, region_name) DB'den anlik tamamla.

    Eski bildirimler bu alanlar olmadan yaratilmis olabilir. Frontend her
    /notifications cagrisinda anlik enrich edilmis veriyi alir; eski/yeni
    ayrimi gorunmez. Yeni bildirimler zaten dolu olarak DB'de durur, fonksiyon
    no-op davranir."""
    if not raw:
        return raw
    try:
        meta = json.loads(raw)
        if not isinstance(meta, dict):
            return raw
    except Exception:  # noqa: BLE001
        return raw

    changed = False
    # device_name eksikse Device tablosundan al.
    if not meta.get("device_name"):
        dev_code = meta.get("device_code")
        if dev_code:
            dev = db.scalar(select(Device).where(Device.code == dev_code))
            if dev is not None:
                meta["device_name"] = dev.name
                changed = True

    # signal_source eksikse signal_key prefix'inden turet.
    if not meta.get("signal_source"):
        sk = meta.get("signal_key")
        if sk and isinstance(sk, str) and "." in sk:
            meta["signal_source"] = sk.split(".", 1)[0].lower()
            changed = True

    # Hat / bolge: cihaz LineSegment'e atanmissa cek.
    if not meta.get("line_name") or not meta.get("region_name"):
        dev_code = meta.get("device_code")
        if dev_code:
            try:
                from app.models.grid_topology import Line, LineSegment, Region
                dev = db.scalar(select(Device).where(Device.code == dev_code))
                if dev is not None:
                    seg = db.scalar(
                        select(LineSegment).where(LineSegment.device_id == dev.id).limit(1)
                    )
                    if seg is not None:
                        line_row = db.get(Line, seg.line_id)
                        if line_row is not None:
                            if not meta.get("line_name"):
                                meta["line_name"] = line_row.name
                                changed = True
                            if not meta.get("line_code"):
                                meta["line_code"] = line_row.code
                                changed = True
                            if not meta.get("region_name"):
                                region_row = db.get(Region, line_row.region_id)
                                if region_row is not None:
                                    meta["region_name"] = region_row.name
                                    changed = True
            except Exception:  # noqa: BLE001
                logger.debug("notification_enrich_topology_failed", exc_info=True)

    if not changed:
        return raw
    try:
        return json.dumps(meta, ensure_ascii=False)
    except Exception:  # noqa: BLE001
        return raw


@router.get("", response_model=list[NotificationRead])
def list_notifications(
    only_unread: bool = Query(default=False, description="Sadece okunmamislari getir"),
    limit: int = Query(default=50, ge=1, le=500),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Mevcut kullanicinin bildirimleri (kendi + broadcast). En yeni once.

    metadata_json eski bildirimlerde eksik gelmis olabilir; bu endpoint
    yanitinda DB'den anlik enrich edilir (cihaz adi, kaynak, hat, bolge).
    """
    rows = notification_service.list_notifications(
        db,
        username=current_user.username,
        only_unread=only_unread,
        limit=limit,
    )
    # In-memory enrichment — DB row'larini guncellemiyoruz, sadece yanit
    # icin metadata'yi tamamliyoruz. Pydantic from_attributes ile cevirmek
    # yerine elle dict olusturuyoruz cunku metadata_json'i degistirmek
    # istiyoruz.
    out: list[NotificationRead] = []
    for n in rows:
        out.append(
            NotificationRead(
                id=n.id,
                recipient_username=n.recipient_username,
                category=n.category,
                severity=n.severity,
                title=n.title,
                body=n.body,
                actor_username=n.actor_username,
                link=n.link,
                metadata_json=_enrich_metadata(db, n.metadata_json),
                is_read=n.is_read,
                read_at=n.read_at,
                created_at=n.created_at,
            )
        )
    return out


@router.get("/unread-count", response_model=NotificationUnreadCount)
def unread_count(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Header zil rozeti icin sayim. Polling 30sn'de bir cagrilir."""
    return NotificationUnreadCount(unread=notification_service.count_unread(db, current_user.username))


@router.post("/{notification_id}/read", response_model=NotificationMarkResult)
def mark_read(
    notification_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Tek bir bildirimi okundu isaretle."""
    ok = notification_service.mark_as_read(
        db, username=current_user.username, notification_id=notification_id
    )
    if not ok:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Bildirim bulunamadi.")
    db.commit()
    return NotificationMarkResult(ok=True, affected=1)


@router.post("/read-all", response_model=NotificationMarkResult)
def mark_all_read(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Mevcut kullanicinin tum okunmamis bildirimlerini okundu isaretle."""
    affected = notification_service.mark_all_as_read(db, current_user.username)
    db.commit()
    return NotificationMarkResult(ok=True, affected=affected)
