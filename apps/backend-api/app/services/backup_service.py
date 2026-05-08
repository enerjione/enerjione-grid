"""DB Yedekleme servisi.

Sorumluluk:
  - pg_dump ile DB yedek dosyasi uretmek (custom format = .dump)
  - pg_restore ile yedekten geri yuklemek (DROP / CREATE schema sonrasi)
  - retention'a gore eski dosyalari silmek

DB baglanti bilgilerini settings.database_url'den parse eder. URL formati:
  postgresql+psycopg2://USER:PASSWORD@HOST:PORT/DB

Env override:
  BACKUP_DIR  — yedek dosyalari icin dizin (default: ./backups)
  PG_DUMP     — pg_dump binary path (default: pg_dump, PATH'te aranir)
  PG_RESTORE  — pg_restore binary path (default: pg_restore)
  PSQL        — psql binary (geri yuklerken DROP DATABASE icin gerekli olabilir)
"""

from __future__ import annotations

import logging
import os
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse, unquote

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.models.backup import BackupJob, BackupSchedule

logger = logging.getLogger(__name__)


def _backup_dir() -> Path:
    raw = os.getenv("BACKUP_DIR", "./backups")
    p = Path(raw).resolve()
    p.mkdir(parents=True, exist_ok=True)
    return p


def _parse_db_url(url: str) -> dict:
    """SQLAlchemy URL'den pg_dump'in cozebilecegi env doner."""
    # SQLAlchemy: postgresql+psycopg2://user:pass@host:port/db
    # urlparse postgresql+psycopg2'yi schema olarak goruyor; once + sonrasini at.
    if "://" in url and "+" in url.split("://", 1)[0]:
        proto, rest = url.split("://", 1)
        clean = proto.split("+", 1)[0] + "://" + rest
    else:
        clean = url
    u = urlparse(clean)
    return {
        "host": u.hostname or "localhost",
        "port": str(u.port or 5432),
        "user": unquote(u.username or ""),
        "password": unquote(u.password or ""),
        "dbname": (u.path or "/").lstrip("/") or "horstman",
    }


def _pg_env() -> dict[str, str]:
    db = _parse_db_url(settings.database_url)
    env = os.environ.copy()
    if db["password"]:
        env["PGPASSWORD"] = db["password"]
    return env


def run_pg_dump(file_path: Path) -> tuple[bool, str]:
    """pg_dump calistir, custom format (.dump) yaz. (success, error_msg)."""
    db = _parse_db_url(settings.database_url)
    pg_dump = os.getenv("PG_DUMP", "pg_dump")
    cmd = [
        pg_dump,
        "-h", db["host"],
        "-p", db["port"],
        "-U", db["user"],
        "-d", db["dbname"],
        "-F", "c",  # custom format (pg_restore uyumlu, sıkıştırılmış)
        "-f", str(file_path),
        "--no-owner",
        "--no-acl",
    ]
    try:
        completed = subprocess.run(
            cmd,
            env=_pg_env(),
            capture_output=True,
            text=True,
            timeout=900,  # 15 dk
        )
        if completed.returncode != 0:
            return False, (completed.stderr or "pg_dump non-zero exit")[:1900]
        return True, ""
    except FileNotFoundError:
        return False, "pg_dump bulunamadı (PATH'te olmali veya PG_DUMP env ile yol verin)."
    except subprocess.TimeoutExpired:
        return False, "pg_dump zaman aşımı (15 dk)."
    except Exception as exc:  # noqa: BLE001
        return False, f"pg_dump hatasi: {exc}"


def run_pg_restore(file_path: Path) -> tuple[bool, str]:
    """pg_restore calistir — mevcut tablolarin uzerine clean+create modunda
    yazar. UYARI: Mevcut tum DB icerigi silinir.
    """
    db = _parse_db_url(settings.database_url)
    pg_restore = os.getenv("PG_RESTORE", "pg_restore")
    cmd = [
        pg_restore,
        "-h", db["host"],
        "-p", db["port"],
        "-U", db["user"],
        "-d", db["dbname"],
        "--clean",  # mevcut objeleri DROP et
        "--if-exists",  # yoksa hata verme
        "--no-owner",
        "--no-acl",
        str(file_path),
    ]
    try:
        completed = subprocess.run(
            cmd,
            env=_pg_env(),
            capture_output=True,
            text=True,
            timeout=1200,
        )
        # pg_restore kismi hatalar icin non-zero donebilir; kritik bir
        # error olmadigi surece basari sayalim. Ama yine de stderr loglanir.
        if completed.returncode != 0 and ("FATAL" in (completed.stderr or "") or "could not connect" in (completed.stderr or "")):
            return False, (completed.stderr or "pg_restore connection error")[:1900]
        return True, (completed.stderr or "")[:500]
    except FileNotFoundError:
        return False, "pg_restore bulunamadı (PATH'te olmali veya PG_RESTORE env ile yol verin)."
    except subprocess.TimeoutExpired:
        return False, "pg_restore zaman aşımı."
    except Exception as exc:  # noqa: BLE001
        return False, f"pg_restore hatasi: {exc}"


def create_backup(
    db: Session, *, job_type: str = "manual", username: str | None = None
) -> BackupJob:
    """Yeni bir BackupJob kaydi olustur ve pg_dump'i calistir."""
    now = datetime.now(timezone.utc)
    fname = f"hsl-{now.strftime('%Y%m%d-%H%M%S')}-{job_type}.dump"
    target = _backup_dir() / fname
    job = BackupJob(
        job_type=job_type,
        status="running",
        file_path=str(target),
        created_by_username=username,
        created_at=now,
    )
    db.add(job)
    db.flush()
    db.commit()  # job kaydini hemen yaz, uzun sured operasyon

    ok, err = run_pg_dump(target)
    job = db.get(BackupJob, job.id) or job
    if ok:
        try:
            size = target.stat().st_size
        except OSError:
            size = None
        job.status = "success"
        job.size_bytes = size
        job.completed_at = datetime.now(timezone.utc)
    else:
        job.status = "failed"
        job.error_message = err
        job.completed_at = datetime.now(timezone.utc)
        # Eksik/yarim dosya kalmasin
        try:
            if target.exists():
                target.unlink()
        except OSError:
            pass
        job.file_path = None
    db.commit()
    return job


def restore_backup(db: Session, job: BackupJob) -> tuple[bool, str]:
    """Verilen yedek kaydini geri yukle. Backup file diskte olmali.

    UYARI: Mevcut DB icerigi tamamen degisir. Cagiran tarafin kullanici
    onayi alip, audit event yazip cagirmasi gerekir.
    """
    if not job.file_path:
        return False, "Yedek dosya yolu yok."
    p = Path(job.file_path)
    if not p.exists():
        return False, f"Yedek dosyasi bulunamadi: {p.name}"
    return run_pg_restore(p)


def apply_retention(db: Session, retention_count: int) -> int:
    """retention_count'tan eski yedekleri (success durumdaki) sil.

    Sadece scheduled job'lar arasinda retention uygulanir; manual job'lar
    kullanici tarafindan elle silinmedikce kalir."""
    if retention_count <= 0:
        return 0
    rows = list(
        db.scalars(
            select(BackupJob)
            .where(BackupJob.status == "success")
            .where(BackupJob.job_type == "scheduled")
            .order_by(BackupJob.created_at.desc())
        ).all()
    )
    keep = rows[:retention_count]
    drop = rows[retention_count:]
    deleted = 0
    keep_ids = {k.id for k in keep}
    for r in drop:
        try:
            if r.file_path:
                fp = Path(r.file_path)
                if fp.exists():
                    fp.unlink()
        except OSError:
            pass
        db.delete(r)
        deleted += 1
    _ = keep_ids
    if deleted > 0:
        db.commit()
    return deleted


def delete_backup_file(job: BackupJob) -> None:
    if not job.file_path:
        return
    try:
        fp = Path(job.file_path)
        if fp.exists():
            fp.unlink()
    except OSError as exc:  # noqa: BLE001
        logger.warning("backup_delete_file_failed id=%s error=%s", job.id, exc)


def get_or_create_schedule(db: Session) -> BackupSchedule:
    sch = db.get(BackupSchedule, 1)
    if sch is None:
        sch = BackupSchedule(
            id=1,
            enabled=False,
            interval_hours=24,
            retention_count=7,
        )
        db.add(sch)
        db.commit()
        db.refresh(sch)
    return sch
