"""Backend'den adres planlarini cekip sunuculari guncel tutar.

Worker adres HESAPLAMAZ: `/internal/modbus-plans` uctan gelen plani birebir
uygular. Boylece web arayuzunde gosterilen ve CSV ile disa aktarilan adres
tablosu ile sahada yayinlanan adres arasinda ayrisma imkansizdir.

Yeniden deploy sadece plan gercekten degistiginde yapilir (imza karsilastirmasi);
aksi halde her 30 saniyede bir TCP sunucusu kapanip acilir ve SCADA baglantisi
surekli koparadi.
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
    ) -> None:
        self.catalog = catalog
        self.manager = manager
        self.default_listen_host = default_listen_host
        self.refresh_sec = max(5, int(refresh_sec))
        self._deployed: dict[int, str] = {}
        self._stop = asyncio.Event()
        self.last_error: str | None = None
        self.last_plan_count = 0

    def deployed_count(self) -> int:
        return len(self._deployed)

    async def tick(self) -> None:
        plans = self.catalog.fetch_plans()
        if plans is None:
            self.last_error = "plan_fetch_failed"
            return
        self.last_error = None
        self.last_plan_count = len(plans)

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
