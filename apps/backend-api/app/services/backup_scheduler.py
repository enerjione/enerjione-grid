"""Periyodik yedek alma worker'i.

BackupSchedule kaydina bakar; enabled=True ise her interval_hours saatte
bir scheduled tipte yedek olusturur. retention_count'tan eskileri siler.

telemetry_retention pattern'inde basit thread loop. main.py startup'ta
baslatilir, shutdown'da durur.

Konfigurasyon:
  BACKUP_SCHEDULER_TICK_SEC  default 300 (5 dk) — schedule check araligi.
"""

from __future__ import annotations

import logging
import os
import threading
from datetime import datetime, timezone

from app.db.session import SessionLocal
from app.services.backup_service import (
    apply_retention,
    create_backup,
    get_or_create_schedule,
)

logger = logging.getLogger(__name__)


def _tick_sec() -> int:
    raw = os.getenv("BACKUP_SCHEDULER_TICK_SEC", "300")
    try:
        return max(60, int(raw))
    except ValueError:
        return 300


class BackupSchedulerWorker:
    def __init__(self) -> None:
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._run, name="backup-scheduler", daemon=True
        )
        self._thread.start()
        logger.info("backup_scheduler_started tick_sec=%d", _tick_sec())

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=5)
            self._thread = None

    def _run(self) -> None:
        # Acilis sonrasi 30sn bekle (DB migration vb tamamlansin)
        self._stop.wait(30)
        while not self._stop.is_set():
            try:
                self._maybe_run()
            except Exception:  # noqa: BLE001
                logger.exception("backup_scheduler_tick_failed")
            self._stop.wait(_tick_sec())

    def _maybe_run(self) -> None:
        db = SessionLocal()
        try:
            sch = get_or_create_schedule(db)
            if not sch.enabled:
                return
            now = datetime.now(timezone.utc)
            if sch.last_run_at is not None:
                last = sch.last_run_at
                if last.tzinfo is None:
                    last = last.replace(tzinfo=timezone.utc)
                elapsed_h = (now - last).total_seconds() / 3600.0
                if elapsed_h < sch.interval_hours:
                    return
            # Disk-full proaktif alert — backup almadan once disk doluluk
            # oranini kontrol et. %85'in uzerinde ise warning event yaz +
            # log'la; %95'in uzerinde ise backup'i atla (disk-full hatasi
            # cikisini bekleyip recovery zorlamasi yerine).
            try:
                _check_backup_disk_usage(db)
            except Exception:  # noqa: BLE001
                logger.exception("backup_disk_check_failed")

            # Zamani geldi — scheduled yedek al
            logger.info("backup_scheduler_running_job interval_hours=%d", sch.interval_hours)
            create_backup(db, job_type="scheduled", username="(system)")
            sch = get_or_create_schedule(db)
            sch.last_run_at = datetime.now(timezone.utc)
            db.commit()
            # Retention uygula
            try:
                deleted = apply_retention(db, sch.retention_count)
                if deleted > 0:
                    logger.info("backup_retention_applied deleted=%d", deleted)
            except Exception:  # noqa: BLE001
                logger.exception("backup_retention_failed")
        finally:
            db.close()


# Disk threshold: %85 warn, %95 hard stop. Cogu sahada yedek dizini ayri
# bir disk degil; postgres data ile ayni volume — backup almak postgres
# yazimlarini da etkiler (transient slow query + ENOSPC riski).
_DISK_WARN_THRESHOLD = 0.85
_DISK_BLOCK_THRESHOLD = 0.95


def _check_backup_disk_usage(db) -> None:
    """Backup dizininin disk doluluk oranini kontrol et + alert yaz.

    %85+ : warning event (kullanici dashboard'da gorur).
    %95+ : RuntimeError fırlat — backup atlanir; admin disk acmadan
           cron tekrar denesin.
    """
    import shutil as _shutil

    from app.services.backup_service import get_backup_dir
    from app.services.event_service import record_event

    try:
        backup_dir = get_backup_dir()
        usage = _shutil.disk_usage(str(backup_dir))
    except OSError as exc:
        logger.warning("backup_disk_usage_check_failed error=%s", exc)
        return

    if usage.total <= 0:
        return
    used_ratio = 1.0 - (usage.free / usage.total)
    used_pct = round(used_ratio * 100, 1)

    if used_ratio >= _DISK_BLOCK_THRESHOLD:
        logger.error(
            "backup_disk_critical used_pct=%.1f%% threshold=%.1f%% — backup blokladi",
            used_pct,
            _DISK_BLOCK_THRESHOLD * 100,
        )
        record_event(
            db,
            category="backup",
            event_type="backup_disk_critical",
            severity="critical",
            actor_username="(system)",
            message=(
                f"Yedek dizini %{used_pct} dolu — scheduled backup atlandi. "
                f"Disk acmadan yedek alinmayacak."
            ),
            metadata={"used_pct": used_pct, "threshold_pct": _DISK_BLOCK_THRESHOLD * 100},
            i18n_key="backup_disk_critical",
            i18n_params={"pct": used_pct},
        )
        db.commit()
        raise RuntimeError(f"Backup dizini %{used_pct} dolu — backup atlandi")

    if used_ratio >= _DISK_WARN_THRESHOLD:
        logger.warning(
            "backup_disk_warning used_pct=%.1f%% threshold=%.1f%%",
            used_pct,
            _DISK_WARN_THRESHOLD * 100,
        )
        record_event(
            db,
            category="backup",
            event_type="backup_disk_warning",
            severity="warning",
            actor_username="(system)",
            message=f"Yedek dizini %{used_pct} dolu — yer acilmazsa backup'lar duracak.",
            metadata={"used_pct": used_pct, "threshold_pct": _DISK_WARN_THRESHOLD * 100},
            i18n_key="backup_disk_warning",
            i18n_params={"pct": used_pct},
        )
        db.commit()


_worker = BackupSchedulerWorker()


def start() -> None:
    _worker.start()


def stop() -> None:
    _worker.stop()
