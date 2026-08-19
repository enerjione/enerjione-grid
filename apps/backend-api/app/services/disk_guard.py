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

  * `processed_messages`  — UYGULAMA SEVIYESI IDEMPOTENCY DURUMU. Pencere
    (2 saat) disk baskisiyla KISALTILMAZ; aksi halde ayni girdi, diskin o
    anki doluluguna gore farkli sonuc verirdi (bkz. asagidaki sabitler
    blogundaki 90 dakika ornegi). Kendi periyodik temizligi degismeden
    isler.

Silinebilir sayilanlar yalnizca YENIDEN URETILEBILIR olanlardir:
  * bayat FTP gecici dosyalari (yarim/basarisiz transfer artigi)
  * harita karo onbellegi (internetten yeniden inebilir)
  * canli deger penceresi (`telemetry`; her serinin SON degeri MUAF)
  * yayinlanmis outbox satirlari (teslim edilmis; `published=False` ASLA)
  * fazla yedek dosyalari (en yeni BASARILI yedek her kosulda korunur)
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
#
# `processed_messages` BU LISTEDE YOK — BILINCLI.
# ------------------------------------------------
# Bu tablo bir "kisa omurlu tampon" DEGIL, UYGULAMA SEVIYESI IDEMPOTENCY
# DURUMUDUR. Dedup zinciri uc katmanli ve her katmanin kendi ufku var:
#
#     Nats-Msg-Id            120 saniye   (broker seviyesi)
#     processed_messages     2 SAAT       (uygulama seviyesi)
#     telemetry_history dogal anahtari    (son savunma, ON CONFLICT)
#
# Onceki surumde disk guard bu pencereyi baski altinda 1 saate cekiyordu.
# Bu, SISTEMIN DOGRULUGUNU DISK DOLULUGUNA BAGLAR:
#
#     ayni message_id 90 dakika sonra yeniden yayinlanirsa
#       saglikli disk  -> dedup edilir
#       kritik disk    -> kayit silinmis olabilir -> DUPLICATE TELEMETRI
#
# Yani ayni girdi, diskin o anki durumuna gore farkli sonuc veriyordu.
# Kazanilan alan (600 cihazda ~2,8 GB) bu bedeli haklilastirmaz: disk
# baskisi geciciddir, bozulan veri kalicidir. Pencere artik HER SEVIYEDE
# yapilandirilmis degerdir (2 saat) ve guard ona dokunmaz.
_AGGRESSIVE_TELEMETRY_MINUTES = 10
# Outbox'ta zaten kisa (15 dk) olan pencereyi daha da kisaltmanin anlami yok:
# taban REDELIVERY_WINDOW_SEC (10 dk) ve altina inilemiyor. Buradaki kazanc
# sureyi kisaltmaktan degil, periyodu (60sn) BEKLEMEDEN kosturmaktan geliyor.
_AGGRESSIVE_OUTBOX_MINUTES = 15


@dataclass
class DiskStatus:
    path: str
    total_bytes: int
    free_bytes: int
    reserve_bytes: int
    level: str
    actions: list[str] = field(default_factory=list)
    #: Inode ekseni — Windows'ta ve olculemedigi durumda None.
    inode_percent: float | None = None
    inode_used: int | None = None
    inode_total: int | None = None

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
            "used_bytes": self.total_bytes - self.free_bytes,
            "reserve_bytes": self.reserve_bytes,
            "used_ratio": round(self.used_ratio, 4),
            "used_percent": round(self.used_ratio * 100, 1),
            "inode_percent": (
                round(self.inode_percent, 1) if self.inode_percent is not None else None
            ),
            "inode_used": self.inode_used,
            "inode_total": self.inode_total,
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


#: Seviye siralamasi — "en kotu sinyal kazanir" karsilastirmasi icin.
_SIRA = {LEVEL_OK: 0, LEVEL_WARN: 1, LEVEL_AGGRESSIVE: 2, LEVEL_EMERGENCY: 3}

#: `LEVEL_AGGRESSIVE`in okunabilir adi. Tel uzerindeki deger (olay tipi
#: `disk_guard_aggressive`, bildirim ACIK_LISTE anahtarlari) DEGISMEDI —
#: yeniden adlandirmak mevcut kurulumlarin bildirim kurallarini sessizce
#: kirardi. Kod icinde CRITICAL demek isteyen bu takma adi kullanir.
LEVEL_CRITICAL = LEVEL_AGGRESSIVE


def _en_kotu(*seviyeler: str) -> str:
    return max(seviyeler, key=lambda s: _SIRA.get(s, 0))


def _rezerv_seviyesi(free_bytes: int, reserve_bytes: int) -> str:
    """Rezerve gore seviye — ORIJINAL sozlesme, aynen korundu."""
    if reserve_bytes <= 0:
        return LEVEL_OK
    if free_bytes >= 2 * reserve_bytes:
        return LEVEL_OK
    if free_bytes >= reserve_bytes:
        return LEVEL_WARN
    if free_bytes >= reserve_bytes // 2:
        return LEVEL_AGGRESSIVE
    return LEVEL_EMERGENCY


def _esik_seviyesi(deger: float, uyari: float, kritik: float, acil: float) -> str:
    """Artan bir olcut icin (doluluk yuzdesi, inode yuzdesi) seviye."""
    if deger >= acil:
        return LEVEL_EMERGENCY
    if deger >= kritik:
        return LEVEL_AGGRESSIVE
    if deger >= uyari:
        return LEVEL_WARN
    return LEVEL_OK


def _bos_alan_seviyesi(free_bytes: int) -> str:
    """Azalan bir olcut icin (mutlak bos bayt) seviye."""
    gb = free_bytes / 1024**3
    if gb < settings.disk_guard_emergency_free_gb:
        return LEVEL_EMERGENCY
    if gb < settings.disk_guard_critical_free_gb:
        return LEVEL_AGGRESSIVE
    if gb < settings.disk_guard_warning_free_gb:
        return LEVEL_WARN
    return LEVEL_OK


def classify(
    free_bytes: int,
    reserve_bytes: int,
    *,
    total_bytes: int | None = None,
    inode_percent: float | None = None,
) -> str:
    """Dort sinyali birlestirir; EN KOTU olan kazanir.

    Sinyaller: rezerv modeli, mutlak doluluk yuzdesi, mutlak bos bayt ve
    inode doluluk yuzdesi. Hicbiri digerinin yerine gecmez — gerekcesi
    `config.py`'deki esik blogunda.

    `total_bytes` / `inode_percent` verilmezse yalnizca bilinen sinyaller
    degerlendirilir; boylece eski cagri bicimi (iki konumsal arguman)
    calismaya devam eder.
    """
    seviyeler = [_rezerv_seviyesi(free_bytes, reserve_bytes), _bos_alan_seviyesi(free_bytes)]

    if total_bytes and total_bytes > 0:
        kullanim = (1.0 - free_bytes / total_bytes) * 100.0
        seviyeler.append(
            _esik_seviyesi(
                kullanim,
                settings.disk_guard_warning_used_percent,
                settings.disk_guard_critical_used_percent,
                settings.disk_guard_emergency_used_percent,
            )
        )

    if inode_percent is not None:
        seviyeler.append(
            _esik_seviyesi(
                inode_percent,
                settings.disk_guard_warning_inode_percent,
                settings.disk_guard_critical_inode_percent,
                settings.disk_guard_emergency_inode_percent,
            )
        )

    return _en_kotu(*seviyeler)


def inode_kullanimi(path: str) -> tuple[float | None, int | None, int | None]:
    """(yuzde, kullanilan, toplam) — olculemezse (None, None, None).

    NEDEN AYRI BIR EKSEN: disk %7 dolu olsa bile inode'lar tukenebilir ve
    yazma ENOSPC alir. Yalnizca bayta bakan bir guard bunu HIC gormez.

    Windows'ta `os.statvfs` yoktur; orada inode kavrami da yoktur (NTFS MFT
    dinamik buyur). Sessizce None doneriz — guard bayt eksenleriyle calisir.
    """
    statvfs = getattr(os, "statvfs", None)
    if statvfs is None:
        return None, None, None
    try:
        st = statvfs(path)
    except OSError as exc:
        logger.warning("disk_guard_statvfs_failed path=%s error=%s", path, exc)
        return None, None, None
    toplam = int(st.f_files)
    if toplam <= 0:
        return None, None, None
    kullanilan = toplam - int(st.f_ffree)
    return (kullanilan / toplam) * 100.0, kullanilan, toplam


def evaluate(path: str | None = None) -> DiskStatus | None:
    """Anlik disk durumu. Olculemezse None (guard sessizce devre disi)."""
    target = path or guarded_path()
    try:
        usage = shutil.disk_usage(target)
    except OSError as exc:
        logger.warning("disk_guard_stat_failed path=%s error=%s", target, exc)
        return None
    reserve = reserve_for(usage.total)
    inode_pct, inode_used, inode_total = inode_kullanimi(target)
    return DiskStatus(
        path=target,
        total_bytes=int(usage.total),
        free_bytes=int(usage.free),
        reserve_bytes=reserve,
        level=classify(
            int(usage.free),
            reserve,
            total_bytes=int(usage.total),
            inode_percent=inode_pct,
        ),
        inode_percent=inode_pct,
        inode_used=inode_used,
        inode_total=inode_total,
    )


# --------------------------------------------------------------- mudahale


def _temizle_ftp_bayat_gecici() -> list[str]:
    """FTP-T3: yarim/basarisiz transfer artiklari (`*.tmp`, `.tmp_*`).

    NE SILINIR: yalnizca gecici ad kalibina uyan VE bayatlama esigini gecmis
    dosyalar. Bir config yazimi saniyeler surer; saatler once birakilmis bir
    gecici dosya, yazan surecin oldugu ya da gucun kesildigi anlamina gelir.

    NE SILINMEZ: `<seri>_Configuration.csv`, `<seri>_DNP3_settings.bin`,
    `<seri>_Firmware.utf` — cihazin indirmeyi bekledigi AKTIF dosyalar
    (FTP-T0). Bunlar disk baskisi ne olursa olsun korunur; cihazin
    yapilandirmasini silmek sahayi kor birakir.

    Siniflandirma disi (FTP-T2) dosyalara da DOKUNULMAZ: urun karari henuz
    verilmedi, korlemesine silmek veri kaybi riskidir. Onlar yalnizca
    `storage_snapshot` icinde sayilir ve operatore gosterilir.
    """
    import time

    kok = os.getenv("FTP_ROOT", "/data/ftp")
    if not os.path.isdir(kok):
        return []

    esik = max(1, settings.disk_guard_ftp_temp_stale_hours) * 3600
    simdi = time.time()
    silinen = 0
    bayt = 0
    for kok_dizin, _dirs, dosyalar in os.walk(kok):
        for ad in dosyalar:
            if not (ad.endswith(".tmp") or ad.startswith(".tmp_")):
                continue
            yol = os.path.join(kok_dizin, ad)
            try:
                st = os.stat(yol)
                if simdi - st.st_mtime < esik:
                    continue
                os.unlink(yol)
            except OSError:
                continue
            silinen += 1
            bayt += st.st_size
    if not silinen:
        return []
    logger.info("disk_guard_ftp_temp_purged count=%d bytes=%d", silinen, bayt)
    return [f"ftp_bayat_gecici_silindi: {silinen} dosya / {bayt} bayt"]


def _temizle_harita_onbellegi() -> list[str]:
    """Harita karo onbellegi — internetten yeniden inebilir, veri kaybi YOK."""
    try:
        from app.services import map_tile_service

        before = map_tile_service.cache_size_bytes()
        if before <= 0:
            return []
        map_tile_service.clear_cache()
        return [f"harita_onbellegi_temizlendi: {before} bayt"]
    except Exception:  # noqa: BLE001
        logger.exception("disk_guard_map_cache_clear_failed")
        return []


def _relieve_aggressive() -> list[str]:
    """Retention'lari KISALTILMIS pencereyle hemen kostur.

    Normalde bu purge'ler kendi periyotlarini bekler (10 dk / 6 saat). Disk
    baskisi altinda beklemek anlamsiz; ayrica pencereyi gecici olarak
    kisaltiyoruz. Kalici ayar DEGISMEZ — bir sonraki normal tetik yine
    yapilandirilmis degerlerle kosar.
    """
    from app.services import telemetry_retention

    done: list[str] = []
    # `paced=False` — partiler arasi nefes KAPALI. Periyodik yolda o bekleme
    # dogru: temizlik, veri yolunun yanindan gecerken onu itmemeli. Burada
    # oncelik TERSINE doner; disk dolmak uzereyken alani yavas ama kibar
    # acmak, tam da onlemeye calistigimiz seyi (Postgres'in yazamaz hale
    # gelmesi) davet ederdi.
    worker = telemetry_retention.RetentionWorker(paced=False)

    # `processed_messages` BURADA YOK — bkz. modul basindaki gerekce.
    # Idempotency defteri disk baskisiyla kisaltilmaz; kendi periyodik
    # temizligi (2 saatlik pencere) degismeden isler.

    # Canli deger penceresi. Her sinyalin SON degeri yine korunur, yani
    # kisaltmak canli ekrani bosaltmaz.
    try:
        n = worker.purge_telemetry(retention_minutes=_AGGRESSIVE_TELEMETRY_MINUTES)
        done.append(f"telemetry<-{_AGGRESSIVE_TELEMETRY_MINUTES}dk: {n}")
    except Exception:  # noqa: BLE001
        logger.exception("disk_guard_telemetry_purge_failed")

    # Yayinlanmis outbox satirlari — zaten teslim edilmis kayitlar;
    # published=False'a ASLA dokunulmaz.
    #
    # DEAD-LETTER'A DA DOKUNULMAZ: `dead_letter_days` GECILMIYOR, yani o
    # satirlar kendi 14 gunluk penceresinde kalir. Onlar hata ayiklama
    # KANITIDIR ("SCADA'ya su olay neden gitmedi"); disk baskisi bir kaniti
    # yok etme gerekcesi olamaz (bkz. "NELERE ASLA DOKUNULMAZ").
    try:
        n = worker.purge_outbox_events(retention_minutes=_AGGRESSIVE_OUTBOX_MINUTES)
        done.append(f"outbox_published<-{_AGGRESSIVE_OUTBOX_MINUTES}dk: {n}")
    except Exception:  # noqa: BLE001
        logger.exception("disk_guard_outbox_purge_failed")

    return done


def _relieve_emergency() -> list[str]:
    """ACIL seviye — yalnizca FAZLA YEDEKLER.

    Yeniden uretilebilirler (bayat gecici dosyalar, harita onbellegi) artik
    KRITIK seviyede, yani buraya gelmeden ONCE temizleniyor; bkz. `tick`
    icindeki sira ve modul basindaki "TEMIZLIK SIRASI" notu.
    """
    done: list[str] = []

    # Fazla yedekler. En yeni BASARILI yedek her kosulda korunur —
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


#: Olay bastirma durumu: (son yazilan seviye, son yazma zamani).
#: Seviye DEGISTIGINDE olay her zaman yazilir; ayni seviyede kalindiginda
#: `disk_guard_event_cooldown_sec` beklenir. Bu olmadan 5 dakikalik tick
#: gunde 288 satir uretirdi ve gercek gecisler o yiginda kaybolurdu.
_son_olay: dict[str, object] = {"level": None, "at": 0.0}


def _olay_yazilmali(level: str, *, now: float | None = None) -> bool:
    import time

    an = time.monotonic() if now is None else now
    if _son_olay["level"] != level:
        return True
    gecen = an - float(_son_olay["at"] or 0.0)
    return gecen >= max(0, settings.disk_guard_event_cooldown_sec)


def _olay_isaretle(level: str, *, now: float | None = None) -> None:
    import time

    _son_olay["level"] = level
    _son_olay["at"] = time.monotonic() if now is None else now


def _record(status: DiskStatus) -> None:
    """Operator gorunurlugu — Olaylar ekraninda gorulsun.

    Denetim kaydina yazmak disk baskisi altinda ironik gorunebilir ama
    satir maliyeti ihmal edilebilir ve "disk neden bosaldi / neden doldu"
    sorusunun tek cevabi bu kayit olur.
    """
    if status.level == LEVEL_OK:
        # Normale donus de bir GECISTIR; bir sonraki bozulmanin olay
        # uretebilmesi icin durumu sifirla, ama OK icin olay yazma.
        _son_olay["level"] = LEVEL_OK
        return
    if not _olay_yazilmali(status.level):
        logger.debug("disk_guard_event_suppressed level=%s", status.level)
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
                    "used_percent": round(status.used_ratio * 100, 1),
                    "inode_percent": (
                        round(status.inode_percent, 1)
                        if status.inode_percent is not None
                        else None
                    ),
                    "actions": status.actions,
                },
            )
            db.commit()
            _olay_isaretle(status.level)
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

    # TEMIZLIK SIRASI — YENIDEN URETILEBILIR ONCE.
    #
    # DUZELTILEN TERS ONCELIK: onceki sirada KRITIK seviyede once veritabani
    # pencereleri (processed_messages / telemetry / outbox) kisaltiliyor,
    # harita karo onbellegi ise ancak ACIL seviyede temizleniyordu. Yani
    # sistem, internetten yeniden inebilen 70 MB'lik bir onbellegi TUTARKEN
    # dedup defterini budamaya basliyordu. Sira artik:
    #
    #   1. bayat gecici dosyalar (FTP-T3)   — tanim geregi cop
    #   2. harita karo onbellegi            — yeniden uretilebilir
    #   3. kisa omurlu DB pencereleri       — alt sinirlari KILITLI
    #   4. (ACIL) fazla yedekler            — en yenisi her kosulda kalir
    #
    # 3. adimin guvenligi bu modulde DEGIL, `telemetry_retention` icinde
    # zorlanir: dedup penceresi redelivery penceresinin altina inemez ve
    # `published=False` outbox satirlarina hicbir kosulda dokunulmaz.
    if status.level in (LEVEL_AGGRESSIVE, LEVEL_EMERGENCY):
        status.actions.extend(_temizle_ftp_bayat_gecici())
        status.actions.extend(_temizle_harita_onbellegi())
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


def tick_with_snapshot() -> DiskStatus | None:
    """Tick + bilesen anlik goruntusunun arka planda tazelenmesi.

    Anlik goruntu PAHALI (agac taramasi). Tazelemeyi buraya baglamak, API
    tarafinin her istekte disk taramasi yapmasini onler — Sistem Durumu
    sayfasi hazir veriyi okur.

    Tazeleme hatasi tick'i DUSURMEZ: gorunurluk kaybi, korumanin kendisini
    devre disi birakmayi haketmez.
    """
    status = tick()
    try:
        from app.services import storage_snapshot

        storage_snapshot.snapshot()
    except Exception:  # noqa: BLE001
        logger.exception("disk_guard_snapshot_refresh_failed")
    return status
