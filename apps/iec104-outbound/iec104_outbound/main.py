"""iec104-outbound entrypoint.

Akis:
  1. Health HTTP server'i baslat (snapshot icin durum saglayicilara referans).
  2. Asyncio loop'unda IEC 104 server manager + catalog syncer'i baslat.
  3. Pika BlockingConnection consumer'i ayri daemon thread'de baslat.
  4. SIGINT/SIGTERM gelince hepsini graceful indir.

Tag-engine `telemetry.received` -> consumer -> manager.update_point_threadsafe ->
asyncio loop -> her aktif IEC 104 server'i ilgili COT=3 ASDU yayinlar.
"""

from __future__ import annotations

import asyncio
import logging
import signal
import sys
from datetime import datetime, timezone

from iec104_outbound import __version__
from iec104_outbound.catalog import CatalogClient, CatalogSyncer
from iec104_outbound.config import SETTINGS
from iec104_outbound.consumer import TelemetryConsumer
from iec104_outbound.health import start_health_server
from iec104_outbound.server import manager as iec104_manager


def _configure_logging() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
        stream=sys.stdout,
    )


async def _async_main() -> None:
    logger = logging.getLogger("iec104_outbound.main")
    settings = SETTINGS

    loop = asyncio.get_running_loop()
    iec104_manager.bind_loop(loop)

    catalog = CatalogClient(
        base_url=settings.backend_api_base,
        service_token=settings.internal_service_token,
    )
    syncer = CatalogSyncer(
        catalog=catalog,
        manager=iec104_manager,
        default_listen_host=settings.default_listen_host,
        refresh_sec=settings.catalog_refresh_sec,
    )
    consumer = TelemetryConsumer(settings=settings, manager=iec104_manager)

    # Health snapshot icin closure
    started_at = datetime.now(timezone.utc).isoformat()

    def _health() -> dict:
        return {
            "status": "ok",
            "service": "iec104-outbound",
            "version": __version__,
            "started_at": started_at,
            "active_servers": iec104_manager.server_count(),
            "deployed_targets": syncer.deployed_count(),
            "messages_processed": consumer.messages_processed,
            "last_consumer_error": consumer.last_error,
            "config": {
                "backend_api_base": settings.backend_api_base,
                "queue": settings.queue_name,
                "incoming_topic": settings.incoming_topic,
                "catalog_refresh_sec": settings.catalog_refresh_sec,
                "default_listen_host": settings.default_listen_host,
            },
        }

    health = start_health_server(
        host=settings.health_host,
        port=settings.health_port,
        snapshot=_health,
    )
    logger.info(
        "iec104_outbound_starting version=%s health=%s:%d",
        __version__, settings.health_host, settings.health_port,
    )

    # Sonsuza dek calisir; sinyal gelince stop edilir.
    stop_event = asyncio.Event()

    def _on_signal(_signo: int, _frame=None) -> None:
        loop.call_soon_threadsafe(stop_event.set)

    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, _on_signal, sig)
        except (NotImplementedError, RuntimeError):
            # Windows'ta loop.add_signal_handler yok; signal.signal kullan.
            signal.signal(sig, _on_signal)

    syncer_task = asyncio.create_task(syncer.run_forever(), name="catalog-syncer")
    consumer.start()

    try:
        await stop_event.wait()
    finally:
        logger.info("iec104_outbound_shutting_down")
        syncer.request_stop()
        consumer.stop()
        await asyncio.wait([syncer_task], timeout=5)
        await iec104_manager.undeploy_all()
        try:
            health.shutdown()
        except Exception:
            pass


def main() -> None:
    _configure_logging()
    try:
        asyncio.run(_async_main())
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
