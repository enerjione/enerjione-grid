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

from sqlalchemy import select

from app.db.session import SessionLocal
from app.models.alarm import AlarmEvent
from app.models.alarm_rule import AlarmRule
from app.models.telemetry import Telemetry
from app.services.event_service import record_event

logger = logging.getLogger(__name__)


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
    """Alarmi onay durumuna gore sil veya reset et. Action stringini doner."""
    if alarm.acknowledged:
        record_event(
            db,
            category="alarm",
            event_type="alarm_auto_cleared",
            severity="info",
            message=f"Onaylanmış alarm normale döndü ve silindi (reconcile): {alarm.title}",
            metadata={
                "alarm_id": alarm.id,
                "device_id": alarm.device_id,
                "signal_key": alarm.signal_key,
                "reason": reason,
                "auto_deleted": True,
            },
        )
        db.delete(alarm)
        return "deleted"
    alarm.reset = True
    alarm.reset_at = datetime.now(timezone.utc)
    record_event(
        db,
        category="alarm",
        event_type="alarm_auto_cleared",
        severity="info",
        message=f"Alarm sahada normale döndü (reconcile): {alarm.title}",
        metadata={
            "alarm_id": alarm.id,
            "device_id": alarm.device_id,
            "signal_key": alarm.signal_key,
            "reason": reason,
        },
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
            open_alarms = list(
                db.scalars(
                    select(AlarmEvent).where(AlarmEvent.reset.is_(False))
                ).all()
            )
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
            cleared_count = 0
            for alarm in open_alarms:
                rule = rules_by_name.get(alarm.title)
                if rule is None:
                    continue  # Kural silinmis veya pasif — drift; yine de
                    # otomatik silmek riskli (kullanici bilerek kurali silmis
                    # olabilir, alarm tarihce icin durabilir). Manuel silinsin.
                if not alarm.signal_key:
                    continue  # Eski kayit — sinyal bilinmiyor; reconcile edilemez.
                # Bu cihaz icin bu sinyalin son telemetri degeri
                last = db.scalar(
                    select(Telemetry)
                    .where(Telemetry.device_id == alarm.device_id)
                    .where(Telemetry.signal_key == alarm.signal_key)
                    .where(Telemetry.source_timestamp >= cutoff)
                    .order_by(Telemetry.source_timestamp.desc())
                    .limit(1)
                )
                if last is None or last.value is None:
                    continue  # Yeterli yeni veri yok — guvenli karar veremeyiz.
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
                    from app.services.fault_recompute_service import recompute_faults
                    recompute_faults(db)
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
