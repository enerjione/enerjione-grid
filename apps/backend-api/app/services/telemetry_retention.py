"""Telemetri + idempotency retention worker.

Iki tablonun zaman bazli temizligi:
  1. `telemetry` — son N dakikalik kayan pencere (canli deger / kisa trend)
  2. `processed_messages` — idempotency UNIQUE check icin tutuluyor; ayni
     `message_id` 7 gun icinde tekrar gelirse duplicate olarak ele alinir.
     Daha eski mesajlarin yeniden gelme ihtimali (RabbitMQ persistent + outbox
     ile bile) cok dusuktur; 7 gun guvenli marja.

Tablonun sinirsiz buyumesi production'da en buyuk risklerden biri:
  * 600 cihaz × ~30 sinyal degisimi/dk × 60 dk × 24 saat = 26M row/gun
  * 30 gun: 780M row, ~50 GB
  * Idempotency check her telemetry mesaji icin tabloya SELECT yapar — tablo
    buyudukce her sorgu yavaslayarak cascade gecikmeler olusturur.

Konfigurasyon (env):
  TELEMETRY_RETENTION_MINUTES         default 30
  TELEMETRY_RETENTION_INTERVAL_SEC    default 300  (5 dk)
  PROCESSED_MESSAGES_RETENTION_DAYS   default 7
  PROCESSED_MESSAGES_INTERVAL_SEC     default 3600 (1 saat)

Production'da konservatif degerler — hot-loop'ta gereksiz lock contention
olusturmamak icin saatlik calisir, batch'de DELETE LIMIT'siz (PostgreSQL'in
HOT update + autovacuum'i halleder).
"""

from __future__ import annotations

import logging
import os
import threading
from datetime import datetime, timedelta, timezone

from sqlalchemy import delete, text  # `delete` processed_messages purge'unde kullaniliyor

from app.db.session import SessionLocal
from app.models.processed_message import ProcessedMessage
from app.models.telemetry import Telemetry

logger = logging.getLogger(__name__)


def _telemetry_retention_minutes() -> int:
    raw = os.getenv("TELEMETRY_RETENTION_MINUTES", "30")
    try:
        n = int(raw)
        return max(1, n)
    except ValueError:
        return 30


def _telemetry_interval_sec() -> int:
    raw = os.getenv("TELEMETRY_RETENTION_INTERVAL_SEC", "300")
    try:
        n = int(raw)
        return max(30, n)
    except ValueError:
        return 300


def _processed_messages_retention_days() -> int:
    raw = os.getenv("PROCESSED_MESSAGES_RETENTION_DAYS", "7")
    try:
        n = int(raw)
        return max(1, n)
    except ValueError:
        return 7


def _processed_messages_interval_sec() -> int:
    raw = os.getenv("PROCESSED_MESSAGES_INTERVAL_SEC", "3600")
    try:
        n = int(raw)
        return max(60, n)
    except ValueError:
        return 3600


class TelemetryRetentionWorker:
    """Arka plan thread'i; periyodik DELETE çalıştırır.

    Hem `telemetry` (sik, kisa pencere) hem `processed_messages` (yavas, uzun
    pencere) tablolarini temizler. Iki ayri timer; iki farkli interval. Her
    biri kendi periodu geldiginde tetiklenir.
    """

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
            "telemetry_retention_started telemetry_keep_minutes=%d "
            "telemetry_interval_sec=%d processed_messages_keep_days=%d "
            "processed_messages_interval_sec=%d",
            _telemetry_retention_minutes(),
            _telemetry_interval_sec(),
            _processed_messages_retention_days(),
            _processed_messages_interval_sec(),
        )

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=5)
            self._thread = None

    def _run(self) -> None:
        # Acilis sonrasi hemen telemetry temizle; processed_messages bir
        # sonraki tetik anina kadar bekler. monotonic timer ile kac saniye
        # geçtiyse o tetigi calistirsin.
        last_telemetry_purge = 0.0
        last_processed_purge = 0.0
        # Initial run flag — backend acilir acilmaz mevcut buyukluk hakkinda
        # bilgi vermek icin hemen bir kez bakalim
        first_iteration = True
        while not self._stop.is_set():
            now_mono = _monotonic()
            try:
                if first_iteration or now_mono - last_telemetry_purge >= _telemetry_interval_sec():
                    self._purge_telemetry()
                    last_telemetry_purge = now_mono
            except Exception:  # noqa: BLE001
                logger.exception("telemetry_retention_purge_failed")

            try:
                if first_iteration or now_mono - last_processed_purge >= _processed_messages_interval_sec():
                    self._purge_processed_messages()
                    last_processed_purge = now_mono
            except Exception:  # noqa: BLE001
                logger.exception("processed_messages_retention_purge_failed")

            first_iteration = False
            # Iki interval'in en kucugu kadar bekle (saniye granularitesi)
            sleep_s = min(_telemetry_interval_sec(), _processed_messages_interval_sec())
            self._stop.wait(max(30, sleep_s // 2))

    def _purge_telemetry(self) -> int:
        """Eski telemetri row'larini siler — AMA her (device_id, signal_key)
        icin EN SON row'u her zaman korur.

        Eski davranis: timestamp < cutoff olan tum row'lar silinir. Bir sinyal
        30 dakikadir degismiyorsa son row'u da silinir, frontend canli
        degerler tablosunda bos kalir. Kullanici "değer kayboluyor" diye
        bildirdi.

        Yeni davranis: window function ile her sinyalin id-DESC sirasindaki
        ilk row'unu (ROW_NUMBER=1) hicbir zaman silmeyiz. Geri kalan eski
        row'lar (ROW_NUMBER>=2) cutoff'tan onceyse silinir. Boylece her
        sinyalin son degeri DB'de kalici, retention sadece tarihsel veriyi
        temizler.

        SQL (PostgreSQL):
          DELETE FROM telemetry t USING (
            SELECT id FROM (
              SELECT id, source_timestamp,
                     ROW_NUMBER() OVER (
                       PARTITION BY device_id, signal_key
                       ORDER BY id DESC
                     ) AS rn
              FROM telemetry
            ) sub
            WHERE rn > 1 AND source_timestamp < :cutoff
          ) old
          WHERE t.id = old.id
        """
        cutoff = datetime.now(timezone.utc) - timedelta(minutes=_telemetry_retention_minutes())
        db = SessionLocal()
        try:
            result = db.execute(
                text(
                    """
                    DELETE FROM telemetry t
                    USING (
                        SELECT id
                        FROM (
                            SELECT id, source_timestamp,
                                   ROW_NUMBER() OVER (
                                       PARTITION BY device_id, signal_key
                                       ORDER BY id DESC
                                   ) AS rn
                            FROM telemetry
                        ) sub
                        WHERE rn > 1 AND source_timestamp < :cutoff
                    ) old
                    WHERE t.id = old.id
                    """
                ),
                {"cutoff": cutoff},
            )
            db.commit()
            removed = int(getattr(result, "rowcount", 0) or 0)
            if removed > 0:
                logger.info(
                    "telemetry_retention_purged removed=%d cutoff=%s "
                    "(her sinyalin son row'u korundu)",
                    removed,
                    cutoff.isoformat(),
                )
            return removed
        finally:
            db.close()

    def _purge_processed_messages(self) -> int:
        """Eski idempotency kayitlarini siler. PostgreSQL'in autovacuum'i
        sonrasi yer geri kazanir. Her gun ~26M row eklendigi icin bu cleanup
        kritik."""
        cutoff = datetime.now(timezone.utc) - timedelta(days=_processed_messages_retention_days())
        db = SessionLocal()
        try:
            result = db.execute(
                delete(ProcessedMessage).where(ProcessedMessage.processed_at < cutoff)
            )
            db.commit()
            removed = int(getattr(result, "rowcount", 0) or 0)
            if removed > 0:
                logger.info(
                    "processed_messages_retention_purged removed=%d cutoff=%s",
                    removed,
                    cutoff.isoformat(),
                )
            return removed
        finally:
            db.close()


def _monotonic() -> float:
    """time.monotonic() wrapper; test edilebilirlik icin."""
    import time as _time

    return _time.monotonic()


_worker = TelemetryRetentionWorker()


def start() -> None:
    _worker.start()


def stop() -> None:
    _worker.stop()
