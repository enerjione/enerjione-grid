"""Sinyal katalogundan HAT SINIFI cikarir: oncelikli hat mi, toplu hat mi.

NEDEN VAR
---------
Butun olcumler TEK bir konudan aktigi surece, saniyede binlerce ANALOG
okumasiyla bir ARIZA BAYRAGI ayni sirada bekler. Ariza gostergesinde gecikme
bir konfor meselesi degil DOGRULUK meselesidir: 10 dakika gec gorulen ariza
operasyonel olarak KACIRILMISTIR.

Bu modul her sinyalin hangi hatta akacagini soyler. Karar KATALOGDAN gelir
(`GET /api/v1/internal/signals` -> `data_type` + `dnp3_class`); kodda sinyal
adi listesi YOKTUR. Katalog operator tarafindan degistirilebildigi icin
(frontend `SignalEditModal`) otoriter kaynak `signal_catalog` TABLOSUDUR,
depodaki JSON dosyasi degil.

KURAL — ve neden bu kadar muhafazakar
-------------------------------------
TOPLU hatta yalnizca "surekli olcum" gider:
    data_type == "analog"  VE  dnp3_class == Class 2
BASKA HER SEY oncelikli hatta gider. Bunun icine giren kume:
  * binary / counter        -> ariza bayraklari, ariza yonu, ariza sayaclari
  * Class 1 olan ANALOGLAR  -> `fault_current`, `fault_duration`,
    `last_good_known_current`, `test_point_level`. Saf `data_type` ile
    bolseydik ariza BAYRAGI oncelikli hatta, o arizanin BUYUKLUGU toplu
    hatta duserdi; ekranda "ariza var ama akim bilinmiyor" olusurdu.
  * `string` (dnp3_class = "-") ve katalogda OLMAYAN / tipi taninmayan her
    sey -> siniflandiramadigimiz bir sinyali GECIKTIRMEK, gereksiz
    hizlandirmaktan risklidir.

Bugunku katalogda (193 kayit) bu kural 63 sinyali toplu, 130 sinyali
oncelikli hatta koyar. Hacim toplu tarafta: Class 2 analoglar periyodik
taranir, Class 1 binary'ler yalnizca DURUM DEGISTIGINDE uretilir.

KATALOG HENUZ GELMEDIYSE veya backend'e ulasilamiyorsa HER SEY oncelikli
hatta gider. Yani en kotu durum BUGUNKU davranistir (tek hat, karisik
trafik) — sessiz bir gerileme yoktur.

YENI BAGIMLILIK YOK: `urllib.request` stdlib. tag-engine bugun ciplak
(`requirements.txt` tek satir nats-py) ve oyle kalsin.
"""

from __future__ import annotations

import json
import logging
import os
import re
import threading
import urllib.error
import urllib.request
from typing import Any

logger = logging.getLogger("tag-engine")

#: Oncelikli hat — dijital/durum sinyalleri ve ariza belirleyen her sey.
SINIF_ONCELIKLI = "prio"
#: Toplu hat — surekli taranan analog olcumler.
SINIF_TOPLU = "bulk"

#: Toplu hatta DUSEBILECEK tek veri tipi. Liste bilerek TEK elemanli ve
#: "izin verilenler" seklinde: yeni bir tip eklendiginde (ornegin
#: `analog_output`) varsayilan olarak ONCELIKLI hatta duser, sessizce toplu
#: hatta dusmez.
_TOPLU_TIPLER = frozenset({"analog"})
#: Toplu hatta izin veren tek DNP3 sinifi. Class 1 = olay sinifi (cihaz
#: degisimde bildirir), Class 2 = periyodik tarama.
_TOPLU_DNP3_SINIFI = 2

_SINIF_DESENI = re.compile(r"(\d+)")


def _dnp3_sinif_no(ham: Any) -> int | None:
    """"Class 2" / "2" -> 2 ; "-" / None / bos -> None (bilinmiyor)."""
    if ham is None:
        return None
    m = _SINIF_DESENI.search(str(ham))
    return int(m.group(1)) if m else None


def sinif_hesapla(kayit: dict[str, Any] | None) -> str:
    """Tek bir katalog kaydinin hat sinifi. Kayit yoksa ONCELIKLI."""
    if not kayit:
        return SINIF_ONCELIKLI
    veri_tipi = str(kayit.get("data_type") or "").strip().lower()
    if veri_tipi not in _TOPLU_TIPLER:
        return SINIF_ONCELIKLI
    if _dnp3_sinif_no(kayit.get("dnp3_class")) != _TOPLU_DNP3_SINIFI:
        return SINIF_ONCELIKLI
    return SINIF_TOPLU


class KatalogOnbellegi:
    """Sinyal -> hat sinifi tablosu; arka planda periyodik tazelenir.

    OKUMA KILITSIZ: tablo yerinde DEGISTIRILMEZ, her tazelemede YENI bir
    sozluk kurulup referans atomik olarak degistirilir. Boylece `sinif()`
    cagrisi — ki mesaj basina bir kez, olay dongusu uzerinde calisir — hicbir
    kilit beklemez.
    """

    def __init__(
        self,
        *,
        base_url: str,
        service_token: str,
        yenileme_sn: int,
        zaman_asimi_sn: float,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._token = service_token
        self._yenileme_sn = max(5, int(yenileme_sn))
        self._zaman_asimi = float(zaman_asimi_sn)
        self._tablo: dict[str, str] = {}
        self._yuklendi = False
        self._basarisiz_ardisik = 0
        self._durdur = threading.Event()
        self._is_parcacigi: threading.Thread | None = None

    # ---- Okuma -----------------------------------------------------------
    def sinif(self, signal_key: str | None) -> str:
        """Sinyalin hat sinifi. BILINMEYEN HER DURUMDA oncelikli hat."""
        if not signal_key:
            return SINIF_ONCELIKLI
        return self._tablo.get(signal_key, SINIF_ONCELIKLI)

    @property
    def yuklendi(self) -> bool:
        """En az bir kez basariyla cekildi mi? Saglik/log icin."""
        return self._yuklendi

    @property
    def boyut(self) -> int:
        return len(self._tablo)

    # ---- Tazeleme --------------------------------------------------------
    def yenile(self) -> bool:
        """Katalogu bir kez ceker. Basarisizlikta MEVCUT tabloyu KORUR.

        Tabloyu bosaltmak, backend'in kisa bir yeniden baslatmasinda tum
        analog selini oncelikli hatta yikmak olurdu — yani tam da onlemeye
        calistigimiz tikanma. Eski tablo bayat olsa bile dogru tarafta.
        """
        url = f"{self._base_url}/internal/signals"
        istek = urllib.request.Request(
            url,
            headers={
                "X-Service-Token": self._token,
                "X-Service-Name": "tag-engine",
                "Accept": "application/json",
            },
            method="GET",
        )
        try:
            with urllib.request.urlopen(istek, timeout=self._zaman_asimi) as yanit:
                if yanit.status != 200:
                    raise urllib.error.HTTPError(
                        url, yanit.status, "beklenmeyen durum", yanit.headers, None
                    )
                ham = json.loads(yanit.read().decode("utf-8"))
        except Exception as ex:  # noqa: BLE001
            self._basarisiz_ardisik += 1
            # Gurultuyu sinirla: NATS/backend uzun sure yokken her turda log
            # basmak diski doldururdu. Ilkler + her 100'de bir yeter.
            if self._basarisiz_ardisik in (1, 10, 100) or self._basarisiz_ardisik % 100 == 0:
                logger.warning(
                    "katalog-cekilemedi url=%s hata=%s ardisik=%d yuklendi=%s "
                    "(katalog gelene kadar TUM olcumler oncelikli hatta akar)",
                    url,
                    ex,
                    self._basarisiz_ardisik,
                    self._yuklendi,
                )
            return False

        if not isinstance(ham, list):
            logger.warning("katalog-bicimi-beklenmedik tip=%s", type(ham).__name__)
            return False

        yeni: dict[str, str] = {}
        for kayit in ham:
            if not isinstance(kayit, dict):
                continue
            anahtar = kayit.get("key")
            if not anahtar:
                continue
            yeni[str(anahtar)] = sinif_hesapla(kayit)

        onceki_boyut = len(self._tablo)
        # Atomik referans degisimi — bkz. sinif docstring'i.
        self._tablo = yeni
        ilk_yukleme = not self._yuklendi
        self._yuklendi = True
        self._basarisiz_ardisik = 0
        if ilk_yukleme or len(yeni) != onceki_boyut:
            toplu = sum(1 for s in yeni.values() if s == SINIF_TOPLU)
            logger.info(
                "katalog-yuklendi sinyal=%d oncelikli=%d toplu=%d",
                len(yeni),
                len(yeni) - toplu,
                toplu,
            )
        return True

    def baslat(self) -> None:
        """Arka plan tazeleme ipligini baslatir (idempotent).

        AYRI IPLIK, bilincli: HTTP cagrisi olay dongusunde yapilsaydi backend
        yavasladiginda tag-engine'in TUM normalizasyonu dururdu. Ilk cekim de
        burada yapilir; acilis blokelenmez.
        """
        if self._is_parcacigi is not None and self._is_parcacigi.is_alive():
            return
        self._durdur.clear()

        def _dongu() -> None:
            while not self._durdur.is_set():
                try:
                    self.yenile()
                except Exception:  # noqa: BLE001
                    logger.debug("katalog-yenileme-hatasi", exc_info=True)
                # Katalog henuz hic yuklenmediyse daha sik dene: acilista her
                # saniye onemli, cunku o sure boyunca ayrim yapilamiyor.
                bekleme = self._yenileme_sn if self._yuklendi else 3
                self._durdur.wait(bekleme)

        self._is_parcacigi = threading.Thread(
            target=_dongu, name="tag-engine-katalog", daemon=True
        )
        self._is_parcacigi.start()

    def durdur(self) -> None:
        self._durdur.set()


def onbellek_kur() -> KatalogOnbellegi:
    """Ortam degiskenlerinden onbellegi kurar (baslatmaz)."""
    return KatalogOnbellegi(
        base_url=os.getenv("BACKEND_API_BASE", "http://127.0.0.1:8000/api/v1"),
        service_token=os.getenv("INTERNAL_SERVICE_TOKEN", "change-me-internal-token"),
        yenileme_sn=int(os.getenv("SIGNAL_CATALOG_REFRESH_SEC", "60")),
        zaman_asimi_sn=float(os.getenv("SIGNAL_CATALOG_TIMEOUT_SEC", "5")),
    )
