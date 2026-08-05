"""FTP olaylarini backend'e bildirir — kim baglandi, hangi dosyayi yazdi/aldi.

NEDEN SADECE LOG DEGIL
----------------------
Cihaz kendi yapilandirmasini `<seri>_Configuration.csv` adiyla yazar. Dosya
adindaki seri, cihazi TANIMLAR (`master.serial_number` telemetrisiyle eslesir).
Yani bu olaylari yakalamak yalnizca "kim ne yapti" gorunurlugu degil, config
CEKME AKISINI TAMAMLAYAN adimdir: cihaz dosyayi yazar, backend olayi alir,
dosyayi okuyup yeni bir yapilandirma surumu olarak kaydeder.

NEDEN AYRI THREAD + KUYRUK
--------------------------
pyftpdlib `asyncore` tabanlidir ve TEK THREAD'de calisir. Callback icinde
dogrudan HTTP istegi yapmak, istek suresince TUM FTP SUNUCUSUNU bloklar:
backend yavaslarsa ya da yanit vermezse cihazlar baglanamaz, transferler
durur. Ayni sinif hatayi alarm-service'te yasadik (backend HTTP'si event
loop'u blokluyordu).

Bu yuzden callback yalnizca kuyruga yazar (O(1), bloklamaz); gonderimi ayri
bir daemon thread yapar.

KUYRUK SINIRLI
--------------
Backend uzun sure erisilemezse kuyruk sinirsiz buyuyup bellegi tuketirdi.
Ust sinira ulasinca EN ESKI olay atilir ve bu DURUM DEGISIMI olarak bir kez
loglanir — her atilan olayi loglamak, log'u kendi kendine dolduran ikinci bir
sorun olurdu.
"""

from __future__ import annotations

import json
import logging
import queue
import threading
import urllib.error
import urllib.request
from datetime import datetime, timezone
from typing import Any

# `requests` DEGIL, stdlib `urllib`: bu servisin tek bagimliligi pyftpdlib ve
# tek bir POST icin ikinci bir paket eklemek gereksiz. Kurulum yuzeyi kucuk
# kalsin (saha cihazinda her paket bir guncelleme/CVE yuzeyi demek).
log = logging.getLogger("ftp-server.reporter")

#: Kuyruk tavani. Olaylar kucuk (birkac yuz bayt); 5000 olay birkac MB eder
#: ve gunlerce suren bir backend kesintisini tasiyacak kadar genistir.
MAX_QUEUE = 5000

#: Tek istek zaman asimi. Uzun tutmak, kuyrugun arkasini biriktirmekten baska
#: ise yaramaz — backend yoksa yoktur.
TIMEOUT_SEC = 5


class EventReporter:
    """FTP olaylarini kuyruga alip arka planda backend'e gonderir."""

    def __init__(self, *, backend_url: str, service_token: str) -> None:
        self._url = backend_url.rstrip("/") + "/internal/ftp-events"
        self._token = service_token
        self._q: queue.Queue[dict[str, Any]] = queue.Queue(maxsize=MAX_QUEUE)
        self._headers = {
            "X-Service-Token": service_token,
            "X-Service-Name": "ftp-server",
            "Content-Type": "application/json",
        }
        # Kuyruk tasmasi DURUM DEGISIMI olarak loglanir, her olayda degil.
        self._dropping = False
        self._enabled = bool(backend_url and service_token)
        if not self._enabled:
            # Yapilandirilmamis olmak bir HATA DEGIL: FTP sunucusu tek basina
            # da calisabilmeli. Ama sessiz kalmaz — kullanici neden olay
            # gormedigini bilmeli.
            log.warning(
                "FTP olay bildirimi KAPALI (E1_BACKEND_URL / INTERNAL_SERVICE_TOKEN "
                "verilmemis). Dosya transferleri calisir ama arayuzde gorunmez."
            )
            return

        self._thread = threading.Thread(
            target=self._run, name="ftp-event-reporter", daemon=True
        )
        self._thread.start()

    # -- uretici tarafi (FTP callback'lerinden cagrilir) --------------------
    def report(self, event: str, **alanlar: Any) -> None:
        """Olayi kuyruga koyar. ASLA bloklamaz, ASLA istisna firlatmaz.

        Callback icinden cagrildigi icin buradan cikan bir istisna FTP
        oturumunu dusururdu — bildirim, asil isi bozmamali.
        """
        if not self._enabled:
            return
        kayit = {
            "event": event,
            "occurred_at": datetime.now(timezone.utc).isoformat(),
            **alanlar,
        }
        try:
            self._q.put_nowait(kayit)
            if self._dropping:
                log.info("FTP olay kuyrugu normale dondu.")
                self._dropping = False
        except queue.Full:
            # EN ESKIYI at, yenisini koy: son olaylar teshis icin daha degerli.
            try:
                self._q.get_nowait()
                self._q.put_nowait(kayit)
            except (queue.Empty, queue.Full):  # pragma: no cover
                pass
            if not self._dropping:
                log.warning(
                    "FTP olay kuyrugu doldu (%d) — en eski olaylar atiliyor. "
                    "Backend erisilemiyor olabilir.",
                    MAX_QUEUE,
                )
                self._dropping = True
        except Exception:  # noqa: BLE001
            log.debug("FTP olayi kuyruga alinamadi", exc_info=True)

    # -- tuketici tarafi ---------------------------------------------------
    def _run(self) -> None:
        while True:
            kayit = self._q.get()
            istek = urllib.request.Request(
                self._url,
                data=json.dumps(kayit).encode("utf-8"),
                headers=self._headers,
                method="POST",
            )
            try:
                with urllib.request.urlopen(istek, timeout=TIMEOUT_SEC):
                    pass
            except urllib.error.HTTPError as exc:
                # Yeniden DENEMIYORUZ: FTP olayi bir olcumdur, kaybi
                # katlanilabilir. Yeniden deneme kuyrugu buyutur ve backend'i
                # toparlanirken yeniden bogar.
                log.warning(
                    "FTP olayi reddedildi (HTTP %s): %s", exc.code, kayit.get("event")
                )
            except Exception as exc:  # noqa: BLE001
                log.debug("FTP olayi gonderilemedi: %s", exc)
