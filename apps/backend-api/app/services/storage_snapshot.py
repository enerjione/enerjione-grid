"""Bilesen bazli depolama gorunurlugu — Disk Guardian'in "OBSERVE" ayagi.

NEDEN AYRI MODUL
----------------
`disk_guard` KARAR verir (seviye, temizlik). Bu modul yalnizca OLCER. Ayirmak
iki seyi saglar: olcum kodu buyudukce karar mantigini kalabaliklastirmaz, ve
API tarafi temizlik fonksiyonlarini hic import etmeden gorunurluk alabilir.

NEDEN ONBELLEKLI
----------------
Bilesen boyutlari agac taramasi ister (sahada harita onbelleginde 6.365 dosya
var). Bunu her HTTP istegine baglamak, Sistem Durumu sayfasini acan her
kullanicinin diski taramasi demek olurdu. Anlik goruntuyu arka plan tick'i
tazeler; API onbellekten okur.

NE OLCULMEZ VE NEDEN
--------------------
Docker imajlari, build cache ve journald. Backend konteyneri
`/var/run/docker.sock` MOUNT ETMIYOR (bkz. docker-compose.yml backend-api
volumes) ve journald host'ta yasar. Bu kalemler backend'den ne olculebilir ne
temizlenebilir — host katmani isidir. Uydurma bir deger uretmek yerine
`measured=False` ile acikca "bilinmiyor" deriz.

JETSTREAM KAPSAM SINIRI
-----------------------
Burada stream/consumer semantigi, ACK, redelivery, DLQ ya da overflow
YORUMLANMAZ ve hicbir sey DEGISTIRILMEZ. Tek soru: "bu bilesen host disk
butcesinden ne kadar yiyor?" JetStream'in kendi politikasi (max_age /
max_bytes / discard) JetStream FAT sahibinin alanidir.
"""

from __future__ import annotations

import json
import logging
import os
import re
import time
import urllib.request
from urllib.parse import urlparse

from app.core.config import settings

logger = logging.getLogger(__name__)

#: Cihazin indirmeyi bekledigi AKTIF dosyalar (FTP-T0). Bunlar hicbir disk
#: baskisinda silinmez; silmek sahadaki cihazi yapilandirmasiz birakir.
_FTP_AKTIF_RE = re.compile(
    r"^[A-Za-z0-9]{1,20}_(Configuration\.csv|DNP3_settings\.bin|Firmware\.utf)$"
)

_ANLIK: dict[str, object] = {"at": 0.0, "data": None}


def _dizin_boyutu(yol: str) -> tuple[int, int]:
    """(bayt, dosya_sayisi) — okunamayan girdiler atlanir."""
    toplam = 0
    adet = 0
    for kok, _dirs, dosyalar in os.walk(yol):
        for ad in dosyalar:
            try:
                toplam += os.stat(os.path.join(kok, ad)).st_size
                adet += 1
            except OSError:
                continue
    return toplam, adet


def ftp_dagilimi() -> dict:
    """FTP-T0 / T2 / T3 kirilimi — SINIFLANDIRMA, silme karari degil.

    T1 (config gecmisi) bilincli olarak YOK: gecmis FTP'de tutulmuyor,
    PostgreSQL'de surumleniyor (bkz. device_config_service). Dolayisiyla
    FTP tarafinda "config gecmisi retention'i" diye bir sey gerekmiyor.

    T2 (siniflandirma disi cihaz yuklemesi) yalnizca SAYILIR. Urun karari
    henuz verilmedi; korlemesine silmek veri kaybi riskidir.
    """
    kok = os.getenv("FTP_ROOT", "/data/ftp")
    sonuc: dict = {
        "path": kok,
        "measured": os.path.isdir(kok),
        "t0_active_bytes": 0, "t0_active_count": 0,
        "t2_unclassified_bytes": 0, "t2_unclassified_count": 0,
        "t3_temp_bytes": 0, "t3_temp_count": 0,
    }
    if not sonuc["measured"]:
        return sonuc
    for kok_dizin, _dirs, dosyalar in os.walk(kok):
        for ad in dosyalar:
            try:
                boyut = os.stat(os.path.join(kok_dizin, ad)).st_size
            except OSError:
                continue
            if ad.endswith(".tmp") or ad.startswith(".tmp_"):
                sonuc["t3_temp_bytes"] += boyut
                sonuc["t3_temp_count"] += 1
            elif _FTP_AKTIF_RE.match(ad):
                sonuc["t0_active_bytes"] += boyut
                sonuc["t0_active_count"] += 1
            else:
                sonuc["t2_unclassified_bytes"] += boyut
                sonuc["t2_unclassified_count"] += 1
    return sonuc


def postgres_dagilimi() -> dict:
    """Veritabani ve historian katmanlari — SALT OKUNUR sorgular.

    1 DAKIKALIK OZET AYRI GOSTERILIR: sahada olculdu, ham historian 32 MB
    iken 1dk ozeti 29 MB — yani "kucuk ozet" DEGIL. Ustelik 4 kat uzun
    yasiyor (365 gun / 90 gun). Tek bir "historian" rakami bu gercegi
    gizlerdi ve kapasite plani yanlis cikardi.
    """
    from sqlalchemy import text

    from app.db.session import engine

    cikti: dict = {"measured": False}
    try:
        with engine.connect() as c:
            cikti["database_bytes"] = int(
                c.execute(text("SELECT pg_database_size(current_database())")).scalar() or 0
            )
            for anahtar, tablo in (
                ("historian_raw_bytes", "telemetry_history"),
                ("historian_1m_bytes", "telemetry_history_1m"),
                ("historian_1h_bytes", "telemetry_history_1h"),
            ):
                try:
                    cikti[anahtar] = int(
                        c.execute(text("SELECT hypertable_size(:t)"), {"t": tablo}).scalar()
                        or 0
                    )
                except Exception:  # noqa: BLE001
                    # TimescaleDB yok ya da tablo hypertable degil.
                    cikti[anahtar] = None
            cikti["measured"] = True
    except Exception:  # noqa: BLE001
        logger.warning("storage_snapshot_pg_failed", exc_info=True)
    return cikti


def rabbitmq_durumu() -> dict:
    """RabbitMQ disk sagligi — SALT OKUNUR management API sorgusu.

    NEDEN GORUNUR OLMALI: RabbitMQ'nun `disk_free_limit` VARSAYILANI 50 MB.
    456 GB'lik bir diskte bu ancak %99,99 dolulukta alarm demektir — Disk
    Guardian'in ACIL esiginden (%95) cok sonra, yani bir koruma katmani
    olarak fiilen olu. Gorunur kilmak ilk adim; urun-farkinda deger
    `infra/rabbitmq/rabbitmq.conf` ile veriliyor.

    Management API kapaliysa bu bir ARIZA DEGIL, olcum yoklugudur.
    """
    cikti: dict = {"measured": False}
    try:
        u = urlparse(settings.rabbitmq_url)
        host = u.hostname or "rabbitmq"
        url = (
            f"http://{host}:15672/api/nodes?columns="
            "name,disk_free,disk_free_limit,disk_free_alarm,mem_used,mem_limit,mem_alarm"
        )
        yonetici = urllib.request.HTTPPasswordMgrWithDefaultRealm()
        yonetici.add_password(None, url, u.username or "guest", u.password or "guest")
        opener = urllib.request.build_opener(
            urllib.request.HTTPBasicAuthHandler(yonetici)
        )
        with opener.open(url, timeout=5) as yanit:
            dugumler = json.load(yanit)
        if dugumler:
            d = dugumler[0]
            cikti.update(
                measured=True,
                disk_free_bytes=d.get("disk_free"),
                disk_free_limit_bytes=d.get("disk_free_limit"),
                disk_alarm=bool(d.get("disk_free_alarm")),
                memory_alarm=bool(d.get("mem_alarm")),
            )
    except Exception as exc:  # noqa: BLE001
        cikti["error"] = type(exc).__name__
    return cikti


def jetstream_durumu() -> dict:
    """JetStream'in HOST DISK kullanimi — YALNIZCA GOZLEM.

    Disk Guardian JetStream verisini ASLA budamaz. Buradaki alanlar host
    disk butcesi icindir: toplam store, rezerve, akis basina mevcut bayt ve
    yapilandirilmis tavan.
    """
    cikti: dict = {"measured": False}
    try:
        u = urlparse(settings.nats_url)
        host = u.hostname or "nats"
        with urllib.request.urlopen(
            f"http://{host}:8222/jsz?streams=1&config=1", timeout=5
        ) as yanit:
            d = json.load(yanit)
        akislar = []
        for hesap in d.get("account_details", []):
            for s in hesap.get("stream_detail", []):
                cfg = s.get("config", {}) or {}
                durum = s.get("state", {}) or {}
                tavan = cfg.get("max_bytes", 0) or 0
                mevcut = durum.get("bytes", 0) or 0
                akislar.append({
                    "name": s.get("name"),
                    "bytes": mevcut,
                    "max_bytes": tavan,
                    "percent_of_cap": (
                        round(mevcut / tavan * 100, 1) if tavan > 0 else None
                    ),
                })
        cikti.update(
            measured=True,
            store_bytes=d.get("storage", 0),
            reserved_bytes=d.get("reserved_storage", 0),
            configured_cap_bytes=sum(a["max_bytes"] for a in akislar if a["max_bytes"] > 0),
            streams=akislar,
        )
    except Exception as exc:  # noqa: BLE001
        cikti["error"] = type(exc).__name__
    return cikti


def _uret() -> dict:
    """Bilesen bazli depolama haritasi. PAHALI — arka planda cagrilir."""
    from app.services import disk_guard

    durum = disk_guard.evaluate()
    backup_dir = os.getenv("BACKUP_DIR", "/var/lib/e1-backups")
    yedek_bayt, yedek_adet = (
        _dizin_boyutu(backup_dir) if os.path.isdir(backup_dir) else (0, 0)
    )
    try:
        from app.services import map_tile_service

        harita_bayt = map_tile_service.cache_size_bytes()
    except Exception:  # noqa: BLE001
        harita_bayt = 0

    return {
        "filesystem": durum.to_dict() if durum else None,
        "postgres": postgres_dagilimi(),
        "nats_jetstream": jetstream_durumu(),
        "rabbitmq": rabbitmq_durumu(),
        "backups": {"path": backup_dir, "bytes": yedek_bayt, "count": yedek_adet},
        "map_tiles": {"bytes": harita_bayt},
        "ftp": ftp_dagilimi(),
        # Host katmani — backend konteynerinden ERISILEMEZ.
        "host_only": {
            "measured": False,
            "components": ["docker_images", "docker_build_cache", "journald"],
            "note": (
                "Backend konteyneri docker.sock mount etmiyor ve journald "
                "host'ta; bu kalemler host katmaninda ele alinir."
            ),
        },
        "generated_at": time.time(),
    }


def snapshot(*, force: bool = False) -> dict | None:
    """Onbellekli depolama haritasi. API bunu okur, agac taramasi KOSTURMAZ."""
    an = time.monotonic()
    if not force and _ANLIK["data"] is not None:
        yas = an - float(_ANLIK["at"] or 0.0)
        if yas < max(60, settings.disk_guard_snapshot_interval_sec):
            return _ANLIK["data"]  # type: ignore[return-value]
    try:
        _ANLIK["data"] = _uret()
        _ANLIK["at"] = an
    except Exception:  # noqa: BLE001
        logger.exception("storage_snapshot_failed")
    return _ANLIK["data"]  # type: ignore[return-value]


def refresh() -> None:
    """Arka plan tazeleme — disk_guard tick'i cagirir."""
    snapshot(force=True)
