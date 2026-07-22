"""Outbox flush worker — telemetri yayinini request yolundan ayirir.

Onceki mimaride `ingest_gateway_batch` request'in ICINDE `flush_outbox` cagirir,
o da her outbox satirini senkron RabbitMQ'ya publish ederdi. RabbitMQ tek
BlockingConnection/channel + lock ile seri oldugu icin 200 cihaz ayni anda
yayinladiginda ingest response'u bu lock'ta kuyruga girer ve gateway
"Read timed out" alirdi.

Simdi ingest sadece DB'ye yazar/commit eder (published=False). Bu worker tek
arka plan thread'inde kisa araliklarla `flush_outbox` cagirip RabbitMQ'ya
yayinlar. At-least-once korunur: yayinlanmamis satir DB'de bekler, worker
restart'a dayaniklidir.

Backlog varsa (bir turda limit kadar satir yayinlandiysa) hemen devam eder;
bosaltinca kisa uyur. Tek worker => tek RabbitMQ publisher => lock cekismesi yok.

Konfigurasyon (env):
  OUTBOX_FLUSH_INTERVAL_SEC   default 0.3  (bos iken bekleme)
  OUTBOX_FLUSH_BATCH          default 200  (bir turda yayin siniri)
"""

from __future__ import annotations

import logging
import os
import threading

from app.db.session import SessionLocal
from app.services.outbox_service import flush_outbox

logger = logging.getLogger(__name__)


def _interval_sec() -> float:
    try:
        return max(0.05, float(os.getenv("OUTBOX_FLUSH_INTERVAL_SEC", "0.3")))
    except ValueError:
        return 0.3


def _batch() -> int:
    try:
        return max(1, int(os.getenv("OUTBOX_FLUSH_BATCH", "200")))
    except ValueError:
        return 200


class OutboxFlushWorker:
    def __init__(self) -> None:
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._run, name="outbox-flush", daemon=True
        )
        self._thread.start()
        logger.info(
            "outbox_flush_worker_started interval_sec=%.2f batch=%d",
            _interval_sec(), _batch(),
        )

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=5)
            self._thread = None

    def _run(self) -> None:
        batch = _batch()
        consecutive_errors = 0
        while not self._stop.is_set():
            try:
                db = SessionLocal()
                try:
                    published = flush_outbox(db, limit=batch)
                finally:
                    db.close()
                consecutive_errors = 0
                # Bir turda limit kadar yayinlandiysa backlog var — beklemeden
                # devam et. Az yayin/bos ise kisa uyu (broker'i bombardiman etme).
                if published >= batch:
                    continue
            except Exception:  # noqa: BLE001
                consecutive_errors += 1
                # Broker/DB gecici hatasi — spam onle, ilk + periyodik logla.
                if consecutive_errors in (1, 10, 100) or consecutive_errors % 300 == 0:
                    logger.exception(
                        "outbox_flush_failed consecutive=%d (retrier devam ediyor)",
                        consecutive_errors,
                    )
            self._stop.wait(_interval_sec())


_worker = OutboxFlushWorker()


def start() -> None:
    _worker.start()


def stop() -> None:
    _worker.stop()
