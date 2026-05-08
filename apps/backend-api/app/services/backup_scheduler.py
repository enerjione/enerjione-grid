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


_worker = BackupSchedulerWorker()


def start() -> None:
    _worker.start()


def stop() -> None:
    _worker.stop()
