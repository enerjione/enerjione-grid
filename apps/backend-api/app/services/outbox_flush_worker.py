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
  OUTBOX_FLUSH_INTERVAL_SEC          default 0.3   (bos iken bekleme)
  OUTBOX_FLUSH_BATCH                 default 200   (bir turda yayin siniri)
  OUTBOX_PUBLISHED_RETENTION_HOURS   default 1     (published=True saklama)
  OUTBOX_PURGE_INTERVAL_SEC          default 10    (cleanup periyodu)
  OUTBOX_PURGE_BATCH                 default 10000 (tek cleanup delete siniri)
"""

from __future__ import annotations

import logging
import os
import threading
import time
from datetime import datetime, timedelta, timezone

from app.db.session import SessionLocal
from app.services.outbox_service import flush_outbox, purge_published_outbox

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


def _retention_hours() -> int:
    """Yayinlanmis outbox satirlarinin saklama suresi (saat).

    VARSAYILAN 24 -> 1 (2026-08-01, saha test cihazi olcumu).

    Bu satirlar broker'a ZATEN teslim edilmis; saklamanin tek karsiligi adli
    inceleme. Maliyeti ise dogrudan hacimle carpiliyor:

        olculen oran            90,6 satir/sn  (8 cihaz, yuk testi)
        satir basina            ~875 bayt      (indeksler dahil)
        24 saatte               7,8 milyon satir  ~6,5 GB
        1 saatte                  326 bin satir    ~272 MB

    Test cihazinda 8,9 GB bos disk vardi; 24 saatlik varsayilan sureklu yuk
    altinda diski ~1,5 gunde dolduruyordu. 600 cihaz hedefinde ayni hesap
    ~490 GB veriyor, yani varsayilan olcekle birlikte patliyordu.

    1 saat, gateway yeniden gonderim penceresi icin fazlasiyla yeterli
    (retry'lar saniyeler mertebesinde). Ayrica tuketici tarafinda BAGIMSIZ
    bir idempotency defteri var (`processed_messages`, kendi saklama
    suresiyle) — dedup'in tek dayanagi bu tablo DEGIL.

    Adli inceleme icin uzun sure isteyen kurulum `OUTBOX_PUBLISHED_RETENTION_HOURS`
    ile yukseltebilir; disk butcesini de birlikte hesaplamali.
    """
    try:
        return max(1, int(os.getenv("OUTBOX_PUBLISHED_RETENTION_HOURS", "1")))
    except ValueError:
        return 1


def _purge_interval_sec() -> float:
    try:
        return max(1.0, float(os.getenv("OUTBOX_PURGE_INTERVAL_SEC", "10")))
    except ValueError:
        return 10.0


def _purge_batch() -> int:
    try:
        return max(100, int(os.getenv("OUTBOX_PURGE_BATCH", "10000")))
    except ValueError:
        return 10_000


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
        last_purge = 0.0
        while not self._stop.is_set():
            try:
                db = SessionLocal()
                try:
                    published = flush_outbox(db, limit=batch)
                finally:
                    db.close()
                consecutive_errors = 0

                # published=True outbox satirlari sinirsiz buyumesin: saklama
                # suresinden eskiyi 10K batch sil (bkz. _retention_hours).
                # Sahada 1.7M published row UNIQUE/index sorgularini
                # yavaslatiyordu. published=False ASLA silinmez.
                # Backlog surekli dolu olsa bile interval geldiginde purge calisir;
                # aksi halde `continue` retention'i sonsuza dek acliktan birakir.
                now_mono = time.monotonic()
                if now_mono - last_purge >= _purge_interval_sec():
                    purge_db = SessionLocal()
                    try:
                        removed = purge_published_outbox(
                            purge_db,
                            before=datetime.now(timezone.utc) - timedelta(
                                hours=_retention_hours()
                            ),
                            limit=_purge_batch(),
                        )
                    finally:
                        purge_db.close()
                    last_purge = now_mono
                    if removed:
                        logger.info("outbox_published_purged count=%d", removed)
                # Flush batch tamamen doluysa publish backlog var; retention
                # kontrolu yapildi, simdi uyumadan sonraki publish batch'e gec.
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
