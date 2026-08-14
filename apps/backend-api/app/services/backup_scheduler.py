"""Periyodik yedek alma worker'i.

BackupSchedule kaydina bakar; enabled=True ise her interval_hours saatte
bir scheduled tipte yedek olusturur. retention_count'tan eskileri siler.

Program VARSAYILAN OLARAK ACIKTIR (bkz. backup_service.get_or_create_schedule).
Temiz kurulumda `last_run_at` bos oldugu icin ilk yedek 24 saat beklemeden
ilk turda alinir; sonraki her yeniden baslatmada bu alan dolu oldugu icin
tekrar alinmaz.

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


class BackupDiskFull(RuntimeError):
    """Yedek dizini hard-stop esigini asti — bu tur ATLANMALI.

    Beklenmedik bir hatadan AYRI bir sinif olmasi sart: cagiran taraf
    "bilincli olarak almadik" ile "olcemedik" arasindaki farka gore
    davraniyor (bkz. `_maybe_run`).
    """


def _olay_yaz(
    db,  # noqa: ANN001
    *,
    event_type: str,
    severity: str,
    message: str,
    metadata: dict,
    i18n_key: str,
    i18n_params: dict | None = None,
) -> None:
    """Denetim kaydi yazar; hata scheduler'i DUSURMEZ.

    Olay yazamamak, yedek turunu bosa cikarmak icin yeterli bir sebep
    degil — bu yuzden burada yutuluyor.
    """
    from app.services.event_service import record_event

    try:
        record_event(
            db,
            category="backup",
            event_type=event_type,
            severity=severity,
            actor_username="(system)",
            message=message,
            metadata=metadata,
            i18n_key=i18n_key,
            i18n_params=i18n_params,
        )
        db.commit()
    except Exception:  # noqa: BLE001
        db.rollback()
        logger.exception("backup_event_write_failed type=%s", event_type)


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
            # log'la; %95'in uzerinde ise backup'i ATLA.
            #
            # IKI HATA SINIFI AYRILIR — eskiden ayrilmiyordu:
            #   BackupDiskFull       : bilincli "yer yok, alma" karari. Yedek
            #                          ATLANIR ve `last_run_at` DAMGALANMAZ,
            #                          yani operator yer acar acmaz bir
            #                          sonraki turda (5 dk) yedek alinir.
            #   diger her sey        : olcum yapilamadi (izin, mount kaybi).
            #                          Karar verecek veri yok; yedege devam
            #                          edilir — eski davranis.
            #
            # Onceki kod ikisini de yutuyordu: %95 esiginde firlatilan
            # RuntimeError yakalanip loglaniyor ve YEDEK YINE ALINIYORDU.
            # Yani "hard stop" belgelenmis ama islemiyordu. Zamanli yedek
            # varsayilan olarak acildigi icin bu artik gercek bir disk
            # doldurma yolu.
            try:
                _check_backup_disk_usage(db)
            except BackupDiskFull as exc:
                logger.warning("backup_scheduler_skipped reason=%s", exc)
                return
            except Exception:  # noqa: BLE001
                logger.exception("backup_disk_check_failed")

            # Zamani geldi — scheduled yedek al
            logger.info("backup_scheduler_running_job interval_hours=%d", sch.interval_hours)
            job = None
            hata: str | None = None
            try:
                job = create_backup(db, job_type="scheduled", username="(system)")
            except Exception as exc:  # noqa: BLE001
                # `create_backup` normalde firlatMAZ (pg_dump hatasini
                # job.status='failed' olarak yazar). Buraya dusuluyorsa ariza
                # daha derinde (pg_dump ikilisi cozulemedi, DB yazilamadi).
                # YUTULUR, cunku firlatmak dis dongude 5 dakikalik bir
                # yeniden deneme firtinasina ve her turda bir 'running'
                # oksuz is kaydina donusurdu.
                hata = str(exc)[:500]
                logger.exception("backup_scheduler_create_failed")

            # `last_run_at` BASARISIZLIKTA DA damgalanir: aksi halde kalici
            # bir ariza (or. eksik pg_dump) her 5 dakikada bir yeniden
            # denenir, log'u doldurur ve arizayi gizler. Bir sonraki deneme
            # normal periyotta yapilir; gorunurluk asagidaki olay kaydiyla
            # saglanir.
            sch = get_or_create_schedule(db)
            sch.last_run_at = datetime.now(timezone.utc)
            db.commit()

            basarili = hata is None and job is not None and job.status == "success"
            if basarili:
                logger.info(
                    "backup_scheduler_job_ok id=%s size_bytes=%s", job.id, job.size_bytes
                )
            else:
                # Sessiz basarisizlik, hic yedek almamakla ayni sonucu verir.
                # Denetim kaydi olmadan bu ancak yedege ihtiyac duyuldugunda
                # — yani en gec anda — fark edilirdi.
                sebep = hata or (job.error_message if job is not None else None) or "bilinmiyor"
                logger.error("backup_scheduler_job_failed reason=%s", sebep)
                _olay_yaz(
                    db,
                    event_type="backup_scheduled_failed",
                    severity="error",
                    message=f"Zamanli yedek alinamadi: {sebep}",
                    metadata={
                        "job_id": job.id if job is not None else None,
                        "reason": sebep,
                        "interval_hours": sch.interval_hours,
                    },
                    i18n_key="backup_scheduled_failed",
                    i18n_params={"reason": sebep},
                )
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
    %95+ : `BackupDiskFull` fırlat — backup atlanir; admin disk acmadan
           cron tekrar denesin. Tip ONEMLI: cagiran bunu duz bir hatadan
           ayirt edip yedegi ATLIYOR (duz `RuntimeError` yutuluyor ve
           yedek yine aliniyordu).
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
        raise BackupDiskFull(f"Backup dizini %{used_pct} dolu — backup atlandi")

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
