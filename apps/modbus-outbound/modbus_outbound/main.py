"""modbus-outbound entrypoint.

Akis:
  1. Health HTTP sunucusunu baslat.
  2. Asyncio loop'unda plan syncer'i baslat -> her aktif Modbus hedefi icin
     bir TCP sunucusu ayaga kalkar.
  3. NATS consumer'i ayri daemon thread'de baslat.
  4. SIGINT/SIGTERM ile hepsini duzgun indir.

tag-engine -> NATS (telemetry.normalized) -> consumer -> PointRegistry ->
SCADA'nin Modbus okumasi bu hafizadan cevaplanir. Yani SCADA'nin tarama hizi
telemetri akisini ETKILEMEZ; iki taraf birbirinden tamamen bagimsiz calisir.
"""

from __future__ import annotations

import asyncio
import logging
import signal
import sys
from datetime import datetime, timezone

from modbus_outbound import __version__
from modbus_outbound.catalog import CatalogClient, PlanSyncer
from modbus_outbound.config import SETTINGS
from modbus_outbound.consumer import TelemetryConsumer
from modbus_outbound.health import start_health_server
from modbus_outbound.server import manager as modbus_manager


def _configure_logging() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
        stream=sys.stdout,
    )


async def _async_main() -> None:
    logger = logging.getLogger("modbus_outbound.main")
    settings = SETTINGS

    loop = asyncio.get_running_loop()
    modbus_manager.bind_loop(loop)

    catalog = CatalogClient(
        base_url=settings.backend_api_base,
        service_token=settings.internal_service_token,
    )
    syncer = PlanSyncer(
        catalog=catalog,
        manager=modbus_manager,
        default_listen_host=settings.default_listen_host,
        refresh_sec=settings.catalog_refresh_sec,
    )
    consumer = TelemetryConsumer(settings=settings, manager=modbus_manager)

    started_at = datetime.now(timezone.utc).isoformat()

    def _health() -> dict:
        return {
            "status": "ok",
            "service": "modbus-outbound",
            "version": __version__,
            "started_at": started_at,
            "active_servers": modbus_manager.server_count(),
            "deployed_targets": syncer.deployed_count(),
            "plans_seen": syncer.last_plan_count,
            "messages_processed": consumer.messages_processed,
            "points_written": consumer.points_written,
            "bad_quality_count": consumer.bad_quality_count,
            "last_consumer_error": consumer.last_error,
            "last_sync_error": syncer.last_error,
            "targets": modbus_manager.runtime_snapshot(),
            "config": {
                "backend_api_base": settings.backend_api_base,
                "nats_subject": settings.nats_subject,
                "catalog_refresh_sec": settings.catalog_refresh_sec,
                "default_listen_host": settings.default_listen_host,
            },
        }

    def _runtime_for(target_id: int) -> dict:
        return {
            "target_id": target_id,
            "server_running": modbus_manager.is_running(target_id),
            "connected_clients": modbus_manager.connected_clients(target_id),
        }

    health = start_health_server(
        host=settings.health_host,
        port=settings.health_port,
        snapshot=_health,
        runtime_for=_runtime_for,
    )
    logger.info(
        "modbus_outbound_starting version=%s health=%s:%d",
        __version__, settings.health_host, settings.health_port,
    )

    stop_event = asyncio.Event()

    def _on_signal(_signo: int, _frame=None) -> None:
        loop.call_soon_threadsafe(stop_event.set)

    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, _on_signal, sig)
        except (NotImplementedError, RuntimeError):
            # Windows'ta loop.add_signal_handler yok.
            signal.signal(sig, _on_signal)

    syncer_task = asyncio.create_task(syncer.run_forever(), name="modbus-plan-syncer")
    consumer.start()

    try:
        await stop_event.wait()
    finally:
        logger.info("modbus_outbound_shutting_down")
        syncer.request_stop()
        consumer.stop()
        await asyncio.wait([syncer_task], timeout=5)
        await modbus_manager.undeploy_all()
        try:
            health.shutdown()
        except Exception:  # noqa: BLE001
            pass


def _validate_required_secrets() -> None:
    """Placeholder/bos secret'ta fail-fast — sessiz 401 dongusune girmemek icin."""
    placeholders = ("change-me", "please-change-me", "change-this", "your-secret")

    def _is_placeholder(value: str) -> bool:
        v = (value or "").strip().lower()
        return (not v) or v.startswith(placeholders)

    errors: list[str] = []
    if _is_placeholder(SETTINGS.internal_service_token):
        errors.append(
            "INTERNAL_SERVICE_TOKEN set edilmemis veya placeholder; backend 401 "
            "doner ve adres plani hic yuklenemez."
        )
    if not (SETTINGS.nats_url or "").strip():
        errors.append("NATS_URL set edilmemis; telemetri akisi alinamaz.")
    if errors:
        msg = "\n  - ".join(errors)
        print(f"modbus-outbound ZORUNLU KONFIGURASYON EKSIK:\n  - {msg}", flush=True)
        raise SystemExit(2)


def main() -> None:
    _configure_logging()
    _validate_required_secrets()
    try:
        asyncio.run(_async_main())
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
