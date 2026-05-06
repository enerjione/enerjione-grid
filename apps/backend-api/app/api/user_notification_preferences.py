"""Kullanici-bazli bildirim tercihleri.

GET  /me/notification-preferences  -> mevcut tercihler (yoksa default)
PUT  /me/notification-preferences  -> tercihleri guncelle

Kullanicinin profilinde "Email/SMS/Web bildirimi acik mi?" toggle'lari
icin kullanilir. Sistem cap'inda etkinlestirilmis bir kanal (orn. SMS
gateway tanimli) olsa bile, kullanici kendi tercihinde kapatmissa o
kanaldan bildirim almaz."""

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.api.deps import get_current_user
from app.db.session import get_db
from app.models.user import User
from app.models.user_notification_preference import UserNotificationPreference
from app.schemas.user_notification_preference import (
    UserNotificationPreferenceRead,
    UserNotificationPreferenceUpdate,
)

router = APIRouter(prefix="/me/notification-preferences", tags=["user-notification-preferences"])


def _get_or_default(db: Session, user_id: int) -> UserNotificationPreference:
    row = db.get(UserNotificationPreference, user_id)
    if row is None:
        row = UserNotificationPreference(
            user_id=user_id,
            web_enabled=True,
            email_enabled=True,
            sms_enabled=False,
            min_level_rank=0,
        )
        db.add(row)
        db.flush()
    return row


@router.get("", response_model=UserNotificationPreferenceRead)
def get_my_preferences(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    pref = _get_or_default(db, current_user.id)
    return UserNotificationPreferenceRead.model_validate(pref, from_attributes=True)


@router.put("", response_model=UserNotificationPreferenceRead)
def update_my_preferences(
    payload: UserNotificationPreferenceUpdate,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    pref = _get_or_default(db, current_user.id)
    if payload.web_enabled is not None:
        pref.web_enabled = bool(payload.web_enabled)
    if payload.email_enabled is not None:
        pref.email_enabled = bool(payload.email_enabled)
    if payload.sms_enabled is not None:
        pref.sms_enabled = bool(payload.sms_enabled)
    if payload.min_level_rank is not None:
        pref.min_level_rank = max(0, int(payload.min_level_rank))
    db.commit()
    db.refresh(pref)
    return UserNotificationPreferenceRead.model_validate(pref, from_attributes=True)
