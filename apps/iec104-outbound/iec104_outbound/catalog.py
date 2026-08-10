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
from typing import Any

import requests

from iec104_outbound.registry import PointRegistry, build_point_registry
from iec104_outbound.server import IEC104ServerManager

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

    async def run_forever(self) -> None:
        # Ilk cekim hemen.
        await self.tick()
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

    def request_stop(self) -> None:
        self._stop.set()

    def deployed_count(self) -> int:
        return len(self._deployed)
