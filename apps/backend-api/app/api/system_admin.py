"""Sistem yonetim endpoint'leri (restart vb.).

Yetki: SADECE installer/engineer.

POST /admin/system/restart
  Backend-api process'ini exit eder. Docker compose'ta `restart: unless-stopped`
  policy ayakta oldugu icin container otomatik yeniden baslar (~3-5 sn). Worker
  servisleri (tag-engine, alarm-service, notification-worker, iec104-outbound)
  RabbitMQ baglantisi uzerinden calismaya devam eder; backend kalkinca yeni
  HTTP cagrilarini kabul eder. Native (Docker'siz) kurulumlarda exit sonrasi
  otomatik kalkis yoktur — kullanici process'i elle yeniden baslatmali.

  Tipik kullanim: pg_restore sonrasi DB schema/seed degisiminden tam emin
  olmak icin. Restore basarili olduktan sonra backups.py engine.dispose()
  cagiriyor; cogu durumda restart gerekmez. Bu endpoint manuel sigorta.
"""
from __future__ import annotations

import logging
import os
import threading
import time

from fastapi import APIRouter, Depends, status

from app.api.deps import require_roles
from app.models.enums import UserRole
from app.models.user import User

router = APIRouter(
    prefix="/admin/system",
    tags=["system-admin"],
    dependencies=[Depends(require_roles([UserRole.INSTALLER, UserRole.ENGINEER]))],
)

_log = logging.getLogger(__name__)


def _delayed_exit(delay_sec: float) -> None:
    """HTTP yanitinin client'a ulasmasi icin kisa bir gecikme ardindan
    process'i sonlandirir. Docker `restart: unless-stopped` policy'si
    container'i yeniden baslatir."""
    time.sleep(delay_sec)
    _log.warning("admin_restart_exiting_now pid=%s", os.getpid())
    # os._exit: atexit / signal handler'lari atla, hizli ve tam exit.
    # Container PID 1 cikinca Docker reschedule eder.
    os._exit(0)


@router.post("/restart", status_code=status.HTTP_202_ACCEPTED)
def restart_backend(
    current_user: User = Depends(require_roles([UserRole.INSTALLER, UserRole.ENGINEER])),
):
    """Backend container'ini yeniden baslat. Yanit hemen 202 doner; gercek
    exit ~1.5sn sonra ayri bir thread'de gerceklesir."""
    _log.warning(
        "admin_restart_requested actor=%s pid=%s", current_user.username, os.getpid()
    )
    threading.Thread(target=_delayed_exit, args=(1.5,), daemon=True).start()
    return {
        "status": "restarting",
        "estimated_downtime_sec": 5,
        "actor": current_user.username,
    }
