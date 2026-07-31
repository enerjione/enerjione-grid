"""Disk guard — "ne kadar veri gelirse gelsin disk dolmasin" son emniyet subabi.

BU KATMAN NEDEN VAR
-------------------
Sistemde zaten bir suru tavan var: tablo TTL'leri (telemetry_retention),
historian retention/compression politikalari, NATS stream `max_bytes`,
yedek retention'i, harita karo onbellegi tavani. Hepsi "normal sartlarda
dolmaz" garantisi verir.

Disk guard bunlarin hicbirine GUVENMEZ. Tavanlardan biri yanlis
hesaplanmis, bir politika sessizce kurulmamis (0007'nin timescaledb'siz
kosup damgalanmasi gibi) ya da beklenmedik bir bilesen alan yiyor olabilir.
Guard gercek bos alani olcer ve dolmaya YAKLASILDIGINDA mudahale eder.

TASARIM ILKESI: SISTEM DURMAZ
-----------------------------
Amac "disk dolunca sistemi guvenle durdurmak" DEGIL, "hic dolmamasini
saglayip calismaya devam etmek". Bu yuzden mudahale sirasi YENIDEN
URETILEBILIR veriden baslar ve asla operasyonel/denetim verisine dokunmaz.

SEVIYELER (rezerve gore, mutlak yuzde degil)
--------------------------------------------
    rezerv = max(toplam x %10, mutlak_taban)

    bos >= 2 x rezerv   -> ok         : hicbir sey yapma
    bos >= 1 x rezerv   -> uyari      : LOGLA + olay kaydi. SILME YOK.
    bos >= 0.5 x rezerv -> agresif    : retention'lari KISALTILMIS pencereyle
                                        hemen kostur (normalde periyodu bekler)
    bos <  0.5 x rezerv -> acil       : agresif + yeniden uretilebilirleri sil
                                        (harita onbellegi, fazla yedekler)

NELERE ASLA DOKUNULMAZ
----------------------
  * `system_events`      — denetim izi (kim ne yapti)
  * lisans dosyalari     — silinirse sistem kendini acamaz
  * `project_settings`, config, kullanicilar, sorumluluk alanlari
  * alarm / ariza gecmisi — operasyonel kayit
  * `telemetry_history` ve ozetleri — MUSTERININ ANALIZ VERISI. Acil
    seviyede bile OTOMATIK silinmez; bunun yerine operatore kritik uyari
    yazilir. Retention politikasini kisaltmak bir URUN karari olmali,
    otomatik bir refleks degil.

Silinebilir sayilanlar yalnizca YENIDEN URETILEBILIR olanlardir:
  * harita karo onbellegi (internetten yeniden inebilir)
  * fazla yedek dosyalari (en yeni BASARILI yedek her kosulda korunur)
  * idempotency defteri / canli deger penceresi (zaten kisa omurlu)
"""

from __future__ import annotations

import logging
import os
import shutil
from dataclasses import dataclass, field

from app.core.config import settings

logger = logging.getLogger(__name__)

LEVEL_OK = "ok"
LEVEL_WARN = "warn"
LEVEL_AGGRESSIVE = "aggressive"
LEVEL_EMERGENCY = "emergency"

# Agresif/acil seviyede kullanilan KISALTILMIS retention pencereleri.
# Normal degerlerin yerine gecmez; yalnizca o tetikte uygulanir.
_AGGRESSIVE_PROCESSED_HOURS = 6
_AGGRESSIVE_TELEMETRY_MINUTES = 10


@dataclass
class DiskStatus:
    path: str
    total_bytes: int
    free_bytes: int
    reserve_bytes: int
    level: str
    actions: list[str] = field(default_factory=list)

    @property
    def used_ratio(self) -> float:
        if self.total_bytes <= 0:
            return 0.0
        return 1.0 - (self.free_bytes / self.total_bytes)

    def to_dict(self) -> dict:
        return {
            "path": self.path,
            "total_bytes": self.total_bytes,
            "free_bytes": self.free_bytes,
            "reserve_bytes": self.reserve_bytes,
            "used_ratio": round(self.used_ratio, 4),
            "level": self.level,
            "actions": self.actions,
        }


def guarded_path() -> str:
    """Olculecek yol.

    Oncelik: acik ayar -> BACKUP_DIR (gercek bir docker volume mount'u,
    container'in `/` overlay'inden daha dogru gosterge) -> kok.
    """
    if settings.disk_guard_path.strip():
        return settings.disk_guard_path.strip()
    backup_dir = os.getenv("BACKUP_DIR", "").strip()
    if backup_dir and os.path.isdir(backup_dir):
        return backup_dir
    return "C:\\" if os.name == "nt" else "/"


def reserve_for(total_bytes: int) -> int:
    """Bos kalmasi gereken alan: max(toplam x yuzde, mutlak taban).

    Yuzde farkli disk boyutlarinda dogru olcekler; mutlak taban cok kucuk
    disklerde yuzdenin yetersiz kalmasini onler (Postgres bakim islemleri
    calisma alani ister).
    """
    pct = max(0, settings.disk_guard_reserve_percent) / 100.0
    floor = max(0, settings.disk_guard_reserve_min_gb) * 1024**3
    return max(int(total_bytes * pct), floor)


def classify(free_bytes: int, reserve_bytes: int) -> str:
    if reserve_bytes <= 0:
        return LEVEL_OK
    if free_bytes >= 2 * reserve_bytes:
        return LEVEL_OK
    if free_bytes >= reserve_bytes:
        return LEVEL_WARN
    if free_bytes >= reserve_bytes // 2:
        return LEVEL_AGGRESSIVE
    return LEVEL_EMERGENCY


def evaluate(path: str | None = None) -> DiskStatus | None:
    """Anlik disk durumu. Olculemezse None (guard sessizce devre disi)."""
    target = path or guarded_path()
    try:
        usage = shutil.disk_usage(target)
    except OSError as exc:
        logger.warning("disk_guard_stat_failed path=%s error=%s", target, exc)
        return None
    reserve = reserve_for(usage.total)
    return DiskStatus(
        path=target,
        total_bytes=int(usage.total),
        free_bytes=int(usage.free),
        reserve_bytes=reserve,
        level=classify(int(usage.free), reserve),
    )


# --------------------------------------------------------------- mudahale


def _relieve_aggressive() -> list[str]:
    """Retention'lari KISALTILMIS pencereyle hemen kostur.

    Normalde bu purge'ler kendi periyotlarini bekler (10 dk / 6 saat). Disk
    baskisi altinda beklemek anlamsiz; ayrica pencereyi gecici olarak
    kisaltiyoruz. Kalici ayar DEGISMEZ — bir sonraki normal tetik yine
    yapilandirilmis degerlerle kosar.
    """
    from app.services import telemetry_retention

    done: list[str] = []
    worker = telemetry_retention.RetentionWorker()

    # Idempotency defteri: gercek redelivery penceresi 10 DAKIKA oldugu icin
    # 6 saat hala fazlasiyla guvenli.
    try:
        n = worker.purge_processed_messages(retention_hours=_AGGRESSIVE_PROCESSED_HOURS)
        done.append(f"processed_messages<-{_AGGRESSIVE_PROCESSED_HOURS}s: {n}")
    except Exception:  # noqa: BLE001
        logger.exception("disk_guard_processed_purge_failed")

    # Canli deger penceresi. Her sinyalin SON degeri yine korunur, yani
    # kisaltmak canli ekrani bosaltmaz.
    try:
        n = worker.purge_telemetry(retention_minutes=_AGGRESSIVE_TELEMETRY_MINUTES)
        done.append(f"telemetry<-{_AGGRESSIVE_TELEMETRY_MINUTES}dk: {n}")
    except Exception:  # noqa: BLE001
        logger.exception("disk_guard_telemetry_purge_failed")

    # Yayinlanmis outbox satirlari — tamamen yeniden uretilemez degil, zaten
    # yayinlanmis kayitlar; published=False'a ASLA dokunulmaz.
    try:
        from datetime import datetime, timedelta, timezone

        from app.db.session import SessionLocal
        from app.services.outbox_service import purge_published_outbox

        db = SessionLocal()
        try:
            n = purge_published_outbox(
                db, before=datetime.now(timezone.utc) - timedelta(hours=1), limit=50_000
            )
            done.append(f"outbox_published: {n}")
        finally:
            db.close()
    except Exception:  # noqa: BLE001
        logger.exception("disk_guard_outbox_purge_failed")

    return done


def _relieve_emergency() -> list[str]:
    """Yalnizca YENIDEN URETILEBILIR veriyi siler."""
    done: list[str] = []

    # 1) Harita karo onbellegi — internetten yeniden inebilir, veri kaybi yok.
    try:
        from app.services import map_tile_service

        before = map_tile_service.cache_size_bytes()
        map_tile_service.clear_cache()
        done.append(f"harita_onbellegi_temizlendi: {before} bayt")
    except Exception:  # noqa: BLE001
        logger.exception("disk_guard_map_cache_clear_failed")

    # 2) Fazla yedekler. En yeni BASARILI yedek her kosulda korunur —
    # apply_retention en yenileri tutar, biz yalnizca tutulan SAYIYI
    # dusuruyoruz.
    try:
        from app.db.session import SessionLocal
        from app.services.backup_service import apply_retention

        keep = max(1, settings.disk_guard_emergency_backup_keep)
        db = SessionLocal()
        try:
            n = apply_retention(db, keep)
            done.append(f"eski_yedekler_silindi(keep={keep}): {n}")
        finally:
            db.close()
    except Exception:  # noqa: BLE001
        logger.exception("disk_guard_backup_retention_failed")

    return done


def _record(status: DiskStatus) -> None:
    """Operator gorunurlugu — Olaylar ekraninda gorulsun.

    Denetim kaydina yazmak disk baskisi altinda ironik gorunebilir ama
    satir maliyeti ihmal edilebilir ve "disk neden bosaldi / neden doldu"
    sorusunun tek cevabi bu kayit olur.
    """
    if status.level == LEVEL_OK:
        return
    severity = {
        LEVEL_WARN: "warning",
        LEVEL_AGGRESSIVE: "warning",
        LEVEL_EMERGENCY: "critical",
    }.get(status.level, "info")
    try:
        from app.db.session import SessionLocal
        from app.services.event_service import record_event

        db = SessionLocal()
        try:
            free_gb = status.free_bytes / 1024**3
            total_gb = status.total_bytes / 1024**3
            record_event(
                db,
                category="system",
                event_type=f"disk_guard_{status.level}",
                severity=severity,
                message=(
                    f"Disk doluluk uyarisi ({status.level}): "
                    f"{free_gb:.1f} GB bos / {total_gb:.1f} GB toplam "
                    f"(%{status.used_ratio * 100:.1f} dolu)"
                ),
                metadata={
                    "path": status.path,
                    "free_bytes": status.free_bytes,
                    "total_bytes": status.total_bytes,
                    "reserve_bytes": status.reserve_bytes,
                    "actions": status.actions,
                },
            )
            db.commit()
        finally:
            db.close()
    except Exception:  # noqa: BLE001
        logger.exception("disk_guard_event_record_failed")


def tick() -> DiskStatus | None:
    """Bir kontrol turu. Retention worker tarafindan periyodik cagrilir."""
    if not settings.disk_guard_enabled:
        return None
    status = evaluate()
    if status is None:
        return None

    if status.level == LEVEL_OK:
        logger.debug(
            "disk_guard_ok free=%.1fGB reserve=%.1fGB",
            status.free_bytes / 1024**3,
            status.reserve_bytes / 1024**3,
        )
        return status

    logger.warning(
        "disk_guard_level=%s path=%s free=%.1fGB total=%.1fGB reserve=%.1fGB",
        status.level,
        status.path,
        status.free_bytes / 1024**3,
        status.total_bytes / 1024**3,
        status.reserve_bytes / 1024**3,
    )

    if status.level in (LEVEL_AGGRESSIVE, LEVEL_EMERGENCY):
        status.actions.extend(_relieve_aggressive())
    if status.level == LEVEL_EMERGENCY:
        status.actions.extend(_relieve_emergency())
        # Historian OTOMATIK budanmaz — musterinin analiz verisi. Operatore
        # soyluyoruz ki bilincli bir karar versin.
        logger.critical(
            "disk_guard_emergency — yeniden uretilebilir veri temizlendi ama "
            "alan hala kritik olabilir. Historian retention'i (telemetry_history "
            "90 gun / 1dk ozet 1 yil / 1saat ozet 2 yil) bir URUN karari olarak "
            "gozden gecirilmeli; otomatik kisaltilmadi."
        )

    _record(status)
    return status
