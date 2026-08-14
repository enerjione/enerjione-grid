"""Kritik altyapi olaylarini periyodik tarayip bildirime cevirir.

NEDEN AYRI BIR SUREC/IPLIK — YALITIM
------------------------------------
Bildirim gonderimi ag I/O'sudur (SMTP, HTTP). Bunu olayi URETEN yolun
icine koymak, yanit vermeyen bir relay'in disk_guard'i, telemetri
tuketicisini ya da yedek zamanlayicisini bloke etmesi demekti. Bu depoda
ayni hata bir kez yasandi (`fault_recompute_service` satir ici gonderimi
arizayi COMMIT EDILMEDEN asili birakiyordu) ve satir ici gonderim bilerek
kapatildi.

Burada gonderim, olay YAZILDIKTAN VE COMMIT EDILDIKTEN SONRA, ayri bir
iplikte ve ayri bir DB oturumunda yapilir. `record_event` DEGISMEDI: hicbir
uretici bu modulu cagirmaz, bilmez bile. Bildirim altyapisi tamamen coksa
bile olay kaydi yazilmaya, disk_guard temizlik yapmaya, telemetri akmaya
devam eder.

`fault_notify_sweeper` ile AYNI desen — o da alarm akisindan bagimsiz bir
son savunma hattidir.

Konfigurasyon:
  INFRA_NOTIFY_SWEEP_SEC          tarama araligi (varsayilan 60 sn)
  INFRA_NOTIFY_COOLDOWN_MINUTES   ayni olay+kaynak icin soguma (15 dk)
  INFRA_NOTIFY_LOOKBACK_MINUTES   taramanin kapsadigi pencere (60 dk)
"""

from __future__ import annotations

import logging
import threading

from app.core.config import settings
from app.db.session import SessionLocal
from app.services.infra_notification_service import (
    dispatch_pending_infra_notifications,
)

logger = logging.getLogger(__name__)


def _tick_sec() -> int:
    """Tarama araligi.

    Alt sinir 15 sn: kritik altyapi uyarisi dakikalarca beklememeli.
    Ust sinir yok — operator uzun bir deger verirse bu bilincli bir tercih.
    """
    try:
        return max(15, int(settings.infra_notify_sweep_sec))
    except (TypeError, ValueError):
        return 60


class InfraNotifySweeper:
    def __init__(self) -> None:
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        if not settings.infra_notify_enabled:
            # Acil kill switch. Bildirim firtinasi ya da yanlis
            # yapilandirilmis bir kanal sahayi rahatsiz ederse operator
            # `.env`den kapatabilsin; olay kaydi (denetim izi) etkilenmez.
            logger.info(
                "infra_notify_sweeper_disabled — INFRA_NOTIFY_ENABLED=false; "
                "kritik altyapi olaylari YALNIZCA Olaylar ekraninda gorunur"
            )
            return
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._run, name="infra-notify-sweeper", daemon=True
        )
        self._thread.start()
        logger.info(
            "infra_notify_sweeper_started tick_sec=%d cooldown_min=%d",
            _tick_sec(),
            settings.infra_notify_cooldown_minutes,
        )

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=5)
            self._thread = None

    def _run(self) -> None:
        # Acilista bekle: sema hazir olsun ve yeniden baslatma sirasinda
        # gecici olarak uretilen olaylar (baglanti kopmasi vb.) oturana
        # kadar bildirim gitmesin.
        self._stop.wait(45)
        while not self._stop.is_set():
            try:
                self._sweep()
            except Exception:  # noqa: BLE001
                # Tur asla dis dunyaya sizmaz: bildirim altyapisinin arizasi
                # arka plan surecini dusurmemeli.
                logger.exception("infra_notify_sweep_failed")
            self._stop.wait(_tick_sec())

    def _sweep(self) -> None:
        db = SessionLocal()
        try:
            gonderilen = dispatch_pending_infra_notifications(db)
            # Soguma damgalari bu commit ile kalici olur; commit edilmezse
            # ayni olay her turda yeniden bildirilirdi.
            db.commit()
            if gonderilen:
                logger.info("infra_notify_sweeper_dispatched count=%d", gonderilen)
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()


_sweeper = InfraNotifySweeper()


def start() -> None:
    _sweeper.start()


def stop() -> None:
    _sweeper.stop()
