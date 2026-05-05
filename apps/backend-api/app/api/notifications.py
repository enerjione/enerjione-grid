"""Bildirim merkezi API'si — Header'daki zil ikonu icin.

Endpoint'ler kullanicinin kendi bildirimlerini doner; broadcast
(recipient_username IS NULL) bildirimleri herkes gorur.
"""
from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from app.api.deps import get_current_user
from app.db.session import get_db
from app.models.user import User
from app.schemas.notification_inbox import (
    NotificationMarkResult,
    NotificationRead,
    NotificationUnreadCount,
)
from app.services import notification_service

router = APIRouter(prefix="/notifications", tags=["notifications"])


@router.get("", response_model=list[NotificationRead])
def list_notifications(
    only_unread: bool = Query(default=False, description="Sadece okunmamislari getir"),
    limit: int = Query(default=50, ge=1, le=500),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Mevcut kullanicinin bildirimleri (kendi + broadcast). En yeni once."""
    return notification_service.list_notifications(
        db,
        username=current_user.username,
        only_unread=only_unread,
        limit=limit,
    )


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
