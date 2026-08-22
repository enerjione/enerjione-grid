"""Gateway için yeni sürüm çıktığında bir kez bildirim gönder.

NASIL TESPİT EDİLİYOR
---------------------
Karşılaştırma DIGEST üzerinden yapılır. (Bu satır bir zamanlar "gateway
imajı `:latest` etiketine sabit, sürüm numarası yok" diyordu; artık öyle
değil — imaj onaylı sürümün digest'ine sabitleniyor, bkz.
`gateway_release_policy`. Digest karşılaştırması yine de doğru araç:
etiketin işaret ettiği manifest değiştiyse yeni bir imaj yayınlanmıştır.)
Host ajanı iki digest bildiriyor:

    image_digest   — çalışan imajın kayıt defteri digest'i
    remote_digest  — etiketin kayıt defterindeki şu anki digest'i

İkisi farklıysa etiket yeni bir imaja işaret ediyor demektir.

`update_available` **üç durumlu**: `None` = bilinmiyor (kayıt defterine
ulaşılamadı). `False` ile aynı sayılmıyor — "güncel" demek, sormadan
verilmiş bir iddia olurdu.

NEDEN "BİR KEZ"
---------------
Kontrol periyodik çalışıyor. Her turda bildirim göndermek, yeni sürüm
çıktıktan sonra operatör güncelleyene kadar dakikada bir bildirim demekti;
bildirim merkezi kullanılamaz hale gelir ve gerçek uyarılar kaybolur.

Bu yüzden gönderim **digest bazında** hatırlanıyor: aynı hedef digest için
ikinci bir bildirim gitmez. Yeni bir sürüm daha çıkarsa digest değişir ve
bildirim yeniden gider.

Hatırlama `system_events` üzerinden — süreç içi bir değişken restart'ta
sıfırlanır ve backend her yeniden başladığında aynı bildirim tekrar giderdi.
"""

from __future__ import annotations

import logging
import os
import threading

from sqlalchemy import select

from app.db.session import SessionLocal
from app.models.system_event import SystemEvent
from app.services import gateway_agent_service, gateway_release_service
from app.services.event_service import record_event
from app.services.notification_service import create_notification

logger = logging.getLogger(__name__)

#: Kontrol periyodu (saniye). Ajanin kendi uzak digest onbellegi 15 dakika,
#: daha sik sormanin karsiligi yok.
DEFAULT_INTERVAL_SEC = 900.0

#: Bildirimin gonderildigini isaretleyen olay tipi.
EVENT_TYPE = "gateway_update_notified"


def _interval_sec() -> float:
    try:
        return max(60.0, float(os.getenv("GATEWAY_UPDATE_CHECK_INTERVAL_SEC", "900")))
    except ValueError:
        return DEFAULT_INTERVAL_SEC


def _already_notified(db, gateway_code: str, digest: str) -> bool:
    """Bu gateway + bu hedef digest icin bildirim gonderilmis mi?"""
    kayit = db.scalar(
        select(SystemEvent.id)
        .where(
            SystemEvent.event_type == EVENT_TYPE,
            SystemEvent.message.like(f"%{gateway_code}%"),
            SystemEvent.message.like(f"%{digest[:19]}%"),
        )
        .limit(1)
    )
    return kayit is not None


def check_once(db) -> int:
    """Guncelleme bekleyen gateway'ler icin bildirim uretir; adet doner."""
    durum = gateway_agent_service.read_status()
    if not durum.available:
        return 0
    # UZAK SURUMU BACKEND'DEN DE SOR: ajanin sorgusu `docker buildx`e bagli
    # ve buildx cogu Docker Engine kurulumunda yok. O cihazlarda
    # `update_available` kalici olarak None kaliyor ve bu dongu HICBIR
    # bildirim uretmiyordu — yeni surum yayinlandigi halde operator hic
    # haberdar olmuyordu (2026-08-11 saha bulgusu). Zenginlestirme ilk turda
    # arka plan sorgusunu baslatir, sonuc bir sonraki turda kullanilir.
    durum = gateway_release_service.enrich_agent_status(durum)

    gonderilen = 0
    for gw in durum.gateways:
        # UC DURUM: None (bilinmiyor) bildirim URETMEZ. Yalnizca kesin
        # olarak "yeni surum var" bilgisi bildirime donusur.
        if gw.update_available is not True:
            continue
        hedef = (gw.remote_digest or "").strip()
        if not hedef:
            continue
        if _already_notified(db, gw.code, hedef):
            continue

        ad = gw.name or gw.code
        create_notification(
            db,
            # `None` = yayin (broadcast): tum kullanicilara gorunur.
            recipient_username=None,
            title=f"Gateway güncellemesi hazır: {ad}",
            body=(
                f"{ad} ({gw.code}) için yeni bir sürüm yayınlandı. "
                "Mühendislik > Gateway'ler ekranından güncelleyebilirsiniz. "
                "Güncelleme sırasında o gateway'e bağlı cihazlardan kısa süre "
                "telemetri gelmez."
            ),
            category="system",
            severity="info",
            link="/engineering/gateways",
            metadata={
                "gateway_code": gw.code,
                "current_digest": gw.image_digest,
                "available_digest": hedef,
            },
        )
        # Isaret KAYDI — surec ici degisken degil: backend her yeniden
        # baslatildiginda ayni bildirim tekrar giderdi.
        record_event(
            db,
            category="gateway",
            event_type=EVENT_TYPE,
            severity="info",
            message=f"{gw.code} icin guncelleme bildirimi gonderildi ({hedef[:19]})",
            metadata={"gateway_code": gw.code, "available_digest": hedef},
        )
        gonderilen += 1
        logger.info(
            "gateway_update_notified code=%s digest=%s", gw.code, hedef[:19]
        )

    if gonderilen:
        db.commit()
    return gonderilen


class GatewayUpdateNotifier:
    def __init__(self) -> None:
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._run, name="gateway-update-notifier", daemon=True
        )
        self._thread.start()
        logger.info(
            "gateway_update_notifier_started interval=%.0fs", _interval_sec()
        )

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=5)
            self._thread = None

    def _run(self) -> None:
        ardisik_hata = 0
        while not self._stop.is_set():
            try:
                db = SessionLocal()
                try:
                    check_once(db)
                finally:
                    db.close()
                ardisik_hata = 0
            except Exception:  # noqa: BLE001
                ardisik_hata += 1
                if ardisik_hata in (1, 10) or ardisik_hata % 60 == 0:
                    logger.exception(
                        "gateway_update_check_failed ardisik=%d", ardisik_hata
                    )
            self._stop.wait(_interval_sec())


_notifier = GatewayUpdateNotifier()


def start() -> None:
    _notifier.start()


def stop() -> None:
    _notifier.stop()
