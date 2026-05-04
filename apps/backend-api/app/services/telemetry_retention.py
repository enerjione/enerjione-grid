"""Telemetri retention worker — sadece son N dakikalık kayıtları tutar.

Ürün rapor/geriye dönük analiz iddiasında değil; canlı değer ve son trend
ihtiyaçları icin telemetri tablosunda kısa bir kayan pencere yeterli. Bu
worker arka planda çalışır ve TTL'i geçmiş satırları siler. Tablo sürekli
küçük kalır, gateway/cihaz silme akışlarında milyonlarca satırla uğraşmak
yerine birkaç bin satırı temizlemek kalır.

Konfigurasyon:
  TELEMETRY_RETENTION_MINUTES   default 30
  TELEMETRY_RETENTION_INTERVAL_SEC default 300 (5 dk)
"""

from __future__ import annotations

import logging
import os
import threading
import time
from datetime import datetime, timedelta, timezone

from sqlalchemy import delete

from app.db.session import SessionLocal
from app.models.telemetry import Telemetry

logger = logging.getLogger(__name__)


def _retention_minutes() -> int:
    raw = os.getenv("TELEMETRY_RETENTION_MINUTES", "30")
    try:
        n = int(raw)
        return max(1, n)
    except ValueError:
        return 30


def _interval_sec() -> int:
    raw = os.getenv("TELEMETRY_RETENTION_INTERVAL_SEC", "300")
    try:
        n = int(raw)
        return max(30, n)
    except ValueError:
        return 300


class TelemetryRetentionWorker:
    """Arka plan thread'i; periyodik DELETE çalıştırır."""

    def __init__(self) -> None:
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._run, name="telemetry-retention", daemon=True
        )
        self._thread.start()
        logger.info(
            "telemetry_retention_started keep_minutes=%d interval_sec=%d",
            _retention_minutes(),
            _interval_sec(),
        )

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=5)
            self._thread = None

    def _run(self) -> None:
        # Acilis sonrasi hemen calistir; sonra her interval'de tekrar.
        while not self._stop.is_set():
            try:
                self._purge_once()
            except Exception:  # noqa: BLE001
                logger.exception("telemetry_retention_purge_failed")
            # Stop event bekleyen sleep — shutdown anında hızlı çıkar.
            self._stop.wait(_interval_sec())

    def _purge_once(self) -> int:
        cutoff = datetime.now(timezone.utc) - timedelta(minutes=_retention_minutes())
        db = SessionLocal()
        try:
            result = db.execute(
                delete(Telemetry).where(Telemetry.source_timestamp < cutoff)
            )
            db.commit()
            removed = int(getattr(result, "rowcount", 0) or 0)
            if removed > 0:
                logger.info(
                    "telemetry_retention_purged removed=%d cutoff=%s",
                    removed,
                    cutoff.isoformat(),
                )
            return removed
        finally:
            db.close()


_worker = TelemetryRetentionWorker()


def start() -> None:
    _worker.start()


def stop() -> None:
    _worker.stop()
