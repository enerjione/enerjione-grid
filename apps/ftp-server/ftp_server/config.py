"""FTP sunucusu konfigurasyonu — tum env okumalari tek yerde.

Deneme surumu: tek ortak FTP kullanici/parola (.env'den). Tum cihazlar ayni
hesapla baglanir; her cihaz kendi SN alt dizinine yazar (orn /SN20/FOTA/).
"""

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
    # ---- FTP kimlik + kok dizin -------------------------------------------
    ftp_user: str
    ftp_password: str
    ftp_root: str            # cihazlarin config yazacagi kok dizin (volume mount)


    # ---- Ag ---------------------------------------------------------------
    listen_host: str         # container icinde 0.0.0.0
    listen_port: int         # kontrol portu (21)
    # Pasif mod veri portu araligi. Cihaz pasif moddaysa bu aralik host'a da
    # map edilmeli (docker-compose ports). masquerade_address = cihazin gordugu
    # dis IP (NAT arkasindaysa); bos ise pasif adres otomatik.
    pasv_min_port: int
    pasv_max_port: int
    masquerade_address: str  # bos = otomatik

    # ---- Olay bildirimi ---------------------------------------------------
    # Cihaz dosya yazdiginda/aldiginda backend'e bildirilir. Bos birakilirsa
    # FTP sunucusu calismaya devam eder ama arayuzde hicbir hareket gorunmez
    # (baslangicta uyari loglanir).
    backend_url: str
    internal_service_token: str

    # ---- Health -----------------------------------------------------------
    health_host: str
    health_port: int

    @classmethod
    def from_env(cls) -> "Settings":
        return cls(
            ftp_user=os.getenv("FTP_USER", "device"),
            ftp_password=os.getenv("FTP_PASSWORD", ""),
            ftp_root=os.getenv("FTP_ROOT", "/data/ftp"),
            listen_host=os.getenv("FTP_LISTEN_HOST", "0.0.0.0"),
            listen_port=_get_int("FTP_LISTEN_PORT", 21),
            pasv_min_port=_get_int("FTP_PASV_MIN_PORT", 30000),
            pasv_max_port=_get_int("FTP_PASV_MAX_PORT", 30009),
            masquerade_address=os.getenv("FTP_MASQUERADE_ADDRESS", ""),
            backend_url=os.getenv("E1_BACKEND_URL", "http://backend-api:8000/api/v1"),
            internal_service_token=os.getenv("INTERNAL_SERVICE_TOKEN", ""),
            health_host=os.getenv("WORKER_HEALTH_HOST", "0.0.0.0"),
            health_port=_get_int("WORKER_HEALTH_PORT", 8015),
        )


SETTINGS = Settings.from_env()
