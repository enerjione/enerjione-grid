"""Zamanlanmis toplu bildirim scheduler.

Tek background thread her CHECK_INTERVAL_SEC saniyede bir DB'yi tarar:
  status='pending' + scheduled_at <= now()
bulunan ilk N kaydi alir; isler; status'unu sent/failed yapar.

Tasarim:
  - SELECT ... FOR UPDATE SKIP LOCKED kullanmiyoruz (tek-node assumption).
    Production'da birden fazla backend node varsa advisory lock veya
    'claimed_at + claimed_by' kolonu ile geliştirilebilir.
  - Idempotency: bir kez sent isaretlenince yeniden gonderilmez.
  - Actor: target_json icinde 'actor_username' tutuyoruz. send_bulk_notification
    bunu User'a cevirir; ops_manager idi ise operator filtresi yine uygulanir.
  - Hata: yakalanir, status='failed', result_summary'ye error mesaji.

Start/stop: FastAPI startup hook'tan start() cagrilir; shutdown stop().
"""
from __future__ import annotations

import json
import logging
import threading
import time
from datetime import datetime, timezone

from sqlalchemy import select

from app.db.session import SessionLocal
from app.models.bulk_notification_job import BulkNotificationJob
from app.models.user import User
from app.services.bulk_notification_service import send_bulk_notification

logger = logging.getLogger(__name__)

CHECK_INTERVAL_SEC = 30
MAX_BATCH = 20


_stop_event = threading.Event()
_thread: threading.Thread | None = None


def _process_due_jobs() -> int:
    """Bir turda en fazla MAX_BATCH kaydi isle, islenen sayisini don."""
    processed = 0
    db = SessionLocal()
    try:
        now = datetime.now(timezone.utc)
        rows = list(
            db.scalars(
                select(BulkNotificationJob)
                .where(BulkNotificationJob.status == "pending")
                .where(BulkNotificationJob.scheduled_at <= now)
                .order_by(BulkNotificationJob.scheduled_at.asc())
                .limit(MAX_BATCH)
            ).all()
        )
        if not rows:
            return 0

        for row in rows:
            try:
                payload = json.loads(row.target_json or "{}")
                actor_username = payload.get("actor_username") or ""
                actor: User | None = None
                if actor_username:
                    actor = db.scalar(select(User).where(User.username == actor_username))
                if actor is None:
                    # Actor silinmis — yine de gonderilebilir; ama defaultta
                    # ops_manager filtre uygulanmasin. Bir sistem hesabi gibi
                    # davranmak icin user bulamadiysak fail isaretle.
                    row.status = "failed"
                    row.executed_at = datetime.now(timezone.utc)
                    row.result_summary = "actor_user_not_found"
                    db.commit()
                    processed += 1
                    continue

                channels = [
                    c.strip() for c in (row.channels or "").split(",") if c.strip()
                ]
                result = send_bulk_notification(
                    db,
                    actor=actor,
                    subject=row.subject,
                    message=row.message,
                    channels=channels,
                    user_ids=list(payload.get("user_ids") or []),
                    team_ids=list(payload.get("team_ids") or []),
                    send_to_all=bool(payload.get("send_to_all", False)),
                )
                row.status = "sent"
                row.executed_at = datetime.now(timezone.utc)
                row.result_summary = (
                    f"recipients={result.recipients_count} "
                    f"web={result.web_sent} email={result.email_sent} "
                    f"sms={result.sms_sent} "
                    f"email_failed={result.email_failed} sms_failed={result.sms_failed}"
                )
                db.commit()
                processed += 1
                logger.info(
                    "bulk_notification_scheduled_sent job_id=%d recipients=%d",
                    row.id, result.recipients_count,
                )
            except Exception as exc:  # noqa: BLE001
                logger.exception("bulk_notification_scheduled_failed job_id=%d", row.id)
                try:
                    row.status = "failed"
                    row.executed_at = datetime.now(timezone.utc)
                    row.result_summary = f"error: {exc}"[:500]
                    db.commit()
                except Exception:  # noqa: BLE001
                    db.rollback()
                processed += 1
    finally:
        db.close()
    return processed


def _loop() -> None:
    logger.info("bulk_notification_scheduler_started interval=%ds", CHECK_INTERVAL_SEC)
    while not _stop_event.wait(CHECK_INTERVAL_SEC):
        try:
            _process_due_jobs()
        except Exception:  # noqa: BLE001
            logger.exception("bulk_notification_scheduler_tick_failed")
    logger.info("bulk_notification_scheduler_stopped")


def start() -> None:
    global _thread
    if _thread is not None and _thread.is_alive():
        return
    _stop_event.clear()
    _thread = threading.Thread(target=_loop, name="bulk-notify-scheduler", daemon=True)
    _thread.start()


def stop() -> None:
    _stop_event.set()
    if _thread is not None:
        _thread.join(timeout=5)
