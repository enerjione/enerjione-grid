"""Yedekleme & geri yukleme API endpoint'leri.

Yetki: SADECE installer/engineer (require_roles).

Endpoint'ler:
  GET    /admin/backups                  -> liste (en yeni once)
  POST   /admin/backups                  -> yeni manuel yedek (sync, pg_dump)
  GET    /admin/backups/{id}/download    -> .dump dosyasini indir
  DELETE /admin/backups/{id}             -> kayit + dosya sil
  POST   /admin/backups/{id}/restore     -> bu yedekten geri yukle
  GET    /admin/backups/schedule         -> periyodik yedek ayarlari
  PUT    /admin/backups/schedule         -> ayarlari guncelle
"""

from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import FileResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import require_roles
from app.db.session import get_db
from app.models.backup import BackupJob, BackupSchedule
from app.models.enums import UserRole
from app.models.user import User
from app.schemas.backup import (
    BackupJobRead,
    BackupScheduleRead,
    BackupScheduleUpdate,
)
from app.services.backup_service import (
    create_backup,
    delete_backup_file,
    get_or_create_schedule,
    restore_backup,
)
from app.services.event_service import record_event

router = APIRouter(
    prefix="/admin/backups",
    tags=["backups"],
    dependencies=[Depends(require_roles([UserRole.INSTALLER, UserRole.ENGINEER]))],
)


def _to_read(j: BackupJob) -> BackupJobRead:
    fname = None
    if j.file_path:
        try:
            fname = Path(j.file_path).name
        except Exception:  # noqa: BLE001
            fname = None
    return BackupJobRead(
        id=j.id,
        job_type=j.job_type,
        status=j.status,
        file_path=j.file_path,
        size_bytes=j.size_bytes,
        error_message=j.error_message,
        created_by_username=j.created_by_username,
        created_at=j.created_at,
        completed_at=j.completed_at,
        filename=fname,
    )


@router.get("", response_model=list[BackupJobRead])
def list_backups(db: Session = Depends(get_db)):
    rows = list(
        db.scalars(select(BackupJob).order_by(BackupJob.created_at.desc())).all()
    )
    return [_to_read(j) for j in rows]


@router.post("", response_model=BackupJobRead, status_code=status.HTTP_201_CREATED)
def create_manual_backup(
    current_user: User = Depends(require_roles([UserRole.INSTALLER, UserRole.ENGINEER])),
    db: Session = Depends(get_db),
):
    """Manuel yedek tetikle. pg_dump senkron calisir; uzun surebilir.
    Frontend buton tikladiktan sonra request'i bekler (loading)."""
    job = create_backup(db, job_type="manual", username=current_user.username)
    record_event(
        db,
        category="backup",
        event_type="backup_created" if job.status == "success" else "backup_failed",
        severity="info" if job.status == "success" else "warning",
        actor_username=current_user.username,
        message=(
            f"Manuel yedek alındı: {Path(job.file_path).name if job.file_path else '-'}"
            if job.status == "success"
            else f"Manuel yedek başarısız: {job.error_message or '-'}"
        ),
        metadata={"backup_id": job.id, "size_bytes": job.size_bytes},
        i18n_key="backup_created" if job.status == "success" else "backup_failed",
        i18n_params={
            "id": job.id,
            "size": job.size_bytes or "-",
            "error": job.error_message or "-",
        },
    )
    db.commit()
    return _to_read(job)


@router.get("/schedule", response_model=BackupScheduleRead)
def get_schedule(db: Session = Depends(get_db)):
    sch = get_or_create_schedule(db)
    return BackupScheduleRead.model_validate(sch, from_attributes=True)


@router.put("/schedule", response_model=BackupScheduleRead)
def update_schedule(
    payload: BackupScheduleUpdate,
    current_user: User = Depends(require_roles([UserRole.INSTALLER, UserRole.ENGINEER])),
    db: Session = Depends(get_db),
):
    sch = get_or_create_schedule(db)
    if payload.enabled is not None:
        sch.enabled = bool(payload.enabled)
    if payload.interval_hours is not None:
        sch.interval_hours = max(1, int(payload.interval_hours))
    if payload.retention_count is not None:
        sch.retention_count = max(1, int(payload.retention_count))
    record_event(
        db,
        category="backup",
        event_type="backup_schedule_updated",
        severity="info",
        actor_username=current_user.username,
        message=(
            f"Yedek programi guncellendi: enabled={sch.enabled}, "
            f"interval_hours={sch.interval_hours}, retention={sch.retention_count}"
        ),
        metadata={
            "enabled": sch.enabled,
            "interval_hours": sch.interval_hours,
            "retention_count": sch.retention_count,
        },
        i18n_key="backup_schedule_updated",
    )
    db.commit()
    db.refresh(sch)
    return BackupScheduleRead.model_validate(sch, from_attributes=True)


@router.get("/{backup_id}/download")
def download_backup(backup_id: int, db: Session = Depends(get_db)):
    job = db.get(BackupJob, backup_id)
    if job is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Yedek kaydi bulunamadi.")
    if job.status != "success" or not job.file_path:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Bu yedek indirilemez (henuz tamamlanmadi veya silindi).")
    p = Path(job.file_path)
    if not p.exists():
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Yedek dosyasi diskte yok.")
    return FileResponse(
        path=str(p),
        filename=p.name,
        media_type="application/octet-stream",
    )


@router.delete("/{backup_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_backup(
    backup_id: int,
    current_user: User = Depends(require_roles([UserRole.INSTALLER, UserRole.ENGINEER])),
    db: Session = Depends(get_db),
):
    job = db.get(BackupJob, backup_id)
    if job is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Yedek kaydi bulunamadi.")
    delete_backup_file(job)
    record_event(
        db,
        category="backup",
        event_type="backup_deleted",
        severity="info",
        actor_username=current_user.username,
        message=f"Yedek silindi (id={job.id})",
        metadata={"backup_id": job.id},
        i18n_key="backup_deleted",
        i18n_params={"id": job.id},
    )
    db.delete(job)
    db.commit()
    return None


@router.post("/{backup_id}/restore", status_code=status.HTTP_202_ACCEPTED)
def restore(
    backup_id: int,
    current_user: User = Depends(require_roles([UserRole.INSTALLER, UserRole.ENGINEER])),
    db: Session = Depends(get_db),
):
    job = db.get(BackupJob, backup_id)
    if job is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Yedek kaydi bulunamadi.")
    if job.status != "success":
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Bu yedek geri yuklenemez.")
    record_event(
        db,
        category="backup",
        event_type="backup_restore_started",
        severity="warning",
        actor_username=current_user.username,
        message=f"Yedek geri yukleme baslatildi (id={job.id})",
        metadata={"backup_id": job.id},
        i18n_key="backup_restore_started",
        i18n_params={"id": job.id},
    )
    db.commit()
    ok, err = restore_backup(db, job)
    record_event(
        db,
        category="backup",
        event_type="backup_restore_finished" if ok else "backup_restore_failed",
        severity="info" if ok else "warning",
        actor_username=current_user.username,
        message=(
            f"Yedek geri yukleme tamamlandi (id={job.id})"
            if ok
            else f"Yedek geri yukleme hatasi: {err[:200]}"
        ),
        metadata={"backup_id": job.id, "error": err if not ok else None},
        i18n_key="backup_restore_finished" if ok else "backup_restore_failed",
        i18n_params={"id": job.id, "error": (err or "")[:200]},
    )
    db.commit()
    if not ok:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=err or "Geri yukleme basarisiz.")
    return {"status": "restored", "backup_id": job.id}
