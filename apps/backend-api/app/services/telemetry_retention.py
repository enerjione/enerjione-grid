"""Retention worker — sinirsiz buyuyen tablolarin zaman/adet bazli temizligi.

Dort tablonun sorumlulugu burada:

  1. `telemetry`          — canli deger, kisa kayan pencere (default 30 dk).
                            Her (cihaz, sinyal) ikilisinin SON degeri MUAF;
                            sure ne olursa olsun canli ekran bosalmaz.
  2. `processed_messages` — idempotency defteri (default 2 saat).
  3. `system_events`      — denetim/olay kaydi (default 2 yil + FIFO tavani).
  4. `outbox_events`      — yayin kuyrugu. Yayinlanmislar 15 dk, DEAD-LETTER
                            satirlar 14 GUN. `published=False` + damgasiz
                            satira ASLA dokunulmaz (at-least-once).

NEDEN KRITIK
------------
600 cihaz olceginde telemetri ~26M satir/gun uretir; `processed_messages`
mesaj basina bir satir tuttugu icin AYNI hizda buyur. Retention olmazsa:
  * disk dolar ve once Postgres yazamaz hale gelir, telemetri akisi durur,
  * idempotency SELECT'i her batch'te devasa bir tabloya bakar ve yavaslar,
  * autovacuum geride kalir, tablo sisip index'ler bozulur.

`system_events` daha yavas buyur ama HIC temizlenmiyordu — sonsuza kadar
birikiyordu. Artik 2 yillik pencere + adet tavani var.

`outbox_events` temizligi ONCEDEN `outbox_flush_worker` icinde, YAYIN
THREAD'inin kendisinde kosuyordu ve TAVANI URETIMIN ALTINDAYDI:
OUTBOX_PURGE_BATCH(10.000) / OUTBOX_PURGE_INTERVAL_SEC(10sn) = 1.000 satir/sn,
uretim ise ~1.074 satir/sn (saha olcumu 2026-08-03). Silme uretime
yetismedigi icin tablo kararli duruma HIC gelmiyordu: 15 dakikalik pencere
ayarli oldugu halde en eski satir 36 dakikalikti ve tablo 1.7 GB'a cikmisti.
Buraya tasindi cunku (a) buradaki tur tavani uretimin ~15 kati kapasite
verir, (b) yayin thread'i artik yalnizca yayin yapar.

BATCH'LI SILME — NEDEN SART
---------------------------
Onceki surumde `processed_messages` LIMIT'siz tek DELETE ile temizleniyordu.
Saatlik tetikte ~1.1M satir TEK transaction'da siliniyordu; bu:
  * WAL'i tepe noktasina cikarir (checkpoint firtinasi),
  * tabloyu islem boyunca kilitler,
  * autovacuum'un araya girmesini engeller -> olu satirlar birikir (bloat).
Artik her purge LIMIT'li TURLAR halinde kosar ve HER TUR AYRI COMMIT eder.
Tur tavani (`retention_max_batches_per_run`) tek bir tetigin saatlerce
surmesini onler; artan satirlar bir sonraki tetikte silinir. Birikme olmaz
cunku tavan x batch, periyot boyunca uretilenden cok daha buyuktur.

KONFIGURASYON
-------------
Tum degerler `app.core.config.settings` uzerinden gelir (env ile override
edilebilir); `.env.example` ve docker-compose.yml ile senkronu
`tests/test_config_consistency.py` kilitler. Onceden bu ayarlar burada
dogrudan `os.getenv` ile okunuyordu ve hicbir ornek dosyada gorunmuyordu.
"""

from __future__ import annotations

import logging
import os
import threading
from datetime import datetime, timedelta, timezone

from sqlalchemy import and_, delete, select, text

from app.core.config import settings
from app.db.session import SessionLocal
from app.models.outbox_event import OutboxEvent
from app.models.processed_message import ProcessedMessage

#: NATS yeniden teslim penceresi: ack_wait(60sn) x nats_worker_max_deliver.
REDELIVERY_WINDOW_SEC = 60 * settings.nats_worker_max_deliver
#: Pencerenin kac kati emniyet payi birakilacagi. 3 kat: gecikmis bir
#: yeniden teslim turu bile dedup kaydini kacirmasin.
_REDELIVERY_SAFETY_FACTOR = 3.0
from app.models.system_event import SystemEvent
from app.models.telemetry import Telemetry

logger = logging.getLogger(__name__)

# Birimi GUN'den SAAT'e cevrilen eski ayar. Sahada set edilmis olma ihtimali
# dusuk (hicbir ornek dosyada yer almiyordu) ama sessizce yok saymak yerine
# operatoru uyariyoruz.
_LEGACY_ENV = "PROCESSED_MESSAGES_RETENTION_DAYS"

# Outbox purge `outbox_flush_worker`dan buraya tasinirken settings'e gecti.
# Bu iki degisken artik OKUNMUYOR; sahada .env'ine yazmis kurulum ayari
# degistirdigini sanmasin diye acikca uyariyoruz. (Ayni tuzaga
# PROCESSED_MESSAGES_RETENTION_DAYS ile bir kez dusuldu.)
_LEGACY_OUTBOX_ENV = {
    "OUTBOX_PURGE_BATCH": "RETENTION_DELETE_BATCH",
    "OUTBOX_PUBLISHED_RETENTION_HOURS": "OUTBOX_PUBLISHED_RETENTION_MINUTES",
}

# FIFO adet tavani YALNIZCA asagidaki gurultu satirlarini dusurebilir.
#
# NEDEN ALLOWLIST (denylist DEGIL): denetim kaydinda "en eskiyi sil" kurali
# tek basina TERS calisir. Bir olay firtinasi (ornegin bir entegrasyon
# dongude hata uretmesi) tabloyu sisirdiginde en eski satirlar genelde
# kurulum donemindeki `security` / `auth` / `license` kayitlaridir — yani
# denetim izinin EN DEGERLI kismi, gecici bir telemetri gurultusu ugruna
# silinirdi.
#
# Allowlist olmasi sayesinde ileride eklenecek YENI bir kategori otomatik
# KORUNUR (unutulup silinme tarafina dusmez). Korunan kayitlar yalnizca
# sure bazli pencere (system_events_retention_days) dolunca duser.
_FIFO_EXPENDABLE_CATEGORIES = ("telemetry", "outbound", "outbound_target")
_FIFO_EXPENDABLE_SEVERITIES = ("debug", "info")


def _warn_legacy_env() -> None:
    if os.getenv(_LEGACY_ENV):
        logger.warning(
            "%s artik KULLANILMIYOR (birim gun -> saat degisti). Yerine "
            "PROCESSED_MESSAGES_RETENTION_HOURS kullanin; su an gecerli deger "
            "%d saat.",
            _LEGACY_ENV,
            settings.processed_messages_retention_hours,
        )
    for eski, yeni in _LEGACY_OUTBOX_ENV.items():
        if os.getenv(eski, "").strip():
            logger.warning(
                "%s artik KULLANILMIYOR (outbox temizligi retention worker'a "
                "tasindi). Yerine %s kullanin.",
                eski,
                yeni,
            )


def _batch_delete(model, where, *, label: str) -> int:
    """`where` kosuluna uyan satirlari LIMIT'li turlarla siler.

    Her tur AYRI transaction: kisa islemler -> WAL tepe noktasi dusuk, lock
    suresi kisa, autovacuum arayi yakalar. Bir tur tam dolu donmediyse
    (removed < batch) silinecek satir kalmamistir, erken cikilir.

    `model` PK'si `id` olan bir ORM sinifi olmali (subquery LIMIT icin gerekir).
    """
    batch = settings.retention_delete_batch
    total = 0
    for _ in range(settings.retention_max_batches_per_run):
        db = SessionLocal()
        try:
            ids = (
                select(model.id)
                .where(where)
                .order_by(model.id.asc())
                .limit(batch)
            )
            result = db.execute(delete(model).where(model.id.in_(ids)))
            db.commit()
            removed = int(getattr(result, "rowcount", 0) or 0)
        except Exception:  # noqa: BLE001
            db.rollback()
            raise
        finally:
            db.close()
        total += removed
        if removed < batch:
            break
    else:
        # Tur tavanina dayandik: bu tetikte hepsini bitiremedik. Normal degil,
        # operator gorsun (retention periyodu cok uzun ya da yuk beklenenden
        # yuksek olabilir).
        logger.warning(
            "retention_batch_cap_reached label=%s removed=%d cap=%d "
            "(kalan satirlar sonraki tetikte silinecek)",
            label,
            total,
            settings.retention_max_batches_per_run,
        )
    return total


class RetentionWorker:
    """Arka plan thread'i; her tablo icin ayri periyotla purge tetikler."""

    def __init__(self) -> None:
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop.clear()
        _warn_legacy_env()
        self._thread = threading.Thread(
            target=self._run, name="retention-worker", daemon=True
        )
        self._thread.start()
        logger.info(
            "retention_started telemetry_keep_min=%d/%ds "
            "processed_messages_keep_h=%d/%ds "
            "system_events_keep_d=%d/%ds max_rows=%d "
            "outbox_keep_min=%d/%ds outbox_dead_letter_keep_d=%d batch=%d",
            settings.telemetry_retention_minutes,
            settings.telemetry_retention_interval_sec,
            settings.processed_messages_retention_hours,
            settings.processed_messages_interval_sec,
            settings.system_events_retention_days,
            settings.system_events_interval_sec,
            settings.system_events_max_rows,
            settings.outbox_published_retention_minutes,
            settings.outbox_purge_interval_sec,
            settings.outbox_dead_letter_retain_days,
            settings.retention_delete_batch,
        )

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=5)
            self._thread = None

    def _jobs(self) -> list[list]:
        """(etiket, periyot_getter, fonksiyon, son_calisma_zamani).

        Ayri metot: kayitli olmayan bir purge SESSIZCE hic kosmaz ve bu ancak
        disk dolunca fark edilir. Test bu listeyi dogrudan gorebilsin.
        """
        return [
            ["telemetry", lambda: settings.telemetry_retention_interval_sec, self.purge_telemetry, 0.0],
            ["processed_messages", lambda: settings.processed_messages_interval_sec, self.purge_processed_messages, 0.0],
            ["system_events", lambda: settings.system_events_interval_sec, self._purge_system_events, 0.0],
            ["outbox_events", lambda: settings.outbox_purge_interval_sec, self.purge_outbox_events, 0.0],
            # Disk guard: yukaridaki TTL'lerin hepsi dogru calissa BILE diskin
            # dolmadigini gercek olcumle dogrular; dolmaya yaklasilirsa
            # yeniden uretilebilir veriden baslayarak alan acar.
            ["disk_guard", lambda: settings.disk_guard_interval_sec, self._disk_guard_tick, 0.0],
        ]

    def _run(self) -> None:
        jobs = self._jobs()
        first_iteration = True
        while not self._stop.is_set():
            now_mono = _monotonic()
            for job in jobs:
                label, interval_fn, fn, last_run = job[0], job[1], job[2], job[3]
                if not (first_iteration or now_mono - last_run >= interval_fn()):
                    continue
                try:
                    fn()
                except Exception:  # noqa: BLE001
                    # Bir tablonun purge'u patlarsa digerleri calismaya devam
                    # etmeli; retention topluca durursa disk dolar.
                    logger.exception("retention_purge_failed label=%s", label)
                job[3] = now_mono

            first_iteration = False
            # En kisa periyodun yarisi kadar uyu (en az 30sn) — tetik anlarini
            # kacirmadan bosuna donmeyi de onler.
            sleep_s = min(interval_fn() for _l, interval_fn, _f, _t in jobs)
            self._stop.wait(max(30, sleep_s // 2))

    # ------------------------------------------------------------------ jobs

    def _disk_guard_tick(self) -> None:
        """Disk guard turu.

        Lazy import: disk_guard bu modulu (kisaltilmis pencereyle purge
        cagirmak icin) import ediyor; ust duzeyde import etsek dongu olurdu.
        """
        from app.services import disk_guard

        disk_guard.tick()

    def purge_telemetry(self, *, retention_minutes: int | None = None) -> int:
        """Eski telemetri satirlarini siler — AMA her (device_id, signal_key)
        icin EN SON satiri her zaman korur.

        `retention_minutes` verilirse yapilandirilmis degerin YERINE o pencere
        kullanilir. Disk guard baski altinda pencereyi gecici olarak kisaltmak
        icin bunu kullanir; kalici ayar DEGISMEZ.

        Neden koruma: bir sinyal 30 dakikadir degismiyorsa son satiri da
        silinirse canli degerler tablosunda o hucre BOSALIR. Kullanici
        "deger kayboluyor" diye bildirmisti.

        PERFORMANS — filtre pencere fonksiyonunun ICINDE olmali:
          Ic sorgu WHERE'siz olsaydi ROW_NUMBER() TUM tabloyu tarayip
          siralar ve `idx_telemetry_source_timestamp` KULLANILAMAZDI.
          Filtreyi ice indirince pencere yalnizca ESKI satirlar uzerinde
          calisir ve index devreye girer.

        KABUL EDILEN FARK: aktif bir seride "cutoff oncesi son deger" 1 satir
        olarak kalir (pencere yalnizca eski satirlari gordugu icin onlarin en
        yenisini rn=1 sayar). Cihaz+sinyal basina sabit 1 satir — buyumez.

        BATCH: `LIMIT :batch` ic sorguda. Worker bir sure durmussa (ya da
        yuk beklenenden yuksekse) tek DELETE 26M satira dayanabilir; turlara
        boluyoruz.
        """
        minutes = (
            retention_minutes
            if retention_minutes is not None
            else settings.telemetry_retention_minutes
        )
        cutoff = datetime.now(timezone.utc) - timedelta(minutes=minutes)
        batch = settings.retention_delete_batch
        total = 0
        for _ in range(settings.retention_max_batches_per_run):
            db = SessionLocal()
            try:
                result = db.execute(
                    text(
                        """
                        DELETE FROM telemetry t
                        USING (
                            SELECT id
                            FROM (
                                SELECT id,
                                       ROW_NUMBER() OVER (
                                           PARTITION BY device_id, signal_key
                                           ORDER BY id DESC
                                       ) AS rn
                                FROM telemetry
                                WHERE source_timestamp < :cutoff
                            ) sub
                            WHERE rn > 1
                            LIMIT :batch
                        ) old
                        WHERE t.id = old.id
                        """
                    ),
                    {"cutoff": cutoff, "batch": batch},
                )
                db.commit()
                removed = int(getattr(result, "rowcount", 0) or 0)
            except Exception:  # noqa: BLE001
                db.rollback()
                raise
            finally:
                db.close()
            total += removed
            if removed < batch:
                break
        if total:
            logger.info(
                "telemetry_retention_purged removed=%d cutoff=%s "
                "(her sinyalin son satiri korundu)",
                total,
                cutoff.isoformat(),
            )
        return total

    def purge_processed_messages(self, *, retention_hours: int | None = None) -> int:
        """Eski idempotency kayitlarini siler.

        `retention_hours` verilirse yapilandirilmis degerin YERINE o pencere
        kullanilir (disk guard baski altinda kisaltir; kalici ayar degismez).

        Gercek redelivery penceresi ack_wait(60s) x max_deliver(10) = 10 dakika
        (bkz. telemetry_consumer.py consumer_cfg). Default 24 saat bunun 144
        kati; ustelik `telemetry_history` dogal anahtarinda ON CONFLICT DO
        NOTHING ikinci bir dedup katmani var. Yani bu tabloyu kisa tutmak
        at-least-once garantisini ZAYIFLATMAZ.
        """
        hours = (
            retention_hours
            if retention_hours is not None
            else settings.processed_messages_retention_hours
        )
        # ALT SINIR — ayar degil, GUVENLIK KILIDI.
        #
        # Gercek yeniden teslim penceresi ack_wait(60sn) x max_deliver =
        # 10 dakika. Bunun ALTINA inilirse dedup kaydi, mesaj HALA yeniden
        # teslim edilebilirken silinir ve DUPLICATE telemetri yazilir.
        # Env'e 0 yazan bir operator (ya da "diski bosaltayim" diye agresif
        # ayar yapan disk_guard) sessizce veri butunlugunu bozardi.
        saniye = max(
            float(REDELIVERY_WINDOW_SEC) * _REDELIVERY_SAFETY_FACTOR,
            float(hours) * 3600.0,
        )
        cutoff = datetime.now(timezone.utc) - timedelta(seconds=saniye)
        removed = _batch_delete(
            ProcessedMessage,
            ProcessedMessage.processed_at < cutoff,
            label="processed_messages",
        )
        if removed:
            logger.info(
                "processed_messages_retention_purged removed=%d cutoff=%s",
                removed,
                cutoff.isoformat(),
            )
        return removed

    def purge_outbox_events(
        self,
        *,
        retention_minutes: int | None = None,
        dead_letter_days: int | None = None,
    ) -> int:
        """Outbox temizligi: YAYINLANMISLAR kisa, DEAD-LETTER uzun pencerede.

        NELERE DOKUNULMAZ — `published=False` VE `dead_letter_at IS NULL`
        satirlar. Bunlar hala teslim edilmeyi bekleyen olaylardir; silinmeleri
        KALICI VERI KAYBI olur (at-least-once garantisi buna dayaniyor).

        IKI AYRI PENCERE, BILINCLI
        --------------------------
        Yayinlanmis satir yalnizca tekrar-yayin korumasidir; 15 dakika sonra
        degeri kalmaz. Dead-letter satir ise HATA AYIKLAMA KANITIDIR —
        "SCADA'ya su olay neden gitmedi" sorusunun tek cevabi `last_error`
        sutunudur. Ayni kefeye konsalardi kanit, operator sorunu fark etmeden
        15 dakika icinde silinirdi.

        `retention_minutes` verilirse yapilandirilmis pencerenin YERINE o
        kullanilir (disk guard baski altinda kisaltir; kalici ayar degismez).
        Dead-letter penceresini disk guard KISALTMAZ.

        KAPASITE: `_batch_delete` tur basina retention_delete_batch x
        retention_max_batches_per_run = 1.000.000 satir siler. 60sn periyotla
        ~16.600 satir/sn tavan; olculen ~1.074 satir/sn uretime karsi ~15 kat
        pay. Eski tavan 1.000 satir/sn idi, yani uretimin ALTINDA.
        """
        minutes = (
            retention_minutes
            if retention_minutes is not None
            else settings.outbox_published_retention_minutes
        )
        # ALT SINIR — ayar degil, GUVENLIK KILIDI. Satir durdugu surece
        # `dedup_key` UNIQUE + ON CONFLICT DO NOTHING ayni message_id'nin
        # tekrar kuyruga girmesini engelliyor. Yeniden teslim penceresinin
        # (ack_wait x max_deliver) altina inilirse bu koruma, mesaj HALA
        # yeniden teslim edilebilirken kalkar.
        saniye = max(float(REDELIVERY_WINDOW_SEC), float(minutes) * 60.0)
        cutoff = datetime.now(timezone.utc) - timedelta(seconds=saniye)
        removed = _batch_delete(
            OutboxEvent,
            and_(
                OutboxEvent.published.is_(True),
                OutboxEvent.published_at.is_not(None),
                OutboxEvent.published_at < cutoff,
            ),
            label="outbox_published",
        )

        gun = (
            dead_letter_days
            if dead_letter_days is not None
            else settings.outbox_dead_letter_retain_days
        )
        dl_cutoff = datetime.now(timezone.utc) - timedelta(days=gun)
        dl_removed = _batch_delete(
            OutboxEvent,
            and_(
                OutboxEvent.dead_letter_at.is_not(None),
                OutboxEvent.dead_letter_at < dl_cutoff,
            ),
            label="outbox_dead_letter",
        )

        if removed or dl_removed:
            logger.info(
                "outbox_retention_purged published=%d cutoff=%s "
                "dead_letter=%d dl_cutoff=%s",
                removed,
                cutoff.isoformat(),
                dl_removed,
                dl_cutoff.isoformat(),
            )
        return removed + dl_removed

    def _purge_system_events(self) -> int:
        """Denetim/olay kaydi temizligi: once SURE, sonra ADET tavani (FIFO).

        Sure bazli pencere normal calisma sartlarinda tek basina yeterlidir.
        Adet tavani, beklenmedik bir olay firtinasinda (ornegin bir entegrasyon
        dongude hata uretirse) tablonun 2 yil dolmadan sismesini onleyen
        emniyet subabidir — en ESKI kayitlar dusulur, en yeniler kalir.
        """
        removed = 0

        # 1) Sure bazli pencere.
        cutoff = datetime.now(timezone.utc) - timedelta(
            days=settings.system_events_retention_days
        )
        removed += _batch_delete(
            SystemEvent,
            SystemEvent.created_at < cutoff,
            label="system_events_age",
        )

        # 2) FIFO tavani. En yeni N. satirin id'sini bulup altindakileri sil —
        # AMA yalnizca dusurulebilir gurultu satirlarini (bkz.
        # _FIFO_EXPENDABLE_*). OFFSET, PK index'i uzerinde yurur; tablo
        # taranmaz.
        max_rows = settings.system_events_max_rows
        if max_rows > 0:
            db = SessionLocal()
            try:
                threshold_id = db.scalar(
                    select(SystemEvent.id)
                    .order_by(SystemEvent.id.desc())
                    .offset(max_rows)
                    .limit(1)
                )
            finally:
                db.close()
            if threshold_id is not None:
                over = _batch_delete(
                    SystemEvent,
                    and_(
                        SystemEvent.id <= threshold_id,
                        SystemEvent.category.in_(_FIFO_EXPENDABLE_CATEGORIES),
                        SystemEvent.severity.in_(_FIFO_EXPENDABLE_SEVERITIES),
                    ),
                    label="system_events_fifo",
                )
                if over:
                    logger.warning(
                        "system_events_fifo_cap_applied removed=%d max_rows=%d "
                        "(olay uretimi beklenenden yuksek — kaynagi inceleyin)",
                        over,
                        max_rows,
                    )
                else:
                    # Tavan asilmis ama dusurulebilir satir YOK: fazlalik
                    # korunan denetim kayitlarindan geliyor. Bunlari FIFO ile
                    # SILMIYORUZ (bilincli); operator gorsun diye uyariyoruz.
                    # Disk baskisi varsa disk guard katmani devreye girer.
                    logger.warning(
                        "system_events_fifo_cap_exceeded_but_protected max_rows=%d "
                        "(fazlalik korunan denetim kategorilerinde — silinmedi)",
                        max_rows,
                    )
                removed += over

        if removed:
            logger.info(
                "system_events_retention_purged removed=%d cutoff=%s",
                removed,
                cutoff.isoformat(),
            )
        return removed


def _monotonic() -> float:
    """time.monotonic() wrapper; test edilebilirlik icin."""
    import time as _time

    return _time.monotonic()


# Geriye uyumluluk: modul adi ve disari acilan API degismedi (main.py
# `telemetry_retention.start()` cagiriyor). Sinif adi TelemetryRetentionWorker
# -> RetentionWorker oldu cunku artik yalnizca telemetri temizlemiyor.
TelemetryRetentionWorker = RetentionWorker

_worker = RetentionWorker()


def start() -> None:
    _worker.start()


def stop() -> None:
    _worker.stop()
