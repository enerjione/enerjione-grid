"""Aktif kullanici oturumu yonetimi — installer-only.

Endpoints:
  GET    /admin/sessions          → tum aktif (revoked_at IS NULL) oturumlar
  DELETE /admin/sessions/{jti}    → tek oturumu at (revoke)
"""
from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.api.deps import require_role
from app.db.session import get_db
from app.models.enums import UserRole
from app.models.user import User
from app.models.user_session import UserSession
from app.services.auth_service import revoke_jti

router = APIRouter(prefix="/admin/sessions", tags=["admin-sessions"])


class SessionRead(BaseModel):
    jti: str
    user_id: int
    username: str
    full_name: str
    role: str
    ip_address: str | None
    user_agent: str | None
    login_at: str
    last_seen_at: str
    expires_at: str | None
    is_self: bool


@router.get("", response_model=list[SessionRead])
def list_active_sessions(
    current_user: User = Depends(require_role(UserRole.INSTALLER)),
    db: Session = Depends(get_db),
):
    """Gercekten aktif olan oturumlari listele.

    Iki kosul birlikte aranir:
      * `revoked_at IS NULL` — logout / installer 'at' ile kapatilmamis
      * `expires_at > now` — JWT'nin exp'i gecmemis

    Ikinci kosul olmadan token'i coktan expire olmus her login satiri
    sonsuza kadar "aktif" gorunuyordu; ayni kullanici onlarca kez
    listelenebiliyordu. `expires_at IS NULL` (0015 oncesi kayitlar) toleransli
    kabul edilir — migration bunlari login_at + 7 gun ile backfill ediyor.

    En son aktif olanlar uste gelir (last_seen_at DESC).
    """
    now = datetime.now(timezone.utc)
    stmt = (
        select(UserSession, User)
        .join(User, UserSession.user_id == User.id)
        .where(
            UserSession.revoked_at.is_(None),
            or_(UserSession.expires_at.is_(None), UserSession.expires_at > now),
        )
        .order_by(UserSession.last_seen_at.desc())
        .limit(500)
    )
    out: list[SessionRead] = []
    for sess, user in db.execute(stmt).all():
        out.append(
            SessionRead(
                jti=sess.jti,
                user_id=user.id,
                username=user.username,
                full_name=user.full_name,
                role=user.role.value if hasattr(user.role, "value") else str(user.role),
                ip_address=sess.ip_address,
                user_agent=sess.user_agent,
                login_at=sess.login_at.isoformat(),
                last_seen_at=sess.last_seen_at.isoformat(),
                expires_at=sess.expires_at.isoformat() if sess.expires_at else None,
                is_self=(sess.jti == _extract_self_jti(current_user)),
            )
        )
    return out


def _extract_self_jti(_user: User) -> str:
    """Caller'in kendi jti'sini bulmak icin placeholder.

    require_role bize User dondurdu, JWT payload'a erisimimiz yok. Kendi
    oturumunu vurgulamak istersek bunu farkli bir Dependency ile cozeriz.
    Simdilik bos string don — frontend tarafi 'kendi jti'sini' login
    cevabindan veya cookie'den degil de username/IP esleme ile vurgular.
    """
    return ""


@router.delete("/{jti}", status_code=status.HTTP_204_NO_CONTENT)
def revoke_session(
    jti: str,
    current_user: User = Depends(require_role(UserRole.INSTALLER)),
    db: Session = Depends(get_db),
):
    """Belirtilen jti'yi (oturumu) revoke et. Kullanici bir sonraki
    istegide 401 alir ve login ekranina dusurulur.
    """
    sess = db.get(UserSession, jti)
    if sess is None:
        raise HTTPException(status_code=404, detail="Oturum bulunamadi")
    if sess.revoked_at is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Bu oturum zaten revoke edilmis",
        )
    now = datetime.now(timezone.utc)
    sess.revoked_at = now
    sess.revoked_by_user_id = current_user.id

    # In-memory blacklist'e de ekle (anlik etki icin; DB lookup zaten var ama
    # hizli yol = memory list).
    # JWT exp epoch'unu bilmiyoruz; max access_token_minutes uzunlugunda
    # tutarsak overshoot olmayız. settings'tan al.
    try:
        from app.core.config import settings
        max_minutes = max(
            settings.access_token_minutes, settings.remember_me_token_minutes
        )
        revoke_jti(jti, now.timestamp() + max_minutes * 60)
    except Exception:  # noqa: BLE001
        pass

    db.commit()
    return None
