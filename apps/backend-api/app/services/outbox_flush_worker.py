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
  OUTBOX_PUBLISHED_RETENTION_MINUTES default 15    (published=True saklama)
                                     (eski ..._HOURS geriye uyumlu okunur)
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


#: NATS yeniden teslim penceresi: ack_wait(60sn) x nats_worker_max_deliver(10).
#: Saklama sureleri bunun ALTINA inemez — inerse ayni mesaj yeniden teslim
#: edildiginde dedup kaydi silinmis olur.
REDELIVERY_WINDOW_SEC = 600


def _retention_sec() -> float:
    """Yayinlanmis outbox satirlarinin saklama suresi (saniye).

    VARSAYILAN 15 DAKIKA — gerekcesi TAHMIN DEGIL, olculen pencereden turetildi.

    BU PENCERE NEYI KORUYOR
    -----------------------
    `dedup_key` UNIQUE ve ekleme `ON CONFLICT DO NOTHING` yapiyor; satir
    durdugu surece ayni `message_id` tekrar kuyruga girmez. Yani pencere =
    tekrar-yayin koruma penceresi.

    AMA TEK KORUMA BU DEGIL. Tuketici tarafinda bagimsiz bir defter var
    (`processed_messages`, kendi saklama suresiyle) ve kayit yazilmadan once
    ORASI kontrol ediliyor. Dolayisiyla bu pencereyi kisaltmanin bedeli CIFT
    KAYIT DEGIL, bosa giden bir yayin.

    `published=false` satirlar saklama suresinden BAGIMSIZ olarak hic
    silinmiyor — teslim edilmemis bir olay, sure ne olursa olsun kaybolmaz.

    NEDEN 15 DAKIKA
    ---------------
    Gercek yeniden teslim penceresi 10 dakika (REDELIVERY_WINDOW_SEC); 15
    dakika ona %50 pay birakir. Olculen 296 satir/sn oraninda:

        1 saat   -> ~1,07 milyon satir
        15 dakika->   ~266 bin satir

    NOT: saklama suresi tablonun BOYUTUNU sinirlar, YAZMA HIZINI degil. Her
    okuma yine INSERT + UPDATE + DELETE olarak uc kez yaziliyor; CPU ve WAL
    yuku oradan geliyor ve bu ayar ona dokunmuyor.
    """
    # Yeni ayar dakika biriminde. Eski `..._HOURS` degiskeni GERIYE UYUMLU
    # okunuyor: sahada .env'ine onu yazmis kurulumlar bozulmasin.
    ham = os.getenv("OUTBOX_PUBLISHED_RETENTION_MINUTES")
    if ham is not None and ham.strip():
        try:
            dk = float(ham)
        except ValueError:
            dk = 15.0
    else:
        eski = os.getenv("OUTBOX_PUBLISHED_RETENTION_HOURS")
        if eski is not None and eski.strip():
            try:
                dk = float(eski) * 60.0
            except ValueError:
                dk = 15.0
        else:
            dk = 15.0
    # Yeniden teslim penceresinin ALTINA inilmesine izin verilmiyor: o
    # noktada dedup korumasi mesaj hala yeniden teslim edilebilirken
    # kalkar ve bosa giden yayinlar duplicate riskine donusur.
    return max(float(REDELIVERY_WINDOW_SEC), dk * 60.0)


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
                                seconds=_retention_sec()
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
