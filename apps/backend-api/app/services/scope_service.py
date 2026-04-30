"""Kullanici kapsami (scope) - operator rolu sadece kendi sorumluluk alanlarindaki
cihazlari/alarm/sinyalleri gorur. Engineer ve Installer kisitsiz erisim.

API endpoint'leri bu helper'i cagirip ek `WHERE device_id IN (...)` clausesi
uygular. Kapsam None doner ise filtre uygulanmaz (kisit yok)."""

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.enums import UserRole
from app.models.responsibility_area import (
    responsibility_area_devices,
    responsibility_area_users,
)
from app.models.user import User


def get_visible_device_ids(db: Session, user: User) -> set[int] | None:
    """Kullanicinin gorebilecegi cihaz id setini doner.

    - INSTALLER ve ENGINEER: None doner -> kisit yok, tum cihazlar.
    - OPERATOR: kendi sorumluluk alanlarindaki cihazlarin id'leri (set).
      Hicbir alana atanmamis operatorler bos set goryp hicbir cihazi gormez.
    """
    if user.role in (UserRole.INSTALLER, UserRole.ENGINEER):
        return None

    # Operator: alan_id -> device_id zinciri uzerinden cihaz id setini cek
    stmt = (
        select(responsibility_area_devices.c.device_id)
        .join(
            responsibility_area_users,
            responsibility_area_devices.c.area_id == responsibility_area_users.c.area_id,
        )
        .where(responsibility_area_users.c.user_id == user.id)
    )
    rows = db.execute(stmt).all()
    return {row[0] for row in rows}
