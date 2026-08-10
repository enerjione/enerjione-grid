"""Kullanici-bazli bildirim tercihleri.

GET  /me/notification-preferences  -> mevcut tercihler (yoksa default)
PUT  /me/notification-preferences  -> tercihleri guncelle

Kullanicinin profilinde "Email/SMS/Web bildirimi acik mi?" toggle'lari
icin kullanilir. Sistem cap'inda etkinlestirilmis bir kanal (orn. SMS
gateway tanimli) olsa bile, kullanici kendi tercihinde kapatmissa o
kanaldan bildirim almaz."""

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import get_current_user, require_roles
from app.db.session import get_db
from app.models.enums import UserRole
from app.models.user import User
from app.models.user_notification_preference import UserNotificationPreference
from app.schemas.user_notification_preference import (
    UserNotificationPreferenceRead,
    UserNotificationPreferenceUpdate,
)

router = APIRouter(prefix="/me/notification-preferences", tags=["user-notification-preferences"])
# Ayri admin router — kurulumcu/mühendis kullanicilar baskasinin tercihini
# yonetir. /users/{user_id}/notification-preferences yolu altinda.
admin_router = APIRouter(
    prefix="/users/{user_id}/notification-preferences",
    tags=["user-notification-preferences"],
)


def _get_or_default(
    db: Session, user_id: int, *, persist: bool
) -> UserNotificationPreference:
    """Tercih satirini getirir; yoksa varsayilanlarla (HEPSI ACIK) uretir.

    `persist=False` iken satir tabloya YAZILMAZ. Eskiden GET de yaziyordu:
    kullanici bildirim ekranini sadece acinca sms/telegram/whatsapp=False
    satiri kalici hale geliyor, sonra alarm kuralinda SMS secilse bile
    dispatcher onu sessizce bloklamis oluyordu. Yazma yalnizca kullanicinin
    gercekten kaydettigi PUT'ta olur — yani satirin varligi artik "bu
    kullanici bilincli bir tercih belirtti" demektir.
    """
    row = db.get(UserNotificationPreference, user_id)
    if row is None:
        row = UserNotificationPreference(
            user_id=user_id,
            web_enabled=True,
            email_enabled=True,
            sms_enabled=True,
            telegram_enabled=True,
            whatsapp_web_enabled=True,
            min_level_rank=0,
        )
        if persist:
            db.add(row)
            db.flush()
    return row


@router.get("", response_model=UserNotificationPreferenceRead)
def get_my_preferences(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    pref = _get_or_default(db, current_user.id, persist=False)
    return UserNotificationPreferenceRead.model_validate(pref, from_attributes=True)


@router.put("", response_model=UserNotificationPreferenceRead)
def update_my_preferences(
    payload: UserNotificationPreferenceUpdate,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    pref = _get_or_default(db, current_user.id, persist=True)
    if payload.web_enabled is not None:
        pref.web_enabled = bool(payload.web_enabled)
    if payload.email_enabled is not None:
        pref.email_enabled = bool(payload.email_enabled)
    if payload.sms_enabled is not None:
        pref.sms_enabled = bool(payload.sms_enabled)
    if payload.telegram_enabled is not None:
        pref.telegram_enabled = bool(payload.telegram_enabled)
    if payload.whatsapp_web_enabled is not None:
        pref.whatsapp_web_enabled = bool(payload.whatsapp_web_enabled)
    if payload.min_level_rank is not None:
        pref.min_level_rank = max(0, int(payload.min_level_rank))
    db.commit()
    db.refresh(pref)
    return UserNotificationPreferenceRead.model_validate(pref, from_attributes=True)


# ---------------------------------------------------------------------------
# Admin endpointleri: kurulumcu/mühendis baskasinin tercihlerini yonetir
# (Kullanıcı yonetim panelindeki Email/SMS/Web/Telegram togglelari icin).
# ---------------------------------------------------------------------------


def _ensure_user_exists(db: Session, user_id: int) -> User:
    user = db.scalar(select(User).where(User.id == user_id))
    if user is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")
    return user


@admin_router.get("", response_model=UserNotificationPreferenceRead)
def get_user_preferences(
    user_id: int,
    _: User = Depends(require_roles([UserRole.ENGINEER, UserRole.INSTALLER])),
    db: Session = Depends(get_db),
):
    _ensure_user_exists(db, user_id)
    pref = _get_or_default(db, user_id, persist=False)
    return UserNotificationPreferenceRead.model_validate(pref, from_attributes=True)


@admin_router.put("", response_model=UserNotificationPreferenceRead)
def update_user_preferences(
    user_id: int,
    payload: UserNotificationPreferenceUpdate,
    _: User = Depends(require_roles([UserRole.ENGINEER, UserRole.INSTALLER])),
    db: Session = Depends(get_db),
):
    _ensure_user_exists(db, user_id)
    pref = _get_or_default(db, user_id, persist=True)
    if payload.web_enabled is not None:
        pref.web_enabled = bool(payload.web_enabled)
    if payload.email_enabled is not None:
        pref.email_enabled = bool(payload.email_enabled)
    if payload.sms_enabled is not None:
        pref.sms_enabled = bool(payload.sms_enabled)
    if payload.telegram_enabled is not None:
        pref.telegram_enabled = bool(payload.telegram_enabled)
    if payload.whatsapp_web_enabled is not None:
        pref.whatsapp_web_enabled = bool(payload.whatsapp_web_enabled)
    if payload.min_level_rank is not None:
        pref.min_level_rank = max(0, int(payload.min_level_rank))
    db.commit()
    db.refresh(pref)
    return UserNotificationPreferenceRead.model_validate(pref, from_attributes=True)
