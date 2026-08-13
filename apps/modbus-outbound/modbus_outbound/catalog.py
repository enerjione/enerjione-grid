"""Backend'den adres planlarini + son bilinen degerleri cekip sunuculari
guncel tutar.

Worker adres HESAPLAMAZ: `/internal/modbus-plans` uctan gelen plani birebir
uygular. Boylece web arayuzunde gosterilen ve CSV ile disa aktarilan adres
tablosu ile sahada yayinlanan adres arasinda ayrisma imkansizdir.

Yeniden deploy sadece plan gercekten degistiginde yapilir (imza karsilastirmasi);
aksi halde her 30 saniyede bir TCP sunucusu kapanip acilir ve SCADA baglantisi
surekli koparadi.

IKI SENKRON DONGUSU
-------------------
  PlanSyncer      adres plani (nadiren degisir, cekimi pahali)
  SnapshotSyncer  son bilinen degerler (`/internal/modbus-values`) — canli
                  akisin doldurmadigi register'lari doldurur. Modbus'ta
                  "deger gelmedi" hali olmadigi icin degismeyen bir sinyalin
                  register'i aksi halde sonsuza dek 0 kalir ve SCADA bunu
                  gercek bir olcum gibi okur.

HTTP CEKIMLERI THREAD'E ALINIR: `requests` bloklayicidir ve bu iki dongu
Modbus sunucularinin asyncio loop'unda kosar. Dogrudan cagrilirsa backend
yavasladiginda (ya da 15 sn timeout'a dustugunde) TUM SCADA okumalari o
sure boyunca durur.
"""

from __future__ import annotations

import asyncio
import json
import logging

import requests

from modbus_outbound.registry import build_registry_from_plan
from modbus_outbound.server import ModbusServerManager

logger = logging.getLogger(__name__)


class CatalogClient:
    def __init__(self, *, base_url: str, service_token: str, timeout_sec: int = 15) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout_sec = timeout_sec
        self._headers = {
            "X-Service-Token": service_token,
            "X-Service-Name": "modbus-outbound",
        }

    def fetch_plans(self) -> list[dict] | None:
        """Aktif Modbus hedeflerinin planlari. Hata durumunda None (eski plan kalir)."""
        url = f"{self.base_url}/internal/modbus-plans"
        try:
            resp = requests.get(url, headers=self._headers, timeout=self.timeout_sec)
        except requests.RequestException as exc:
            logger.warning("modbus_plan_fetch_error error=%s", exc)
            return None
        if resp.status_code != 200:
            logger.warning("modbus_plan_fetch_status status=%d", resp.status_code)
            return None
        try:
            data = resp.json()
        except ValueError:
            logger.warning("modbus_plan_fetch_invalid_json")
            return None
        return list(data) if isinstance(data, list) else None

    def fetch_values(self, since: str | None = None) -> dict | None:
        """Aktif Modbus hedeflerindeki cihazlarin SON BILINEN degerleri.

        Kaynak backend'in `telemetry_latest` tablosu — Canli Degerler
        ekraninin da okudugu satirlar.

        `since` verilirse yalnizca o damgadan SONRA guncellenen satirlar
        gelir. Deger backend'in kendi yanitindan alinir (`max_updated_at`),
        worker kendi saatini kullanmaz — saat kaymasi satir kaybettirmesin.
        600 cihazda tam liste ~115.000 satirdir; her turda tam cekmek
        `/signals/live`de OOM'a goturen desenin aynisi olurdu.

        Hata durumunda None doner ve tazeleme turu atlanir; register'larda ne
        varsa kalir (yarim uygulanmis bir tur SCADA'da kismi/karisik veri
        demek olurdu).
        """
        url = f"{self.base_url}/internal/modbus-values"
        params = {"since": since} if since else None
        try:
            resp = requests.get(
                url, headers=self._headers, params=params, timeout=self.timeout_sec
            )
        except requests.RequestException as exc:
            logger.warning("modbus_values_fetch_error error=%s", exc)
            return None
        if resp.status_code != 200:
            logger.warning("modbus_values_fetch_status status=%d", resp.status_code)
            return None
        try:
            data = resp.json()
        except ValueError:
            logger.warning("modbus_values_fetch_invalid_json")
            return None
        if isinstance(data, dict):
            rows = data.get("values")
            if not isinstance(rows, list):
                return None
            return {
                "values": rows,
                "max_updated_at": data.get("max_updated_at"),
                "full": bool(data.get("full", since is None)),
            }
        # Uc ilerde duz liste dondurmeye gecerse de calissin.
        if isinstance(data, list):
            return {"values": data, "max_updated_at": None, "full": True}
        return None


def plan_signature(plan: dict) -> str:
    """Plani temsil eden kararli imza — degisiklik tespiti icin.

    Noktalarin tamami dahil edilir (adres kaymasi da yakalanmali). Buyuk
    kurulumlarda bu string uzun olur ama sadece hash'lenip karsilastirilir.
    """
    core = {
        "host": plan.get("listen_host"),
        "port": plan.get("listen_port"),
        "mode": plan.get("mode"),
        "fmt": plan.get("value_format"),
        "word": plan.get("word_order"),
        "peers": plan.get("allowed_peers"),
        "points": [
            (
                p.get("device_code"), p.get("signal_key"), p.get("unit_id"),
                p.get("function"), p.get("address"), p.get("word_count"),
                p.get("scale"), p.get("offset"),
            )
            for p in plan.get("points") or []
        ],
    }
    return str(hash(json.dumps(core, sort_keys=True, default=str)))


class PlanSyncer:
    """Periyodik plan cekimi + fark uygulama."""

    def __init__(
        self,
        *,
        catalog: CatalogClient,
        manager: ModbusServerManager,
        default_listen_host: str,
        refresh_sec: int,
        on_plan_change=None,
    ) -> None:
        self.catalog = catalog
        self.manager = manager
        self.default_listen_host = default_listen_host
        self.refresh_sec = max(5, int(refresh_sec))
        # Plan degistiginde (yeni hedef/cihaz/adres) cagrilir. Snapshot
        # tazelemesini BEKLETMEDEN tetiklemek icin: yeni kurulan noktalar
        # ilk canli olcume kadar 0 kalmasin.
        self._on_plan_change = on_plan_change
        self._deployed: dict[int, str] = {}
        self._stop = asyncio.Event()
        self.last_error: str | None = None
        self.last_plan_count = 0

    def deployed_count(self) -> int:
        return len(self._deployed)

    async def tick(self) -> None:
        # Bloklayici HTTP thread'e: aksi halde backend yavaslarken Modbus
        # sunucularinin loop'u duruyor ve SCADA okumalari zaman asimina
        # dusuyor (bkz. modul docstring'i).
        plans = await asyncio.to_thread(self.catalog.fetch_plans)
        if plans is None:
            self.last_error = "plan_fetch_failed"
            return
        self.last_error = None
        self.last_plan_count = len(plans)

        degisti = False
        seen: set[int] = set()
        for plan in plans:
            try:
                target_id = int(plan["target_id"])
            except (KeyError, TypeError, ValueError):
                continue
            if not plan.get("is_active", True):
                continue
            seen.add(target_id)

            signature = plan_signature(plan)
            if self._deployed.get(target_id) == signature:
                continue

            registry = build_registry_from_plan(plan)
            host = str(plan.get("listen_host") or self.default_listen_host)
            port = int(plan.get("listen_port") or 502)
            peers = tuple(str(p) for p in (plan.get("allowed_peers") or []))
            try:
                await self.manager.deploy(
                    target_id=target_id,
                    name=str(plan.get("target_name") or f"target-{target_id}"),
                    host=host,
                    port=port,
                    registry=registry,
                    allowed_peers=peers,
                )
                self._deployed[target_id] = signature
                degisti = True
                logger.info(
                    "modbus_target_deployed id=%d addr=%s:%d mode=%s fmt=%s units=%d points=%d",
                    target_id, host, port, plan.get("mode"), plan.get("value_format"),
                    len(registry.stores), registry.point_count,
                )
            except OSError as exc:
                logger.error(
                    "modbus_target_bind_failed id=%d port=%d error=%s",
                    target_id, port, exc,
                )
            except Exception:
                logger.exception("modbus_target_deploy_failed id=%d", target_id)

        for stale in [tid for tid in self._deployed if tid not in seen]:
            try:
                await self.manager.undeploy(stale)
                self._deployed.pop(stale, None)
                logger.info("modbus_target_undeployed id=%d", stale)
            except Exception:
                logger.exception("modbus_target_undeploy_failed id=%d", stale)

        if degisti and self._on_plan_change is not None:
            # Yeni/degismis noktalar bir sonraki periyodu BEKLEMESIN.
            try:
                self._on_plan_change()
            except Exception:  # noqa: BLE001
                logger.exception("modbus_plan_change_callback_failed")

    async def run_forever(self) -> None:
        await self.tick()
        while not self._stop.is_set():
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=self.refresh_sec)
                return
            except asyncio.TimeoutError:
                pass
            try:
                await self.tick()
            except Exception:
                logger.exception("modbus_plan_tick_failed")

    def request_stop(self) -> None:
        self._stop.set()


class SnapshotSyncer:
    """Son bilinen degerleri periyodik olarak register'lara yazar.

    NE COZUYOR: canli akis yalnizca cihaz yeni olcum yayinladikca akar.
    Degismeyen bir sinyal (ariza bayragi, konum, nominal degerler) veya yeni
    baslatilmis bir servis icin register'a HIC yazilmaz ve Modbus'ta
    yazilmamis adres 0 doner — SCADA bunu "gerilim sifir" gibi gercek bir
    olcum sanir. Bu dongu her turda backend'in `telemetry_latest` tablosunu
    okuyup EKSIK KALAN noktalari doldurur.

    Tazelemenin canli akisi bozmamasi registry tarafinda garanti altinda:
    daha yeni bir canli deger bayat bir DB satiriyla ezilmez (bkz.
    `PointRegistry.apply_snapshot`).

    CEKIM ARTIMLIDIR: ilk tur (ve plan degisimi sonrasi) TAM liste ceker
    (tohumlama), sonraki turlar yalnizca degisen satirlari alir. 600 cihazda
    tam liste ~115.000 satirdir; her 30 saniyede tam cekmek `/signals/live`de
    OOM'a goturen desenin aynisi olurdu.

    `refresh_sec <= 0` verilirse dongu hic kurulmaz (kapali).
    """

    #: Kacinci turda bir TAM liste cekilir (artimli esigi sifirlanir).
    #: NEDEN GEREKLI: artimli filtre `updated_at`e dayanir. Cihazda/sunucuda
    #: saat GERI alinirsa (NTP adimi) yeni yazilan satirlarin damgasi esigin
    #: altinda kalir ve o satirlar bir daha HIC gelmez. Periyodik tam tur bu
    #: sinifi kendiliginden onarir. 20 tur x 30 sn = 10 dakika.
    TAM_TUR_PERIYODU = 20

    #: Tek seferde kac satir uygulanir.
    #: NEDEN PARCALI: `apply_snapshot` senkrondur ve registry kilidini tutar;
    #: Modbus sunuculari da AYNI event loop'ta cevap verir. Tam tur (600
    #: cihazda ~115.000 satir) tek blokta uygulanirsa loop o sure boyunca
    #: durur ve SCADA istekleri gecikir. Parcalar arasinda loop'a donerek
    #: okumalarin araya girmesine izin veriyoruz.
    PARTI_BOYU = 2_000

    def __init__(
        self,
        *,
        catalog: CatalogClient,
        manager: ModbusServerManager,
        refresh_sec: int,
    ) -> None:
        self.catalog = catalog
        self.manager = manager
        self.refresh_sec = int(refresh_sec)
        self.enabled = self.refresh_sec > 0
        self._stop = asyncio.Event()
        # Uyandirma: hem durdurma hem "hemen tazele" ayni event'i kullanir;
        # dongu uyandiginda hangisi oldugunu `_stop` ile ayirir.
        self._wake = asyncio.Event()
        # Artimli cekim esigi — backend'in verdigi `max_updated_at`. None =
        # bir sonraki tur TAM liste ceker.
        self._since: str | None = None
        self.last_error: str | None = None
        self.refreshes = 0
        self.full_refreshes = 0
        self.last_row_count = 0
        self.last_result: dict = {}
        self.total_seeded = 0
        self.total_refreshed = 0

    def request_refresh(self) -> None:
        """Periyodu beklemeden TAM tazeleme yap (plan degisiminde).

        Esik sifirlanir: plan degistiyse adresler kaymis olabilir ve yeni
        adreslerin doldurulmasi icin butun noktalar yeniden yazilmali —
        artimli liste yalnizca son saniyelerde degisenleri getirirdi.
        """
        self._since = None
        self._wake.set()

    def request_stop(self) -> None:
        self._stop.set()
        self._wake.set()

    async def _parcali_uygula(self, rows: list[dict]) -> dict:
        """Satirlari PARTI_BOYU'luk dilimlerde uygula, arada loop'a don.

        Bkz. `PARTI_BOYU`: kilit dilim basina milisaniyeler tutulur, SCADA
        okumalari tam turun ortasinda bile cevaplanir.
        """
        toplam = {
            "targets": self.manager.server_count(), "seeded": 0,
            "refreshed": 0, "stale": 0, "unmapped": 0, "uncoercible": 0,
        }
        for bas in range(0, len(rows), self.PARTI_BOYU):
            dilim = rows[bas:bas + self.PARTI_BOYU]
            sonuc = self.manager.apply_snapshot(dilim)
            for anahtar in ("seeded", "refreshed", "stale", "unmapped", "uncoercible"):
                toplam[anahtar] += sonuc[anahtar]
            if bas + self.PARTI_BOYU < len(rows):
                await asyncio.sleep(0)
        return toplam

    async def tick(self) -> None:
        if self.manager.server_count() == 0:
            # Henuz hedef ayaga kalkmadi (ilk plan cekimi surmekte olabilir).
            # Bos yere on binlerce satir cekmenin anlami yok; plan deploy
            # edildiginde `request_refresh` bu turu hemen tetikler.
            return
        if self.refreshes and self.refreshes % self.TAM_TUR_PERIYODU == 0:
            self._since = None
        yanit = await asyncio.to_thread(self.catalog.fetch_values, self._since)
        if yanit is None:
            self.last_error = "values_fetch_failed"
            return
        self.last_error = None
        rows = yanit["values"]
        self.last_row_count = len(rows)
        self.refreshes += 1
        if yanit.get("full"):
            self.full_refreshes += 1
        # Esik yalnizca backend deger verdiyse ilerler; bos turda korunur.
        if yanit.get("max_updated_at"):
            self._since = str(yanit["max_updated_at"])
        if not rows:
            # `last_result` GERCEKTEN son turu anlatsin: eski turun sayilarini
            # birakmak, operatore her turda yeniden yaziliyor gibi gorunur.
            self.last_result = {
                "targets": self.manager.server_count(), "seeded": 0,
                "refreshed": 0, "stale": 0, "unmapped": 0, "uncoercible": 0,
            }
            return
        sonuc = await self._parcali_uygula(rows)
        self.last_result = sonuc
        self.total_seeded += sonuc.get("seeded", 0)
        self.total_refreshed += sonuc.get("refreshed", 0)
        # Ilgi ceken tek durum bir sey YAZILMASI; "hepsi guncel" turlari
        # 30 saniyede bir log satiri uretmesin.
        if sonuc.get("seeded") or sonuc.get("refreshed"):
            logger.info(
                "modbus_snapshot_applied rows=%d hedef=%d ilk=%d tazelendi=%d "
                "bayat_atlandi=%d planda_yok=%d",
                len(rows), sonuc.get("targets", 0), sonuc.get("seeded", 0),
                sonuc.get("refreshed", 0), sonuc.get("stale", 0),
                sonuc.get("unmapped", 0),
            )

    async def run_forever(self) -> None:
        if not self.enabled:
            logger.warning(
                "modbus_snapshot_disabled — degismeyen sinyaller register'da "
                "0 kalabilir (MODBUS_SNAPSHOT_REFRESH_SEC=%d)", self.refresh_sec
            )
            return
        await self.tick()
        while not self._stop.is_set():
            try:
                await asyncio.wait_for(self._wake.wait(), timeout=self.refresh_sec)
            except asyncio.TimeoutError:
                pass
            if self._stop.is_set():
                return
            self._wake.clear()
            try:
                await self.tick()
            except Exception:
                logger.exception("modbus_snapshot_tick_failed")
