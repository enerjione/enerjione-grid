"""Servis konfigurasyonu — tum env okumalari tek yerde."""

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


@dataclass(frozen=True)
class Settings:
    # ---- NATS JetStream (telemetri kaynagi) -------------------------------
    # tag-engine cikisi: e1.telemetry.normalized.<gw>
    nats_url: str
    nats_subject: str
    nats_durable: str

    # ---- Backend-api -------------------------------------------------------
    # Adres planlari buradan gelir; worker adres HESAPLAMAZ.
    backend_api_base: str
    internal_service_token: str
    catalog_refresh_sec: int
    # Son bilinen degerlerin (`/internal/modbus-values`) register'lara
    # yazilma periyodu. Canli akis yalnizca DEGISIM oldugunda akar; bu tur
    # degismeyen sinyallerin register'da 0 kalmasini engeller.
    # 0 (veya negatif) = kapali — o zaman degismeyen sinyaller SCADA'da
    # sonsuza dek 0 gorunur, bilerek kapatilmadikca dokunulmamali.
    snapshot_refresh_sec: int

    # ---- Health ------------------------------------------------------------
    health_host: str
    health_port: int

    # ---- Modbus ------------------------------------------------------------
    # Hedefin listen_host'u backend'de bos ise bu kullanilir.
    default_listen_host: str

    @classmethod
    def from_env(cls) -> "Settings":
        return cls(
            nats_url=os.getenv("NATS_URL", "nats://localhost:4222"),
            nats_subject=os.getenv(
                "NATS_SUBJECT_TELEMETRY_NORMALIZED", "e1.telemetry.normalized.>"
            ),
            nats_durable=os.getenv("NATS_MODBUS_DURABLE", "modbus-outbound-bridge"),
            backend_api_base=os.getenv(
                "BACKEND_API_BASE", "http://127.0.0.1:8000/api/v1"
            ),
            internal_service_token=os.getenv(
                "INTERNAL_SERVICE_TOKEN", "change-me-internal-token"
            ),
            catalog_refresh_sec=_get_int("MODBUS_CATALOG_REFRESH_SEC", 30),
            snapshot_refresh_sec=_get_int("MODBUS_SNAPSHOT_REFRESH_SEC", 30),
            health_host=os.getenv("WORKER_HEALTH_HOST", "127.0.0.1"),
            health_port=_get_int("WORKER_HEALTH_PORT", 8017),
            default_listen_host=os.getenv("MODBUS_DEFAULT_LISTEN_HOST", "0.0.0.0"),
        )


SETTINGS = Settings.from_env()
