"""Bildirimi gonderilmemis hat arizalarini periyodik olarak sureklestirir.

YASANAN ARIZA
-------------
Hat arizasi acildi, e-posta ve WhatsApp grubu ACIKTI, hicbir bildirim
gitmedi. Sebep tek bir hata degil, bir ZINCIR KOPUKLUGU:

  1. `fault_recompute_service` arizayi acar ama SMTP/HTTP YAPMAZ. Bu
     bilincli: yanit vermeyen bir relay ariza motorunu tutuyor ve ariza
     kaydi COMMIT EDILMEDEN asili kaliyordu. Bu yuzden satir ici gonderim
     `notification_inline_dispatch_enabled` bayragina baglandi ve bayrak
     production'da VARSAYILAN OLARAK KAPALI.

  2. Gonderim sorumlulugu `dispatch_pending_fault_notifications`e gecti;
     ama onu cagiran TEK yer `/internal/notifications/dispatch/{alarm_id}`,
     yani notification-worker'in ALARM yolu.

  3. Ariza kaydini olusturan `recompute_faults_debounced` COALESCING yapar:
     in-flight ise ya da minimum aralik dolmamissa `False` doner ve
     hesaplamayi SONRAKI tetige birakir.

Sonuc: arizayi doguran alarmin dispatch'i, ariza satiri HENUZ YOKKEN kosar
(`pending` bos doner) ve `processed_messages`e islenmis olarak damgalanir.
Debounce daha sonra arizayi acar — ama artik onu gonderecek kimse yoktur.
Yeni bir alarm dusmedigi surece ariza sonsuza dek `notified_at IS NULL`
kalir. Tekil bir arizada (tam da en kritik durumda) bildirim HIC gitmez.

BU WORKER O BOSLUGU KAPATIR: alarm akisindan BAGIMSIZ olarak bekleyen
arizalari tarar ve gonderir. Alarm yolu hala calisiyor (hizli yol); burasi
son savunma hatti. `notified_at` damgasi iki yolun ayni arizayi iki kez
gondermesini engeller.

Konfigurasyon:
  FAULT_NOTIFY_SWEEP_SEC  default 60 (sn) — tarama araligi.
"""

from __future__ import annotations

import logging
import os
import threading

from app.db.session import SessionLocal
from app.services.notification_dispatch_service import (
    dispatch_pending_fault_notifications,
)

logger = logging.getLogger(__name__)


def _tick_sec() -> int:
    """Tarama araligi.

    Alt sinir 15 sn: ariza bildirimi sahaya ekip cikarir, dakikalarca
    beklemek kabul edilemez. Ust sinira gerek yok — operator uzun bir deger
    verirse bu bilincli bir tercihtir.
    """
    raw = os.getenv("FAULT_NOTIFY_SWEEP_SEC", "60")
    try:
        return max(15, int(raw))
    except ValueError:
        return 60


class FaultNotifySweeper:
    def __init__(self) -> None:
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._run, name="fault-notify-sweeper", daemon=True
        )
        self._thread.start()
        logger.info("fault_notify_sweeper_started tick_sec=%d", _tick_sec())

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=5)
            self._thread = None

    def _run(self) -> None:
        # Acilista kisa bekleme: DB/migration hazir olsun ve alarm yolunun
        # hizli gonderimi once denesin (duplicate degil, sira meselesi —
        # `notified_at` zaten tek gonderimi garantiliyor).
        self._stop.wait(20)
        while not self._stop.is_set():
            try:
                self._sweep()
            except Exception:  # noqa: BLE001
                logger.exception("fault_notify_sweep_failed")
            self._stop.wait(_tick_sec())

    def _sweep(self) -> None:
        db = SessionLocal()
        try:
            sent = dispatch_pending_fault_notifications(db)
            if sent:
                # Damgalar (`notified_at`) bu commit ile kalici olur; commit
                # edilmezse ayni ariza her turda yeniden gonderilirdi.
                db.commit()
                logger.info("fault_notify_sweeper_dispatched count=%d", sent)
            else:
                # Bekleyen yoksa bile oturumu temiz birak.
                db.rollback()
        finally:
            db.close()


_sweeper = FaultNotifySweeper()


def start() -> None:
    _sweeper.start()


def stop() -> None:
    _sweeper.stop()
