"""Asili kalan servisi kendi kendine sonlandiran bekci.

NEDEN GEREKLI
-------------
`restart: unless-stopped` yalnizca CIKIS yapan sureci geri kaldirir. Ana
dongusu kilitlenen bir worker "calisiyor" gorunur: container ayakta, surec
ayakta, ama SCADA'ya veri sessizce akmayi birakmis olur. Baslarinda kimsenin
olmadigi bir saha cihazinda bu, fark edilmesi en zor ariza.

Docker healthcheck TEK BASINA yetmez: unhealthy bir container'i YENIDEN
BASLATMAZ, yalnizca isaretler. Kendiliginden toparlanma icin surecin
kendisinin cikmasi gerekir.

NASIL
-----
Ana dongu duzenli olarak `kalp_at()` cagirir. Bekci ipligi son atistan bu
yana gecen sureyi olcer; esik asilirsa sureci HARD EXIT ile sonlandirir ve
restart politikasi servisi geri kaldirir.

ATIS NEYE BAGLANMALI
--------------------
"Mesaj isledim"e DEGIL, "olay dongum hala calisiyor"a. Aksi halde mesaj
gelmeyen sakin bir gecede saglikli servis oldurulurdu — ve bu, cozdugumuz
sorundan daha kotu bir ariza olurdu.

BU SERVISTE ATIS pyftpdlib IOLOOP'UNA BAGLI (bkz. main.py):
Atis, `ioloop.call_every` ile dongunun KENDISINE zamanlanir. Dongu donuyorsa
atis gelir; `serve_forever` icinde bir sey kilitlenirse zamanlanmis cagri hic
calismaz ve bekci devreye girer. Bu olcum TRAFIKTEN BAGIMSIZDIR: ioloop
soketleri zaman asimiyla yoklar, yani hicbir cihaz baglanmasa da doner.
Dolayisiyla sessiz bir gecede yanlis yere restart uretmez.

`os._exit` BILINCLI: normal `sys.exit` temizlik yollarindan gecer ve asili
kalan sey tam da o yollardan biri olabilir; o zaman bekci de asilir.
"""

from __future__ import annotations

import logging
import os
import time
from threading import Thread

logger = logging.getLogger(__name__)

_son_atis: float = time.monotonic()
_esik_sn: float = 0.0


def kalp_at() -> None:
    """Ana dongu tarafindan cagrilir: 'hala donuyorum'."""
    global _son_atis
    _son_atis = time.monotonic()


def gecen_sn() -> float:
    return time.monotonic() - _son_atis


def saglikli(esik_sn: float | None = None) -> bool:
    """Healthcheck ucu bunu kullanir; bekciden BAGIMSIZ calisir."""
    sinir = esik_sn if esik_sn is not None else (_esik_sn or float("inf"))
    return gecen_sn() <= sinir


def baslat(esik_sn: float, *, servis: str, kontrol_sn: float = 5.0) -> None:
    """Bekciyi baslatir. `esik_sn` ana dongunun EN UZUN sessiz kalabilecegi
    sureden bol olmali — dar esik saglikli servisi oldurur."""
    global _esik_sn
    _esik_sn = esik_sn
    kalp_at()

    def izle() -> None:
        while True:
            time.sleep(kontrol_sn)
            gecen = gecen_sn()
            if gecen > esik_sn:
                logger.critical(
                    "%s ana dongusu %.0f sn'dir atis yapmadi (esik %.0f sn) — "
                    "surec sonlandiriliyor, restart politikasi geri kaldiracak.",
                    servis, gecen, esik_sn,
                )
                # Log'un diske dusmesine firsat ver; hemen cikmak son satiri
                # kaybettirebiliyor ve o satir teshisin tek kaynagi.
                for h in logging.getLogger().handlers:
                    try:
                        h.flush()
                    except Exception:  # noqa: BLE001
                        pass
                os._exit(1)

    Thread(target=izle, name="watchdog", daemon=True).start()
    logger.info("Bekci calisiyor: %s, esik %.0f sn", servis, esik_sn)
