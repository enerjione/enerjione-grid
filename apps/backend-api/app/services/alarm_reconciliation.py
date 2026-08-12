"""Alarm reconciliation worker.

Periyodik olarak (default 30sn) DB'deki acik alarmlari (reset=False) gozden
gecirir; her alarmin tetigi olan kural icin cihazin son telemetri degerine
bakar ve kural kosulu artik karsilanmiyorsa alarmi cozer:

  * acknowledged=True ise: alarmi DB'den SILER (kullanici onaylamis, normale
    dondu, gereksiz kayit).
  * acknowledged=False ise: alarmi reset=True yapar (alt panelde "Normale
    Donen — Onay Bekliyor" listesine duser).

Bu worker neden gerekli?
  alarm-service in-memory state'e bagli olarak transition (active->inactive)
  durumunda backend'e clear cagrisi atiyor; ama:
    - alarm-service restart'inda state kaybolur,
    - cihaz alarm sinyalini artik HIC gondermiyorsa (eski alarm icin yeni
      telemetri akmiyor), transition hic tetiklenmez,
    - clear cagrisi gelmedigi icin DB'de acik alarm asili kalir.

Reconciliation worker bu drift'i temizler — alarm-service tamamen down olsa
bile DB self-healing olur.

Konfigurasyon (env):
  ALARM_RECONCILE_INTERVAL_SEC  default 30
  ALARM_RECONCILE_LOOKBACK_MIN  default 30   (telemetry retention ile uyumlu)
"""

from __future__ import annotations

import logging
import os
import threading
import time as _time
from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select, tuple_
from sqlalchemy.dialects.postgresql import insert as _pg_insert

from app.db.session import SessionLocal
from app.models.alarm import AlarmEvent
from app.models.alarm_rule import AlarmRule
from app.models.device import Device
from app.models.telemetry import Telemetry
from app.models.telemetry_history import TelemetryHistory
from app.services.event_service import record_event
from app.services.tag_engine_service import quality_blocks_alarm

logger = logging.getLogger(__name__)

# Alarm sikligi (rate) icin historian sinyal key'i. Cihaz bazli — her tick
# "son ALARM_RATE_WINDOW_MIN dakikada olusan alarm sayisi" TelemetryHistory'ye
# yazilir. Katalog/aggregate otomatik kapsar (device_id+signal_key sorgusu).
# Ileride hat analizi: LineSegment.device_id uzerinden hat toplami cekilir.
ALARM_RATE_SIGNAL_KEY = "master.alarm_rate"


def _alarm_rate_window_min() -> int:
    raw = os.getenv("ALARM_RATE_WINDOW_MIN", "1")
    try:
        return max(1, int(raw))
    except ValueError:
        return 1


def _record_alarm_rate(db) -> None:
    """Her tick: cihaz bazli son-pencere alarm sayisini historian'a yaz.

    Alarm gelmeyen cihaza da 0 yazilir (surekli zaman serisi -> heatmap/trend
    icin bosluksuz). Bucket zamani tick anina hizalanir (pencere sonu).
    """
    now = datetime.now(timezone.utc)
    window = timedelta(minutes=_alarm_rate_window_min())
    since = now - window

    # Son penceredeki alarm sayisi (created_at) cihaz bazli — tum yeni alarmlar.
    counts = dict(
        db.execute(
            select(AlarmEvent.device_id, func.count(AlarmEvent.id))
            .where(AlarmEvent.created_at >= since)
            .group_by(AlarmEvent.device_id)
        ).all()
    )
    # Tum cihazlar (alarm gelmese de 0 yaz — surekli seri).
    device_ids = [row[0] for row in db.execute(select(Device.id)).all()]
    if not device_ids:
        return

    # Bucket zamanini pencereye hizala (dakikanin basi) — idempotent, ayni
    # dakikada ikinci tick ustune yazmaz (on_conflict_do_nothing PK).
    bucket_ts = now.replace(second=0, microsecond=0)
    rows = [
        {
            "device_id": did,
            "signal_key": ALARM_RATE_SIGNAL_KEY,
            "value": float(counts.get(did, 0)),
            "value_string": None,
            "quality": "good",
            "source_timestamp": bucket_ts,
        }
        for did in device_ids
    ]
    try:
        db.execute(
            _pg_insert(TelemetryHistory)
            .values(rows)
            .on_conflict_do_update(
                index_elements=["device_id", "signal_key", "source_timestamp"],
                set_={"value": _pg_insert(TelemetryHistory).excluded.value},
            )
        )
        db.commit()
    except Exception:  # noqa: BLE001
        logger.exception("alarm_rate_historian_write_failed")
        db.rollback()


def _interval_sec() -> int:
    raw = os.getenv("ALARM_RECONCILE_INTERVAL_SEC", "30")
    try:
        n = int(raw)
        return max(10, n)
    except ValueError:
        return 30


def _lookback_minutes() -> int:
    raw = os.getenv("ALARM_RECONCILE_LOOKBACK_MIN", "30")
    try:
        n = int(raw)
        return max(5, n)
    except ValueError:
        return 30


#: Haberlesme alarminin basligi — alarm-service `_build_quality_alarm`
#: icinde SABIT olarak bu metinle uretiliyor. `kind` alani eklenmeden once
#: acilmis kayitlarda tur bilgisi yok; yedek olarak baslik eslesmesi kalir.
COMM_ALARM_TITLE = "Haberleşme arızası"

#: Alarm degerlendirmesini engelleyen kaliteler — `quality_blocks_alarm` ile
#: AYNI kume, ama burada SQL `IN` icin gerekiyor (satir satir Python'a cekip
#: filtrelemek yerine veritabaninda eleyelim).
_BLOCKING_QUALITIES = ("bad", "offline", "invalid", "comm_lost", "restart", "forced")

#: Haberlesme alarmi icin "toparlandi" penceresi (saniye).
#:
#: Kural alarmlarinin 30 dakikalik `lookback`i burada KULLANILMAZ: toparlanan
#: bir cihazin yarim saat daha alarmda gorunmesi operatore yalan soylemek
#: olurdu. Iki dakika, varsayilan 2 sn'lik tarama periyodunda ~60 okuma —
#: gecici bir titremenin alarmi erken kapatmasina yetmeyecek kadar uzun.
#:
#: ENV ILE AYARLANABILIR DEGIL (bilincli): `ALARM_RECONCILE_*` kardeslerinin
#: env okumasi compose'dan GECMIYOR ve test bunu bir "borc kaydi" olarak
#: tutuyor (tests/test_env_compose_ulasilabilirlik.py). Ulasilamayan ucuncu
#: bir dugme eklemektense sabit ve belgeli bir deger daha durust.
_COMM_RECOVERY_WINDOW_SEC = 120


def _is_comm_alarm(alarm: AlarmEvent) -> bool:
    """Cihaz seviyesi haberlesme alarmi mi? (kurali ve sinyali yoktur)"""
    return (alarm.kind or "") == "comm_loss" or alarm.title == COMM_ALARM_TITLE


def _evaluate_rule(rule: AlarmRule, value: float) -> bool:
    """Kuralin verilen deger icin AKTIF olup olmadigini doner.

    NOT: Burada hysteresis uygulanmaz — reconciliation amaci "kosul artik
    yok mu" tespit etmek; histerezis aktivasyon yonu icindir. Reconciliation
    konservatif olmali: sadece esik kesin asilmiyorsa alarmi cozer.
    """
    t = rule.threshold
    hi = rule.threshold_high
    cmp = rule.comparator
    if cmp == "gt":
        return value > t
    if cmp == "gte":
        return value >= t
    if cmp == "lt":
        return value < t
    if cmp == "lte":
        return value <= t
    if cmp == "eq":
        return value == t
    if cmp == "ne":
        return value != t
    if cmp == "between":
        if hi is None:
            return False
        return t <= value <= hi
    if cmp == "outside":
        if hi is None:
            return False
        return value < t or value > hi
    if cmp == "boolean_true":
        return value >= 0.5
    if cmp == "boolean_false":
        return value < 0.5
    return False


def _resolve_alarm(db, alarm: AlarmEvent, reason: str) -> str:
    """Alarmi onay durumuna gore ARSIVE al ya da reset et. Action doner.

    ONAYLANMIS alarm eskiden SILINIYORDU (kullanici gormustu, listede yer
    kaplamasin). Bir gorunum karariydi ama tarihceyi goturuyordu: alarm
    takvimi ve cihaz x zaman matrisi gecmis gunler icin bos kaliyordu.
    Artik satir duruyor, `superseded_at` ile canli listeden dusuyor.
    """
    simdi = datetime.now(timezone.utc)
    if alarm.acknowledged:
        alarm.reset = True
        alarm.reset_at = simdi
        alarm.superseded_at = simdi
        record_event(
            db,
            category="alarm",
            event_type="alarm_auto_cleared",
            severity="info",
            message=f"Acknowledged alarm cleared and archived (reconcile): {alarm.title}",
            metadata={
                "alarm_id": alarm.id,
                "device_id": alarm.device_id,
                "signal_key": alarm.signal_key,
                "reason": reason,
                "archived": True,
            },
            i18n_key="alarm_auto_cleared_acked",
            i18n_params={"title": alarm.title},
        )
        return "archived"
    alarm.reset = True
    alarm.reset_at = simdi
    record_event(
        db,
        category="alarm",
        event_type="alarm_auto_cleared",
        severity="info",
        message=f"Alarm cleared on site (reconcile): {alarm.title}",
        metadata={
            "alarm_id": alarm.id,
            "device_id": alarm.device_id,
            "signal_key": alarm.signal_key,
            "reason": reason,
        },
        i18n_key="alarm_auto_cleared",
        i18n_params={"title": alarm.title},
    )
    return "reset"


class AlarmReconciliationWorker:
    """Arka plan thread'i; periyodik olarak acik alarmlari kontrol eder."""

    def __init__(self) -> None:
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._run, name="alarm-reconciliation", daemon=True
        )
        self._thread.start()
        logger.info(
            "alarm_reconciliation_started interval_sec=%d lookback_min=%d",
            _interval_sec(),
            _lookback_minutes(),
        )

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=5)
            self._thread = None

    def _run(self) -> None:
        # Acilis sonrasi ilk taramayi 5sn beklet (DB'nin migrate olmasi icin)
        self._stop.wait(5)
        while not self._stop.is_set():
            try:
                self._reconcile_once()
            except Exception:  # noqa: BLE001
                logger.exception("alarm_reconciliation_failed")
            self._stop.wait(_interval_sec())

    def _reconcile_once(self) -> None:
        db = SessionLocal()
        try:
            # Alarm sikligi (rate) historian yazimi — reconcile'dan bagimsiz,
            # her tick (0 alarm da yazilir, surekli seri). Kendi commit'i var.
            try:
                _record_alarm_rate(db)
            except Exception:  # noqa: BLE001
                logger.exception("alarm_rate_record_failed")
                db.rollback()

            open_alarms = list(
                db.scalars(
                    select(AlarmEvent).where(AlarmEvent.reset.is_(False))
                ).all()
            )
            # Her tick'te fault_events tablosunu aktif alarmlardan yeniden
            # hesapla. Bu, internal endpoint'leri uzerinden geçmeyen
            # (manuel olusturulmus eski) alarmlarin da fault listesine
            # yansimasini saglar; ayrica fault_events drift'lerini onler.
            try:
                # Periyodik tick — debounced (alarm flow ile coalescing eder).
                from app.services.fault_recompute_service import recompute_faults_debounced
                recompute_faults_debounced(db)
                db.commit()
            except Exception:  # noqa: BLE001
                logger.exception("fault_recompute_failed_in_periodic_tick")
                db.rollback()
            if not open_alarms:
                return
            # Kural cache'i: title -> rule (ilk eslesme)
            # Bir alarm bir kurala "title" uzerinden bagli; rule_id alanini
            # tutmadigimiz icin title eslesmesi yapariz (alarm-service de
            # boyle uretiyor).
            rules = list(db.scalars(select(AlarmRule).where(AlarmRule.is_active.is_(True))).all())
            rules_by_name: dict[str, AlarmRule] = {}
            for r in rules:
                rules_by_name.setdefault(r.name, r)

            cutoff = datetime.now(timezone.utc) - timedelta(minutes=_lookback_minutes())

            # --- Son telemetri degerlerini TEK SORGUDA cek ------------------
            # Onceden bu dongu her acik alarm icin AYRI bir sorgu atiyordu
            # (N+1). Bir hat arizasinda 100+ alarm acik olabiliyor ve bu is
            # 30 saniyede bir kosuyor — 100+ gidis-donus. Sorgular indeksli
            # oldugu icin her biri hizli, ama tur sayisi kendi basina yuk.
            #
            # DISTINCT ON (PostgreSQL) her (device_id, signal_key) icin
            # yalnizca en yeni satiri dondurur; idx_telemetry_device_signal_ts
            # ile dogrudan index taramasi olur.
            ilgili = {
                (a.device_id, a.signal_key)
                for a in open_alarms
                if a.device_id is not None and a.signal_key
            }
            son_degerler: dict[tuple[int, str], Telemetry] = {}
            if ilgili:
                satirlar = db.scalars(
                    select(Telemetry)
                    .where(Telemetry.source_timestamp >= cutoff)
                    .where(
                        tuple_(Telemetry.device_id, Telemetry.signal_key).in_(
                            list(ilgili)
                        )
                    )
                    .distinct(Telemetry.device_id, Telemetry.signal_key)
                    .order_by(
                        Telemetry.device_id,
                        Telemetry.signal_key,
                        Telemetry.source_timestamp.desc(),
                    )
                ).all()
                son_degerler = {(r.device_id, r.signal_key): r for r in satirlar}

            # --- HABERLESME ALARMLARI (kuralsiz, sinyalsiz) -----------------
            #
            # 2026-08-12 OLAYI: yedi cihazin "Haberleşme arızası" alarmi
            # 08:23'te acildi ve cihazlar saatlerce sorunsuz haberlesmesine
            # ragmen HIC kapanmadi. Sebep uc kirigin ust uste gelmesi:
            #
            #   1. alarm-service kapanisi YALNIZCA surec ici bir sozlukten
            #      (`_QUALITY_STATE`) karar veriyor. Servis yeniden basladiginda
            #      sozluk bosaliyor; iyi veri geldiginde "bu cihaz zaten bozuk
            #      degildi" deyip CLEAR gondermiyor.
            #   2. Bu worker — tam olarak o drift'i temizlemek icin yazilmis
            #      olan yer — alarmi KURALINA gore cozuyor. Haberlesme
            #      alarminin kurali yok (`rule_id=None`), asagidaki
            #      `rule is None -> continue` filtresinden dusuyor.
            #   3. Ayni dongudeki `if not alarm.signal_key` filtresi de
            #      eliyor: haberlesme alarmi cihaz seviyesidir, sinyali yok.
            #
            # Sonuc: sistemin kendi kendini iyilestiren yolu bu alarm turunu
            # HIC gormuyordu. Asagidaki blok o deligi kapatir.
            #
            # KARAR OLCUSU (kurala degil VERIYE bakar): son
            # `_COMM_RECOVERY_WINDOW_SEC` icinde cihazdan telemetri geldi mi ve
            # o pencerede BOZUK KALITELI hic okuma var mi?
            #   * taze veri YOK          -> cihaz gercekten sessiz, alarm HAKLI
            #   * pencerede bozuk okuma  -> sorun suruyor, dokunma
            #   * taze + hepsi iyi       -> haberlesme geri geldi, alarmi coz
            # Pencere kisa (2 dk) cunku 30 dakikalik `lookback` kullanilsaydi
            # toparlanan bir cihaz yarim saat daha alarmda kalirdi.
            comm_alarms = [a for a in open_alarms if _is_comm_alarm(a)]
            comm_ids = {a.device_id for a in comm_alarms if a.device_id is not None}
            taze_cihazlar: set[int] = set()
            bozuk_cihazlar: set[int] = set()
            if comm_ids:
                pencere = datetime.now(timezone.utc) - timedelta(
                    seconds=_COMM_RECOVERY_WINDOW_SEC
                )
                taze_cihazlar = set(
                    db.scalars(
                        select(Telemetry.device_id)
                        .where(Telemetry.source_timestamp >= pencere)
                        .where(Telemetry.device_id.in_(comm_ids))
                        .distinct()
                    ).all()
                )
                bozuk_cihazlar = set(
                    db.scalars(
                        select(Telemetry.device_id)
                        .where(Telemetry.source_timestamp >= pencere)
                        .where(Telemetry.device_id.in_(comm_ids))
                        .where(Telemetry.quality.in_(_BLOCKING_QUALITIES))
                        .distinct()
                    ).all()
                )

            cleared_count = 0
            for alarm in open_alarms:
                if _is_comm_alarm(alarm):
                    if alarm.device_id is None:
                        continue
                    if alarm.device_id not in taze_cihazlar:
                        continue  # Cihaz sessiz — alarm dogru, ACIK KALIR.
                    if alarm.device_id in bozuk_cihazlar:
                        continue  # Pencerede hala bozuk okuma var.
                    action = _resolve_alarm(db, alarm, reason="comm_restored")
                    cleared_count += 1
                    logger.info(
                        "comm_alarm_reconciled action=%s alarm_id=%d device_id=%d",
                        action, alarm.id, alarm.device_id,
                    )
                    continue

                rule = rules_by_name.get(alarm.title)
                if rule is None:
                    continue  # Kural silinmis veya pasif — drift; yine de
                    # otomatik silmek riskli (kullanici bilerek kurali silmis
                    # olabilir, alarm tarihce icin durabilir). Manuel silinsin.
                if not alarm.signal_key:
                    continue  # Eski kayit — sinyal bilinmiyor; reconcile edilemez.
                # Bu cihaz icin bu sinyalin son telemetri degeri —
                # yukarida tek sorguda toplandi, burada sadece okunuyor.
                last = son_degerler.get((alarm.device_id, alarm.signal_key))
                if last is None or last.value is None:
                    continue  # Yeterli yeni veri yok — guvenli karar veremeyiz.
                # KALITE KAPISI — bu satir olmadan sistem, cihazla baglanti
                # koptugu anda "ariza gecti" diyordu.
                #
                # Haberlesmesi kopan cihaz icin gateway `comm_lost` kalitesiyle
                # 0.0 basiyor. Asagidaki `_evaluate_rule(rule, 0.0)` cagrisi
                # "esik artik saglanmiyor" sonucunu uretiyor ve `_resolve_alarm`
                # ACIK ARIZA ALARMINI KAPATIYORDU; ardindan fault_recompute
                # tetiklenip harita YESILE donuyordu. Onaylanmis alarmlar ise
                # kalici siliniyordu (bkz. internal.py clear akisi).
                #
                # SCADA doktrini: veri kaybinda SON BILINEN DURUM korunur.
                # `Telemetry.quality` zaten elde (telemetry_consumer yaziyor),
                # ek sorgu maliyeti YOK.
                if quality_blocks_alarm(last.quality):
                    continue
                # Kural kosulu artik karsilanmiyorsa cozeriz.
                still_active = _evaluate_rule(rule, float(last.value))
                if still_active:
                    continue
                action = _resolve_alarm(db, alarm, reason="threshold_no_longer_met")
                cleared_count += 1
                logger.info(
                    "alarm_reconciled action=%s alarm_id=%d device_id=%d signal=%s value=%s",
                    action, alarm.id, alarm.device_id, alarm.signal_key, last.value,
                )
            if cleared_count > 0:
                # Reconcile sonrasi fault listesini de yenile
                try:
                    from app.services.fault_recompute_service import recompute_faults_debounced
                    recompute_faults_debounced(db)
                except Exception:  # noqa: BLE001
                    logger.exception("fault_recompute_failed_after_reconcile")
                db.commit()
        finally:
            db.close()


_worker = AlarmReconciliationWorker()


def start() -> None:
    _worker.start()


def stop() -> None:
    _worker.stop()


# time import — ileride kullanilabilir
_ = _time
