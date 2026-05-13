"""Kullanici kapsami (scope) - operator rolu sadece kendi sorumluluk alanlarindaki
cihazlari/alarm/sinyalleri gorur. Engineer ve Installer kisitsiz erisim.

API endpoint'leri bu helper'i cagirip ek `WHERE device_id IN (...)` clausesi
uygular. Kapsam None doner ise filtre uygulanmaz (kisit yok)."""

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.enums import UserRole
from app.models.grid_topology import Line, Pole
from app.models.responsibility_area import (
    responsibility_area_devices,
    responsibility_area_lines,
    responsibility_area_regions,
    responsibility_area_users,
)
from app.models.user import User


def get_visible_device_ids(db: Session, user: User) -> set[int] | None:
    """Kullanicinin gorebilecegi cihaz id setini doner.

    - INSTALLER ve ENGINEER: None doner -> kisit yok, tum cihazlar.
    - OPERATOR: kendi sorumluluk alanlarindaki cihazlarin id'leri (set).
      Hicbir alana atanmamis operatorler bos set goryp hicbir cihazi gormez.
    """
    if user.role in (UserRole.INSTALLER, UserRole.ENGINEER, UserRole.OPS_MANAGER):
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


def get_visible_line_ids(db: Session, user: User) -> set[int] | None:
    """Kullanicinin gorebilecegi line_id setini doner. Engineer/Installer
    icin None (kisit yok). Operator icin: ekibinin kapsamadaki bolgelerin
    altindaki tum hatlar + ekibe direkt eklenmis hatlar.
    """
    if user.role in (UserRole.INSTALLER, UserRole.ENGINEER, UserRole.OPS_MANAGER):
        return None
    line_ids: set[int] = set()
    # 1) Direkt ekibe eklenmis hatlar
    for row in db.execute(
        select(responsibility_area_lines.c.line_id)
        .join(
            responsibility_area_users,
            responsibility_area_lines.c.area_id == responsibility_area_users.c.area_id,
        )
        .where(responsibility_area_users.c.user_id == user.id)
    ).all():
        line_ids.add(int(row[0]))
    # 2) Ekipteki bolgelerin altindaki tum hatlar
    region_ids = {
        int(row[0])
        for row in db.execute(
            select(responsibility_area_regions.c.region_id)
            .join(
                responsibility_area_users,
                responsibility_area_regions.c.area_id == responsibility_area_users.c.area_id,
            )
            .where(responsibility_area_users.c.user_id == user.id)
        ).all()
    }
    if region_ids:
        for row in db.execute(
            select(Line.id).where(Line.region_id.in_(region_ids))
        ).all():
            line_ids.add(int(row[0]))
    return line_ids


def get_users_in_scope_for_device(db: Session, device_id: int) -> list[User]:
    """Bir cihazin alarm/fault'undan haberdar olmasi gereken kullanicilari doner.

    Kapsam:
      - Engineer ve Installer rolundeki tum AKTIF kullanicilar (sistem yoneticileri,
        her yerden haberdar olur).
      - Bu cihaza dogrudan baglı sorumluluk alanindaki Operator'ler.
      - Cihazin hatti/bolgesi sorumluluk alanindaysa o alana bagli Operator'ler.

    SMS gonderiminde "ekibi disindaki kullaniciya gondermeyecek" kuralini
    bu fonksiyon zaten karsilar — sadece dogrudan/dolayli sorumluluk alani
    olan operatorler donulur. Engineer/Installer her zaman dahildir cunku
    sistemin sorumlusudur.
    """
    from app.models.device import Device
    user_ids: set[int] = set()
    # Engineer + Installer (kisitsiz) — ama davet edilmis ve henuz sifre
    # belirlememis user'lara bildirim gitmesin (hashed_password=NULL).
    # Pending invitation kullanicilar hesabini aktive etmeden operasyonel
    # veri (alarm icerigi, cihaz adi) email/SMS ile sizmasin.
    for u in db.scalars(
        select(User).where(
            User.role.in_([UserRole.ENGINEER, UserRole.INSTALLER, UserRole.OPS_MANAGER]),
            User.hashed_password.isnot(None),
        )
    ).all():
        user_ids.add(u.id)
    # Cihaza dogrudan bagli operatorler
    for row in db.execute(
        select(responsibility_area_users.c.user_id)
        .join(
            responsibility_area_devices,
            responsibility_area_devices.c.area_id == responsibility_area_users.c.area_id,
        )
        .where(responsibility_area_devices.c.device_id == device_id)
    ).all():
        user_ids.add(int(row[0]))
    # Cihazin oturdugu segment(ler) -> hat -> sorumluluk alani
    # Cihaz birden fazla segmentte olabilir mi? Tasarımda hayir, ama edge case
    # icin "iliski varsa" kullanip line/region uzerinden de operatorleri ekleriz.
    device = db.get(Device, device_id)
    if device is not None:
        # Segment uzerinden hat
        from app.models.grid_topology import LineSegment, Region
        seg = db.scalar(select(LineSegment).where(LineSegment.device_id == device_id))
        if seg is not None:
            # Hat dogrudan ekipte ise
            for row in db.execute(
                select(responsibility_area_users.c.user_id)
                .join(
                    responsibility_area_lines,
                    responsibility_area_lines.c.area_id == responsibility_area_users.c.area_id,
                )
                .where(responsibility_area_lines.c.line_id == seg.line_id)
            ).all():
                user_ids.add(int(row[0]))
            # Hattin bolgesi ekipte ise
            line = db.get(Line, seg.line_id)
            if line is not None:
                for row in db.execute(
                    select(responsibility_area_users.c.user_id)
                    .join(
                        responsibility_area_regions,
                        responsibility_area_regions.c.area_id == responsibility_area_users.c.area_id,
                    )
                    .where(responsibility_area_regions.c.region_id == line.region_id)
                ).all():
                    user_ids.add(int(row[0]))
            _ = Pole, Region  # imports
    if not user_ids:
        return []
    return list(db.scalars(select(User).where(User.id.in_(user_ids))).all())
