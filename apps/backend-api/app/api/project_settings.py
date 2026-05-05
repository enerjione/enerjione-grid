"""Proje ayarlari endpoint'leri.

GET / — auth gerek**siz**. Login ekrani ve header henuz oturum yokken
   logoyu cekebilsin diye public. Sadece display alanlari donulur.
PUT / — sadece INSTALLER. Logo data URL'leri ve isimler.

Singleton tablo: id=1 satiri yoksa GET'te bos donulur, PUT'ta upsert edilir.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, status
from sqlalchemy.orm import Session

from app.api.deps import require_role
from app.db.session import get_db
from app.models.enums import UserRole
from app.models.project_settings import ProjectSettings
from app.models.user import User
from app.schemas.project_settings import ProjectSettingsRead, ProjectSettingsUpdate
from app.services.event_service import record_event

router = APIRouter(prefix="/project-settings", tags=["project-settings"])


def _get_or_empty(db: Session) -> ProjectSettings:
    row = db.get(ProjectSettings, 1)
    if row is None:
        row = ProjectSettings(id=1)
        db.add(row)
        db.commit()
        db.refresh(row)
    return row


@router.get("", response_model=ProjectSettingsRead)
def get_project_settings(db: Session = Depends(get_db)):
    """Auth gerektirmez — login ekrani ve header public okur."""
    row = db.get(ProjectSettings, 1)
    if row is None:
        return ProjectSettingsRead()
    return row


@router.put("", response_model=ProjectSettingsRead, status_code=status.HTTP_200_OK)
def update_project_settings(
    payload: ProjectSettingsUpdate,
    current_user: User = Depends(require_role(UserRole.INSTALLER)),
    db: Session = Depends(get_db),
):
    row = _get_or_empty(db)
    updates = payload.model_dump(exclude_unset=True)
    for key, value in updates.items():
        setattr(row, key, value)
    # Logo data URL'leri buyuk olabilir; metadata'ya koymak yerine kisa flag tut.
    summary_fields = []
    for k in updates.keys():
        if k in ("customer_logo", "customer_logo_light"):
            summary_fields.append(f"{k}({'set' if updates[k] else 'cleared'})")
        else:
            summary_fields.append(k)
    record_event(
        db,
        category="project-settings",
        event_type="project_settings_updated",
        severity="info",
        actor_username=current_user.username,
        message=f"Proje ayarları güncellendi: {', '.join(summary_fields)}",
        metadata={"fields": list(updates.keys())},
    )
    db.commit()
    db.refresh(row)
    return row
