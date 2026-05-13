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

from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, status
from fastapi.responses import FileResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from datetime import datetime, timezone

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
    get_backup_dir,
    get_or_create_schedule,
    restore_backup,
    validate_dump_file,
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
    if job.status != "success":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Bu yedek indirilemez (durum: {job.status}).",
        )
    if not job.file_path:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Yedek dosya yolu kayitli degil.",
        )
    # Path traversal guard: BackupJob.file_path DB'de saklanir; SQL injection
    # veya audit miss durumunda saldirgan `/etc/passwd` gibi mutlak yol set
    # edebilir → FileResponse istenen dosyayi serve eder. Resolved path'in
    # get_backup_dir() altinda oldugundan emin ol.
    from app.services.backup_service import get_backup_dir

    backup_root = get_backup_dir().resolve()
    try:
        p = Path(job.file_path).resolve()
        # Python 3.9+ is_relative_to. Mutlak yol get_backup_dir() icinde mi?
        if not p.is_relative_to(backup_root):
            import logging as _logging

            _logging.getLogger(__name__).error(
                "backup_download_path_traversal_attempt backup_id=%s file_path=%r",
                backup_id,
                job.file_path,
            )
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Gecersiz yedek dosya yolu (path traversal koruma).",
            )
    except (ValueError, OSError) as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Yedek dosya yolu cozumlenemedi: {exc}",
        )
    if not p.exists():
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Yedek dosyasi diskte yok: {p.name}",
        )
    # Content-Disposition acikca eklenir; tarayici dosyayi indirme dialogunda
    # bu adla acar. octet-stream sayesinde tarayici icinde acmaya calismaz.
    return FileResponse(
        path=str(p),
        filename=p.name,
        media_type="application/octet-stream",
        headers={"Content-Disposition": f'attachment; filename="{p.name}"'},
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


# Backup upload icin maksimum dosya boyutu — 2 GiB. Tek bir tenant icin
# pg_dump custom format 600 cihaz + 30 gun telemetri ~500 MB civari; 2 GiB
# 4x marj. Bunun ustu: ya kotu niyetli disk doldurma, ya da hatali export.
# Streaming sirasinda asilirsa write durdurulup partial dosya silinir.
_BACKUP_UPLOAD_MAX_BYTES = 2 * 1024 * 1024 * 1024


@router.post("/upload", response_model=BackupJobRead, status_code=status.HTTP_201_CREATED)
async def upload_backup(
    file: UploadFile = File(...),
    current_user: User = Depends(require_roles([UserRole.INSTALLER, UserRole.ENGINEER])),
    db: Session = Depends(get_db),
):
    """Kullanicinin daha onceden indirdigi .dump dosyasini yukle.

    Dosya `BACKUP_DIR/uploaded-<timestamp>-<orig>.dump` olarak kaydedilir
    ve bir BackupJob (job_type='uploaded', status='success') uretilir.
    Boylece kullanici listede yedek olarak gorur ve normal Restore
    butonuyla geri yukleyebilir.

    Sadece pg_dump custom format (.dump) kabul edilir; uzanti kontrolu yapilir.
    Disk dolma DoS'una karsi 2 GiB hard cap; sinir asildiginda partial dosya
    silinir ve 413 doner.
    """
    name = (file.filename or "uploaded.dump").strip()
    if not name.lower().endswith(".dump"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Sadece .dump uzantili pg_dump custom-format dosyasi kabul edilir.",
        )
    # Dosyayi diske yaz
    now = datetime.now(timezone.utc)
    safe = "".join(c for c in name if c.isalnum() or c in "._-")[:120] or "upload.dump"
    target = get_backup_dir() / f"e1-{now.strftime('%Y%m%d-%H%M%S')}-uploaded-{safe}"
    written = 0
    try:
        with open(target, "wb") as out:
            while True:
                chunk = await file.read(1024 * 1024)
                if not chunk:
                    break
                written += len(chunk)
                if written > _BACKUP_UPLOAD_MAX_BYTES:
                    out.close()
                    try:
                        target.unlink()
                    except OSError:
                        pass
                    raise HTTPException(
                        status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                        detail=f"Yedek dosyasi cok buyuk (max {_BACKUP_UPLOAD_MAX_BYTES // (1024 * 1024)} MiB).",
                    )
                out.write(chunk)
    except HTTPException:
        raise
    except Exception:  # noqa: BLE001
        try:
            target.unlink()
        except OSError:
            pass
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Dosya kaydedilemedi.",
        )
    # Format + tehlikeli SQL pattern validation — gecmezse dosyayi sil ve 400 don.
    # Saldirgan engineer rolunu ele gecirip RCE icin malicious dump yukleyemesin.
    valid, validation_err = validate_dump_file(target)
    if not valid:
        try:
            target.unlink()
        except OSError:
            pass
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Yedek dosyasi reddedildi: {validation_err}",
        )
    try:
        size = target.stat().st_size
    except OSError:
        size = None
    job = BackupJob(
        job_type="uploaded",
        status="success",
        file_path=str(target),
        size_bytes=size,
        created_by_username=current_user.username,
        created_at=now,
        completed_at=now,
    )
    db.add(job)
    db.flush()
    record_event(
        db,
        category="backup",
        event_type="backup_uploaded",
        severity="info",
        actor_username=current_user.username,
        message=f"Yedek dosyasi yuklendi (id={job.id}, {target.name})",
        metadata={"backup_id": job.id, "size_bytes": size, "filename": target.name},
        i18n_key="backup_uploaded",
        i18n_params={"id": job.id, "name": target.name},
    )
    db.commit()
    db.refresh(job)
    return _to_read(job)


@router.post("/{backup_id}/restore", status_code=status.HTTP_202_ACCEPTED)
def restore(
    backup_id: int,
    current_user: User = Depends(require_roles([UserRole.INSTALLER, UserRole.ENGINEER])),
    db: Session = Depends(get_db),
):
    """Restore'u arka plan thread'inde tetikle, anlik 202 don.

    Frontend `GET /admin/backups/restore/status` ile polling yapip kullaniciya
    progress bar + adim listesi gosterir. restore_status_tracker tek bir
    aktif restore tutar (paralel restore engellenir).
    """
    from app.services import restore_status_tracker as _tracker
    import threading

    job = db.get(BackupJob, backup_id)
    if job is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Yedek kaydi bulunamadi.")
    if job.status != "success":
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Bu yedek geri yuklenemez.")
    if _tracker.is_running():
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Su an baska bir restore zaten calisiyor. Tamamlanmasini bekleyin.",
        )

    fname = Path(job.file_path).name if job.file_path else f"backup-{job.id}"
    # Tracker'i SENKRON baslat — endpoint donmeden status 'queued' olmali ki
    # frontend ilk polling'inde 'idle' yerine 'queued' gorsun.
    _tracker.start(job.id, fname, current_user.username)

    # Audit event (start)
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

    # Background thread — restore HTTP request'i bloklamasin
    def _run_restore_in_thread(job_id: int, username: str) -> None:
        # Yeni DB session — thread-safe degil paylasilan session.
        from app.db.session import SessionLocal as _SessionLocal
        thread_db = _SessionLocal()
        try:
            j = thread_db.get(BackupJob, job_id)
            if j is None:
                _tracker.fail(f"BackupJob {job_id} bulunamadi")
                return
            ok, err = restore_backup(thread_db, j)
            # Audit event (finish/fail)
            try:
                record_event(
                    thread_db,
                    category="backup",
                    event_type="backup_restore_finished" if ok else "backup_restore_failed",
                    severity="info" if ok else "warning",
                    actor_username=username,
                    message=(
                        f"Yedek geri yukleme tamamlandi (id={j.id})"
                        if ok
                        else f"Yedek geri yukleme hatasi: {err[:200]}"
                    ),
                    metadata={"backup_id": j.id, "error": err if not ok else None},
                    i18n_key="backup_restore_finished" if ok else "backup_restore_failed",
                    i18n_params={"id": j.id, "error": (err or "")[:200]},
                )
                thread_db.commit()
            except Exception:  # noqa: BLE001
                import logging as _logging
                _logging.getLogger(__name__).exception("restore_audit_event_failed")
        finally:
            thread_db.close()

    threading.Thread(
        target=_run_restore_in_thread,
        args=(job.id, current_user.username),
        name=f"restore-{job.id}",
        daemon=True,
    ).start()

    return {"status": "started", "backup_id": job.id}


@router.get("/restore/status")
def get_restore_status(
    _: User = Depends(require_roles([UserRole.INSTALLER, UserRole.ENGINEER])),
):
    """Aktif veya son restore'un guncel durumu — frontend polling endpoint'i.

    Frontend her 1-2 saniyede bir bu endpoint'i cagirir; status='done' veya
    'failed' olunca polling'i durdurur. 'idle' = hic restore baslamadi.
    """
    from app.services import restore_status_tracker as _tracker
    return _tracker.snapshot()
