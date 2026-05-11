"""Servis konfigurasyonu - tum env okumalari tek yerde toplanir."""

from __future__ import annotations

import os
from dataclasses import dataclass


def _get_int(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw is None or raw == "":
        return default
    try:
        return int(raw)
    except ValueError:
        return default


def _get_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in ("1", "true", "yes", "on")


@dataclass(frozen=True)
class Settings:
    # ---- NATS JetStream (telemetri kaynagi) -------------------------------
    # tag-engine cikisini (telemetry.normalized.<gw>) tuketir; IEC 104 server
    # manager'a koprü ile aktarir.
    nats_url: str
    nats_subject: str  # wildcard: hsl.telemetry.normalized.>
    nats_durable: str  # consumer durable adi

    # ---- RabbitMQ (LEGACY — telemetri akisi JetStream'e tasindi) ----------
    # Asagidaki alanlar geriye uyumluluk icin saklaniyor; runtime'da kullanilmiyor.
    rabbit_url: str
    exchange: str
    incoming_topic: str
    queue_name: str
    dlx_exchange: str
    prefetch_count: int

    # ---- Backend-api -------------------------------------------------------
    backend_api_base: str
    internal_service_token: str
    catalog_refresh_sec: int

    # ---- Health ------------------------------------------------------------
    health_host: str
    health_port: int

    # ---- IEC 104 -----------------------------------------------------------
    # Birden fazla outbound target backend'de tanimlidir; bu servis hepsini
    # ayni anda host eder. listen_host backend'den NULL gelirse asagidaki
    # default kullanilir.
    default_listen_host: str
    # SCADA tarafi STARTDT_ACT yollamadan once spontaneous yayilim kapali
    # tutulur. Buradaki iki parametre IEC 104'un t1/t2/t3 zamanlayicilarinin
    # bizim implementasyonumuzdaki kabaca karsiliklaridir; bu surumde zarar
    # vermeden default kalabilirler.
    keepalive_interval_sec: int

    @classmethod
    def from_env(cls) -> "Settings":
        return cls(
            nats_url=os.getenv("NATS_URL", "nats://localhost:4222"),
            nats_subject=os.getenv(
                "NATS_SUBJECT_TELEMETRY_NORMALIZED", "hsl.telemetry.normalized.>"
            ),
            nats_durable=os.getenv("NATS_IEC104_DURABLE", "iec104-outbound-bridge"),
            rabbit_url=os.getenv("RABBITMQ_URL", ""),
            exchange=os.getenv("RABBITMQ_EXCHANGE", "hsl.events"),
            incoming_topic=os.getenv("IEC104_INCOMING_TOPIC", "telemetry.received"),
            queue_name=os.getenv("IEC104_QUEUE", "hsl.iec104_outbound.telemetry"),
            dlx_exchange=os.getenv("RABBITMQ_DLX_EXCHANGE", "hsl.events.dlx"),
            prefetch_count=_get_int("IEC104_PREFETCH", 50),
            backend_api_base=os.getenv("BACKEND_API_BASE", "http://127.0.0.1:8000/api/v1"),
            internal_service_token=os.getenv(
                "INTERNAL_SERVICE_TOKEN", "change-me-internal-token"
            ),
            catalog_refresh_sec=_get_int("IEC104_CATALOG_REFRESH_SEC", 30),
            health_host=os.getenv("WORKER_HEALTH_HOST", "127.0.0.1"),
            health_port=_get_int("WORKER_HEALTH_PORT", 8013),
            default_listen_host=os.getenv("IEC104_DEFAULT_LISTEN_HOST", "0.0.0.0"),
            keepalive_interval_sec=_get_int("IEC104_KEEPALIVE_SEC", 20),
        )


SETTINGS = Settings.from_env()
