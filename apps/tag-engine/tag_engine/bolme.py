"""Kit cihazlarini sanal setlere BOLME haritasi.

NE COZUYOR
----------
Horstmann Pole Master Kit tek bir DNP3 outstation'dir ama uzerindeki 9 uydu
ucerli setler halinde sahada BIRBIRINDEN BAGIMSIZ noktalara kelepcelenir.
Kullanici her seti ayri bir cihaz olarak gorur; backend'de her set ayri bir
`devices` satiridir.

Gateway ise tek cihaz gorur ve olcumleri fiziksel kodla yayinlar:

    device_code="PMK-001"  signal_key="sat05.fault_current"

Bu modul o mesaji setine ait sanal cihaza yeniden yazar:

    device_code="PMK-001-S2"  signal_key="sat02.fault_current"

NEDEN BURADA, NEDEN BACKEND'DE DEGIL
------------------------------------
tag-engine'in cikisi (normalize akis) sistemin FORK NOKTASIDIR: backend
kalicilastirmasi, alarm-service, iec104-outbound ve MQTT hepsi ayni mesajdan
okur. Bolme daha asagida (backend tuketicisinde) yapilsaydi yalnizca DEPOLAMA
duzelirdi; alarm hala fiziksel cihaza duser, ariza motoru uc setin hepsini tek
noktada gorur ve hatta yalnizca TEK nokta kirmizi olurdu. Ariza araligi hep
fazla genis cikardi — ve bu "kismen calisiyor" gorunumu en tehlikeli hal.

NEDEN COGALTMA DEGIL, YERINDE YENIDEN YAZMA
-------------------------------------------
Bir giren = bir cikan. Backend tuketicisi `processed_messages` uzerinde
(consumer, message_id) TEKIL kisiti tutuyor; ayni mesajdan iki cikti
uretilseydi ikinci satir IntegrityError verir, batch geri alinir ve mesaj
SONSUZA KADAR yeniden teslim edilirdi. Bu yuzden kit seviyesindeki
(`master.*`) olcumler COGALTILMAZ: fiziksel kayitta kalir ve sanal setler
onlari okuma tarafinda devralir.

HARITA YOKKEN NE OLUR
---------------------
Mesaj AYNEN gecer: veri fiziksel kayda `sat05.fault_current` olarak yazilir.
O anahtar fiziksel kitin sinyal katalogunda TANIMLIDIR — yani veri kaybolmaz
ve yanlis bir cihaza da yazilmaz, yalnizca sete ulasmaz. Bilincli secim:
alternatifi (harita gelene kadar mesajlari tutmak) telemetri hattini
durdurmak olurdu.
"""

from __future__ import annotations

import json
import logging
import os
import threading
import urllib.error
import urllib.request

logger = logging.getLogger("tag-engine")


class BolmeOnbellegi:
    """(fiziksel cihaz kodu, kaynak) -> (sanal cihaz kodu, sanal kaynak).

    OKUMA KILITSIZ: tablo yerinde degistirilmez; her tazelemede yeni bir
    sozluk kurulup referans atomik degistirilir (bkz. KatalogOnbellegi).
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
        self._tablo: dict[tuple[str, str], tuple[str, str]] = {}
        self._yuklendi = False
        self._basarisiz_ardisik = 0
        self._durdur = threading.Event()
        self._is_parcacigi: threading.Thread | None = None

    # ---- Okuma -----------------------------------------------------------
    def coz(self, device_code: str | None, signal_key: str | None) -> tuple[str, str] | None:
        """Yeniden yazilmis (device_code, signal_key) ciftini dondur.

        Bolunmeyen her durumda None — cagiran taraf mesaji AYNEN gecirir.
        """
        if not device_code or not signal_key or "." not in signal_key:
            return None
        if not self._tablo:
            return None
        kaynak, _, olcum = signal_key.partition(".")
        hedef = self._tablo.get((device_code, kaynak))
        if hedef is None:
            return None
        yeni_kod, yeni_kaynak = hedef
        return yeni_kod, f"{yeni_kaynak}.{olcum}"

    @property
    def yuklendi(self) -> bool:
        return self._yuklendi

    @property
    def boyut(self) -> int:
        return len(self._tablo)

    # ---- Tazeleme --------------------------------------------------------
    def yenile(self) -> bool:
        """Haritayi bir kez ceker. Basarisizlikta MEVCUT tabloyu KORUR."""
        url = f"{self._base_url}/internal/device-map"
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
            if self._basarisiz_ardisik in (1, 10, 100) or self._basarisiz_ardisik % 100 == 0:
                logger.warning(
                    "bolme-haritasi-cekilemedi url=%s hata=%s ardisik=%d yuklendi=%s "
                    "(harita gelene kadar kit olcumleri FIZIKSEL kayda akar)",
                    url,
                    ex,
                    self._basarisiz_ardisik,
                    self._yuklendi,
                )
            return False

        cihazlar = ham.get("devices") if isinstance(ham, dict) else None
        if not isinstance(cihazlar, list):
            logger.warning("bolme-haritasi-bicimi-beklenmedik tip=%s", type(ham).__name__)
            return False

        yeni: dict[tuple[str, str], tuple[str, str]] = {}
        cakisan = 0
        for kayit in cihazlar:
            if not isinstance(kayit, dict):
                continue
            fiziksel = kayit.get("code")
            altlar = kayit.get("subunits")
            if not fiziksel or not isinstance(altlar, list):
                continue
            for alt in altlar:
                if not isinstance(alt, dict):
                    continue
                sanal_kod = alt.get("code")
                kaynaklar = alt.get("sources")
                if not sanal_kod or not isinstance(kaynaklar, dict):
                    continue
                for fiziksel_kaynak, sanal_kaynak in kaynaklar.items():
                    anahtar = (str(fiziksel), str(fiziksel_kaynak))
                    if anahtar in yeni:
                        # ESLEME BIJEKTIF OLMALI. Ayni fiziksel kaynak iki
                        # sete duserse olcumlerden biri KAYBOLURDU (canli
                        # tabloda (device_id, signal_key) tekil ve en yenisi
                        # kazanir) ve hicbir hata olusmazdi.
                        cakisan += 1
                        continue
                    yeni[anahtar] = (str(sanal_kod), str(sanal_kaynak))

        if cakisan:
            logger.error(
                "bolme-haritasi-cakismasi adet=%d — ayni fiziksel uydu birden "
                "fazla sete atanmis; ilk esleme korundu",
                cakisan,
            )

        onceki = len(self._tablo)
        self._tablo = yeni
        ilk = not self._yuklendi
        self._yuklendi = True
        self._basarisiz_ardisik = 0
        if ilk or len(yeni) != onceki:
            logger.info("bolme-haritasi-yuklendi esleme=%d", len(yeni))
        return True

    def baslat(self) -> None:
        """Arka plan tazeleme ipligini baslatir (idempotent)."""
        if self._is_parcacigi is not None and self._is_parcacigi.is_alive():
            return
        self._durdur.clear()

        def _dongu() -> None:
            while not self._durdur.is_set():
                try:
                    self.yenile()
                except Exception:  # noqa: BLE001
                    logger.debug("bolme-haritasi-yenileme-hatasi", exc_info=True)
                bekleme = self._yenileme_sn if self._yuklendi else 3
                self._durdur.wait(bekleme)

        self._is_parcacigi = threading.Thread(
            target=_dongu, name="tag-engine-bolme", daemon=True
        )
        self._is_parcacigi.start()

    def durdur(self) -> None:
        self._durdur.set()


def onbellek_kur() -> BolmeOnbellegi:
    """Ortam degiskenlerinden onbellegi kurar (baslatmaz).

    Yenileme araligi sinyal katalogu ile AYNI degiskene bagli: ikisi de ayni
    backend'den, ayni sikilikta tazelenir ve ayrisirlarsa "katalog yeni,
    bolme eski" gibi aciklamasi zor bir ara durum olusur.
    """
    return BolmeOnbellegi(
        base_url=os.getenv("BACKEND_API_BASE", "http://127.0.0.1:8000/api/v1"),
        service_token=os.getenv("INTERNAL_SERVICE_TOKEN", "change-me-internal-token"),
        yenileme_sn=int(os.getenv("SIGNAL_CATALOG_REFRESH_SEC", "60")),
        zaman_asimi_sn=float(os.getenv("SIGNAL_CATALOG_TIMEOUT_SEC", "5")),
    )
