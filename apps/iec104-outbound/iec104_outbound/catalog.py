"""Backend-api'den outbound target + signals + devices cekip cache eden modul.

Backend tarafinda `/api/v1/internal/outbound-targets`, `/internal/signals`,
`/internal/devices` endpoint'leri `X-Service-Token` ile korunur. Bu modul
periyodik olarak cekip in-memory bir snapshot tutar; degisiklikler oldugunda
"deploy diff" hesaplayip server manager'a uygular.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from threading import Lock
from typing import Any, Final

import requests

from iec104_outbound.registry import PointRegistry, build_point_registry
from iec104_outbound.server import IEC104ServerManager

from iec104_outbound import runtime_health
from iec104_outbound.consumer import _parse_iso_timestamp

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class TargetSpec:
    """Bir IEC 104 outbound target'inin deploy edilmis hali (signature ile birlikte)."""

    target_id: int
    name: str
    host: str
    port: int
    default_common_address: int
    # Yeniden deploy gerekip gerekmedigini hizlica anlamak icin imza:
    # (host, port, default_ca, sorted device CA imzalari, sorted signal IOA imzalari).
    signature: tuple


class CatalogClient:
    """Backend internal endpoint'lerini cagirir, hata durumunda eski snapshot kalir."""

    def __init__(self, *, base_url: str, service_token: str, timeout_sec: int = 8) -> None:
        self.base_url = base_url.rstrip("/")
        self.service_token = service_token
        self.timeout_sec = timeout_sec
        self._headers = { "X-Service-Token": service_token, "X-Service-Name": "iec104-outbound" }

    def _get(self, path: str) -> list[dict[str, Any]] | None:
        url = f"{self.base_url}{path}"
        try:
            resp = requests.get(url, headers=self._headers, timeout=self.timeout_sec)
        except requests.RequestException as exc:
            logger.warning("catalog_fetch_error path=%s error=%s", path, exc)
            return None
        if resp.status_code != 200:
            logger.warning("catalog_fetch_status path=%s status=%d", path, resp.status_code)
            return None
        try:
            return list(resp.json())
        except ValueError:
            logger.warning("catalog_fetch_invalid_json path=%s", path)
            return None

    def fetch_runtime_health(self) -> list[dict[str, Any]] | None:
        """Cihaz basina calisma-zamani sagligi.

        AYRI CAGRI ve bu bilincli: `fetch_all` herhangi bir parcasi
        basarisizsa None doner ve TUM deploy'u atlar. Saglik bilgisi nokta
        HARITASINI degil DEGERLERI besliyor; onun bir kez alinamamasi
        yuzunden butun registry senkronunu durdurmak, kucuk bir sorunu
        buyugune cevirirdi.

        BAYATLIK KARARI BACKEND'DE verilir (bkz. `/internal/
        device-runtime-health`): burada ikinci bir esik tanimlamak, arayuzde
        `bilinmiyor` gorunen bir cihazin SCADA'da eski `smart_idle`
        degeriyle "saglikli" gorunmesine yol acardi.
        """
        return self._get("/internal/device-runtime-health")

    def fetch_all(self) -> tuple[list[dict], list[dict], list[dict]] | None:
        """(targets, devices, signals) doner; herhangi biri basarisizsa None."""
        targets = self._get("/internal/outbound-targets")
        devices = self._get("/internal/devices")
        signals = self._get("/internal/signals")
        if targets is None or devices is None or signals is None:
            return None
        return targets, devices, signals


def _filter_iec104_targets(targets: list[dict]) -> list[dict]:
    """Sadece protocol='iec104' ve is_active olanlar."""
    return [
        t for t in targets
        if str(t.get("protocol") or "").lower() == "iec104"
        and t.get("is_active", True)
    ]


def _resolve_signal_ioa_for_sig(s: dict) -> int:
    raw = s.get("iec104_ioa")
    if raw is None:
        raw = s.get("iec104_ioa_offset")
    try:
        return int(raw) if raw is not None else -1
    except (TypeError, ValueError):
        return -1


def _build_signature(
    *, target: dict, devices: list[dict], signals: list[dict],
    default_listen_host: str,
) -> tuple:
    host = target.get("listen_host") or default_listen_host
    port = int(target.get("listen_port") or 2404)
    default_ca = int(target.get("iec104_common_address") or 1)
    # Whitelist degisirse de redeploy tetiklensin diye signature'a dahil et.
    allowed_peers_str = (target.get("iec104_allowed_peers") or "").strip()
    # MODEL IMZAYA DAHIL: nokta uretimi artik cihaz modeli ile sinyal
    # modelini eslestiriyor (bkz. registry.build_point_registry). Model
    # imzada olmasaydi, bir cihazin modeli degistirildiginde imza AYNI kalir,
    # worker yeniden deploy ETMEZ ve ESKI nokta haritasiyla calismaya devam
    # ederdi — degisiklik hicbir yerde gorunmezdi.
    device_sigs = tuple(sorted(
        (
            str(d.get("code") or ""),
            int(d["iec104_common_address"])
            if d.get("iec104_common_address") is not None else -1,
            bool(d.get("is_active", True)),
            str(d.get("model") or ""),
        )
        for d in devices
    ))
    signal_sigs = tuple(sorted(
        (
            str(s.get("key") or ""),
            int(s.get("iec104_type_id") or -1),
            _resolve_signal_ioa_for_sig(s),
            bool(s.get("is_active", True)),
            str(s.get("model") or ""),
        )
        for s in signals
    ))
    return (host, port, default_ca, device_sigs, signal_sigs, allowed_peers_str)


def _make_spec(
    target: dict, *, signature: tuple, default_listen_host: str,
) -> TargetSpec:
    return TargetSpec(
        target_id=int(target["id"]),
        name=str(target.get("name") or f"target-{target['id']}"),
        host=str(target.get("listen_host") or default_listen_host),
        port=int(target.get("listen_port") or 2404),
        default_common_address=int(target.get("iec104_common_address") or 1),
        signature=signature,
    )


def _build_registry_for(
    *, target: dict, devices: list[dict], signals: list[dict],
) -> PointRegistry:
    return build_point_registry(
        target_id=int(target["id"]),
        default_common_address=int(target.get("iec104_common_address") or 1),
        devices=devices,
        signals=signals,
    )


#: Ust uste kac basarisiz saglik cekiminden sonra tum cihazlar UNKNOWN'a
#: dusurulur. Bu bir GOZLEM BAYATLIK esigi DEGILDIR (o karar backend'de,
#: `device_session_readiness.gozlem_bayat`); yalnizca "uc ne kadar sure
#: erisilemez kalirsa elimizdeki bilgi durum iddiasi olmaktan cikar"
#: butcesi. Kisa ag hiccup'lari filoyu karartmasin diye 1'den buyuk.
SAGLIK_HATA_BUTCESI: Final = 3


class CatalogSyncer:
    """Periyodik olarak backend'i cekip server manager'i guncel tutar.

    Threading: bu sinif kendi thread'inde calismaz; `tick()` async loop'tan
    cagirilir cunku deploy/undeploy asyncio operasyonu. Kendi periyodikligi
    icin `run_forever` async fonksiyonu var.
    """

    def __init__(
        self,
        *,
        catalog: CatalogClient,
        manager: IEC104ServerManager,
        default_listen_host: str,
        refresh_sec: int,
    ) -> None:
        self.catalog = catalog
        self.manager = manager
        self.default_listen_host = default_listen_host
        self.refresh_sec = max(5, int(refresh_sec))
        self._deployed: dict[int, TargetSpec] = {}
        #: Son BASARILI saglik cekiminde deger yazdigimiz cihaz kodlari.
        #: Bir cihaz listeden DUSERSE noktasi eski degerinde takili kalmasin
        #: diye tutulur (bkz. `_push_runtime_health`).
        self._saglik_bilinen: set[str] = set()
        #: Ust uste kac saglik cekimi basarisiz oldu.
        self._saglik_hata_sayaci = 0
        self._lock = Lock()  # _deployed icin (cross-thread okuma yok ama ileride lazim)
        self._stop = asyncio.Event()

    async def tick(self) -> None:
        """Bir snapshot cek + diff uygula."""
        snapshot = self.catalog.fetch_all()
        if snapshot is None:
            return
        targets, devices, signals = snapshot
        iec104_targets = _filter_iec104_targets(targets)
        seen_ids: set[int] = set()
        for target in iec104_targets:
            try:
                target_id = int(target["id"])
            except (KeyError, TypeError, ValueError):
                continue
            seen_ids.add(target_id)
            signature = _build_signature(
                target=target, devices=devices, signals=signals,
                default_listen_host=self.default_listen_host,
            )
            existing = self._deployed.get(target_id)
            if existing is not None and existing.signature == signature:
                continue
            spec = _make_spec(
                target, signature=signature, default_listen_host=self.default_listen_host,
            )
            registry = _build_registry_for(
                target=target, devices=devices, signals=signals,
            )
            allowed_peers_raw = (target.get("iec104_allowed_peers") or "").strip()
            allowed_peers: tuple[str, ...] = (
                tuple(p.strip() for p in allowed_peers_raw.split(",") if p.strip())
                if allowed_peers_raw
                else ()
            )
            try:
                await self.manager.deploy(
                    target_id=spec.target_id,
                    name=spec.name,
                    host=spec.host,
                    port=spec.port,
                    registry=registry,
                    allowed_peers=allowed_peers,
                )
                self._deployed[target_id] = spec
                logger.info(
                    "iec104_target_deployed id=%d name=%s host=%s port=%d default_ca=%d points=%d distinct_ca=%d",
                    spec.target_id, spec.name, spec.host, spec.port,
                    spec.default_common_address, len(registry.points),
                    len(registry.unique_common_addresses()),
                )
            except OSError as exc:
                logger.error(
                    "iec104_target_bind_failed id=%d port=%d error=%s",
                    spec.target_id, spec.port, exc,
                )
            except Exception:
                logger.exception("iec104_target_deploy_failed id=%d", spec.target_id)

        # Backend'den dusen (silinen ya da pasiflesen) target'lari indir.
        for stale_id in [tid for tid in self._deployed.keys() if tid not in seen_ids]:
            try:
                await self.manager.undeploy(stale_id)
                self._deployed.pop(stale_id, None)
                logger.info("iec104_target_undeployed id=%d", stale_id)
            except Exception:
                logger.exception("iec104_target_undeploy_failed id=%d", stale_id)

    def _saglik_yaz(self, kod: str, durum: int, *, iyi: bool,
                    gecikme: object, damga: object) -> None:
        """Bir cihazin iki sistem noktasini yazar.

        `gecikme is None` = BILINMIYOR. Bayragi `0` yapmak "gecikme YOK"
        demek olurdu; bilmedigimiz seyi iyi haber olarak yayinlamayiz —
        deger `False` gider ama kalite `good=False` ile isaretlenir.
        """
        self.manager.update_point_threadsafe(
            device_code=kod,
            signal_key=runtime_health.KEY_RUNTIME_STATE,
            value=float(durum),
            good=iyi,
            timestamp=damga,
        )
        self.manager.update_point_threadsafe(
            device_code=kod,
            signal_key=runtime_health.KEY_REPORT_LATE,
            value=bool(gecikme),
            good=gecikme is not None,
            timestamp=damga,
        )

    def _bilinmiyora_dusur(self, kodlar: set[str], sebep: str) -> int:
        """Verilen cihazlari UNKNOWN + kotu kalite olarak yayinlar.

        NEDEN GEREKLI — SESSIZ ESKIME
        -----------------------------
        `update_point` "degisim varsa yay" mantigiyla calisir ve son degeri
        onbellekte tutar. Bir cihaz saglik listesinden DUSERSE (silindi,
        gateway artik bildirmiyor, uc erisilemez) hicbir yeni yazma olmaz
        ve SCADA o cihazi SONSUZA KADAR son bilinen degerinde gorur.
        Cihaz haberlesmeyi tamamen kesmis olsa bile ekranda ONLINE kalirdi:
        yanlis, ve sessizce yanlis.

        `unknown` yaymak durustur — "bilmiyoruz" bir durum iddiasi degildir.
        `lost` yaymak ise DOGRULANMAMIS bir ariza iddiasi olurdu.
        """
        for kod in sorted(kodlar):
            self._saglik_yaz(
                kod, runtime_health.STATE_UNKNOWN,
                iyi=False, gecikme=None, damga=None,
            )
        if kodlar:
            logger.warning(
                "iec104_runtime_health_bilinmiyor sebep=%s cihaz_sayisi=%d",
                sebep, len(kodlar),
            )
        return len(kodlar) * 2

    def _push_runtime_health(self) -> int:
        """Saglik degerlerini sunucuya yaz. Doner: guncellenen nokta sayisi.

        `update_point` DEGISIM VARSA yayar (report by exception), yani her
        turda cagirmak SCADA'ya gereksiz trafik uretmez; degismeyen durum
        sessizce onbellekte tazelenir ve bir sonraki GI'da dogru gider.

        UC ERISILEMEZSE tek bir hatada her seyi UNKNOWN yapmayiz — kisa bir
        ag hiccup'i tum filoyu SCADA'da karartirdi. Ust uste
        `SAGLIK_HATA_BUTCESI` kez basarisiz olursa artik "bilmiyoruz"
        demek durustur: elimizdeki en son bilgi o kadar eski ki bir DURUM
        IDDIASI olarak sunulamaz.
        """
        satirlar = self.catalog.fetch_runtime_health()
        if satirlar is None:
            self._saglik_hata_sayaci += 1
            if self._saglik_hata_sayaci < SAGLIK_HATA_BUTCESI:
                logger.warning(
                    "iec104_runtime_health_cekilemedi deneme=%d/%d — mevcut "
                    "degerler korunuyor",
                    self._saglik_hata_sayaci, SAGLIK_HATA_BUTCESI,
                )
                return 0
            dusen = set(self._saglik_bilinen)
            self._saglik_bilinen.clear()
            return self._bilinmiyora_dusur(dusen, "uc_erisilemez")

        self._saglik_hata_sayaci = 0
        yazilan = 0
        goruldu: set[str] = set()
        for satir in satirlar:
            kod = str(satir.get("device_code") or "")
            if not kod:
                continue
            goruldu.add(kod)
            damga = _parse_iso_timestamp(satir.get("updated_at"))
            durum = runtime_health.state_code(satir.get("state"))
            self._saglik_yaz(
                kod, durum,
                # BAYAT GOZLEM `good=False`: SCADA nokta kalitesinden de
                # gorsun. Deger yine de UNKNOWN olarak gider (backend'in
                # kendisi oyle yollar) — iki isaret birbirini destekler.
                iyi=not bool(satir.get("stale")),
                gecikme=satir.get("report_late"),
                damga=damga,
            )
            yazilan += 2

        # LISTEDEN DUSENLER: onceden bildigimiz ama artik gelmeyen cihazlar.
        yazilan += self._bilinmiyora_dusur(
            self._saglik_bilinen - goruldu, "kayit_kayboldu"
        )
        self._saglik_bilinen = goruldu
        return yazilan

    async def run_forever(self) -> None:
        # Ilk cekim hemen.
        await self.tick()
        # BASLANGIC SNAPSHOT'I: servis yeniden basladiginda bir sonraki
        # durum GECISINI beklemez; mevcut durumlari hemen yayinlar. Aksi
        # halde SCADA, cihaz haftalarca `smart_idle`da kalirsa restart
        # sonrasi hicbir sey gormezdi.
        try:
            self._push_runtime_health()
        except Exception:
            logger.exception("iec104_runtime_health_initial_failed")
        while not self._stop.is_set():
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=self.refresh_sec)
                # _stop set edildi.
                return
            except asyncio.TimeoutError:
                pass
            try:
                await self.tick()
            except Exception:
                logger.exception("iec104_catalog_tick_failed")
            try:
                # NOKTA HARITASINDAN AYRI: saglik degerleri her turda
                # tazelenir. `tick` basarisiz olsa bile denenir — deploy
                # sorunu ile deger tazeligi ayri sorunlardir.
                self._push_runtime_health()
            except Exception:
                logger.exception("iec104_runtime_health_push_failed")

    def request_stop(self) -> None:
        self._stop.set()

    def deployed_count(self) -> int:
        return len(self._deployed)
