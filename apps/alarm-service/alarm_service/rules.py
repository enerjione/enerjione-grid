"""Alarm kurali cekmeyi ve degerlendirmeyi yoneten yardimci modul."""

import json
import logging
from dataclasses import dataclass, field
from threading import Lock
from typing import Any

import requests

_log = logging.getLogger(__name__)


@dataclass(frozen=True)
class CompositeTerm:
    """Composite kuraldaki tek bir terim.

    kind='compare' : anlik sinyal degerine comparator + threshold uygulanir.
    kind='agg'     : son agg_window_sec saniyedeki pencere uzerinden
                     agg_fn (avg/min/max/sum/count_above/count_below)
                     hesaplanir; sonra comparator + threshold uygulanir.
    """

    signal_key: str
    device_code: str  # "*" => anchor cihaz
    comparator: str
    threshold: float
    threshold_high: float | None
    kind: str = "compare"  # "compare" | "agg"
    agg_fn: str | None = None
    agg_window_sec: int = 60
    agg_arg: float = 0.0


@dataclass(frozen=True)
class CompositeExpression:
    logic: str  # "AND" | "OR"
    terms: tuple[CompositeTerm, ...]


@dataclass(frozen=True)
class AlarmRule:
    id: int
    signal_key: str
    name: str
    description: str
    level: str
    comparator: str
    threshold: float
    threshold_high: float | None
    hysteresis: float
    debounce_sec: int
    device_code_filter: str | None
    is_active: bool
    # Faz 1: composite kural destegi (geriye uyumlu, default simple).
    rule_kind: str = "simple"
    expression: CompositeExpression | None = None
    # Composite icin: ifadede atifta bulunulan tum anchor (her terimin
    # signal_key'i). Hangi sinyal-anahtarlari geldiginde bu kural yeniden
    # degerlendirilmeli? Anchor liste main.py'de cache lookup'a yardimci olur.
    composite_signal_keys: tuple[str, ...] = field(default_factory=tuple)

    def device_codes(self) -> set[str]:
        if not self.device_code_filter:
            return set()
        return {item.strip() for item in self.device_code_filter.split(",") if item.strip()}


class AlarmRuleCache:
    """Backend'den alarm kurallarini periyodik ceken in-memory cache."""

    def __init__(self, *, base_url: str, service_token: str, refresh_sec: int = 30, timeout_sec: int = 5) -> None:
        self.base_url = base_url.rstrip("/")
        self.service_token = service_token
        self.refresh_sec = refresh_sec
        self.timeout_sec = timeout_sec
        self._lock = Lock()
        self._rules_by_signal: dict[str, list[AlarmRule]] = {}
        self._alarmable_keys: set[str] = set()
        self._ready = False

    def refresh(self) -> bool:
        rules_url = f"{self.base_url}/internal/alarm-rules"
        signals_url = f"{self.base_url}/internal/signals"
        headers = {"X-Service-Token": self.service_token}
        try:
            rules_resp = requests.get(rules_url, headers=headers, timeout=self.timeout_sec)
            signals_resp = requests.get(signals_url, headers=headers, timeout=self.timeout_sec)
        except requests.RequestException as exc:
            print(f"alarm-rules-fetch-error error={exc}")
            return False
        if rules_resp.status_code != 200 or signals_resp.status_code != 200:
            print(
                f"alarm-rules-fetch-bad-status rules={rules_resp.status_code} "
                f"signals={signals_resp.status_code}"
            )
            return False
        rules_data: list[dict[str, Any]] = rules_resp.json()
        signals_data: list[dict[str, Any]] = signals_resp.json()
        # Onceden `supports_alarm` bayragi kuralin degerlendirilmesini engelliyordu;
        # artik UI/backend tum sinyallere kural ekleyebildigi icin bu filtreyi
        # kaldirdik. Sinyal katalog'da var olmasi yeterli sayilir; yine de
        # alarmable set'ini "katalogdaki tum sinyaller" olarak tutuyoruz ki
        # is_alarmable() rapor amacli dogru calisabilsin.
        alarmable = {s["key"] for s in signals_data}
        by_signal: dict[str, list[AlarmRule]] = {}
        for item in rules_data:
            if not item.get("is_active", True):
                continue
            if item["signal_key"] not in alarmable:
                # Sinyal katalogdan tamamen silinmis -> kural anlamsiz, atla.
                continue
            rule_kind = item.get("rule_kind") or "simple"
            expression: CompositeExpression | None = None
            composite_keys: tuple[str, ...] = ()
            if rule_kind == "composite":
                expression = _parse_composite_expression(item.get("expression"))
                if expression is None:
                    # Bozuk / eksik expression — composite kurali degerlendiremeyiz.
                    _log.warning(
                        "composite_rule_skipped rule_id=%s reason=invalid_expression",
                        item.get("id"),
                    )
                    continue
                # Her terimin sinyal_key'i katalogda olmali; yoksa atla.
                missing = [t.signal_key for t in expression.terms if t.signal_key not in alarmable]
                if missing:
                    _log.warning(
                        "composite_rule_skipped rule_id=%s reason=missing_signals %s",
                        item.get("id"),
                        missing,
                    )
                    continue
                composite_keys = tuple({t.signal_key for t in expression.terms})
            rule = AlarmRule(
                id=int(item["id"]),
                signal_key=item["signal_key"],
                name=item.get("name") or item["signal_key"],
                description=item.get("description") or "",
                level=item.get("level") or "warning",
                comparator=item.get("comparator") or "gt",
                threshold=float(item.get("threshold") or 0.0),
                threshold_high=(float(item["threshold_high"]) if item.get("threshold_high") is not None else None),
                hysteresis=float(item.get("hysteresis") or 0.0),
                debounce_sec=int(item.get("debounce_sec") or 0),
                device_code_filter=item.get("device_code_filter"),
                is_active=True,
                rule_kind=rule_kind,
                expression=expression,
                composite_signal_keys=composite_keys,
            )
            # Anchor signal_key + composite expression'daki tum sinyaller icin
            # rules_by_signal'a kayit at; engine herhangi biri geldiginde bu
            # kurali yeniden degerlendirir.
            by_signal.setdefault(rule.signal_key, []).append(rule)
            for ck in composite_keys:
                if ck == rule.signal_key:
                    continue
                by_signal.setdefault(ck, []).append(rule)
        with self._lock:
            self._rules_by_signal = by_signal
            self._alarmable_keys = alarmable
            self._ready = True
        return True

    def rules_for(self, signal_key: str, device_code: str | None) -> list[AlarmRule]:
        with self._lock:
            rules = list(self._rules_by_signal.get(signal_key, ()))
        if not device_code:
            return [rule for rule in rules if not rule.device_code_filter]
        matched: list[AlarmRule] = []
        for rule in rules:
            codes = rule.device_codes()
            if not codes or device_code in codes:
                matched.append(rule)
        return matched

    def is_alarmable(self, signal_key: str) -> bool:
        with self._lock:
            return signal_key in self._alarmable_keys

    def is_ready(self) -> bool:
        with self._lock:
            return self._ready


def evaluate_rule(rule: AlarmRule, value: float, *, prev_active: bool) -> bool:
    """Bir kuralin mevcut deger icin aktif olup olmadigini doner.

    Hysteresis: Kural zaten aktifse esik + hysteresis gerginse devam eder; yani
    aktivasyon esigi ile deaktivasyon esigi farkli.
    """
    t = rule.threshold
    hi = rule.threshold_high
    h = rule.hysteresis or 0.0
    cmp = rule.comparator

    def _gt(v: float, th: float) -> bool:
        return v > th

    def _gte(v: float, th: float) -> bool:
        return v >= th

    def _lt(v: float, th: float) -> bool:
        return v < th

    def _lte(v: float, th: float) -> bool:
        return v <= th

    if cmp == "gt":
        return _gt(value, t - h) if prev_active else _gt(value, t)
    if cmp == "gte":
        return _gte(value, t - h) if prev_active else _gte(value, t)
    if cmp == "lt":
        return _lt(value, t + h) if prev_active else _lt(value, t)
    if cmp == "lte":
        return _lte(value, t + h) if prev_active else _lte(value, t)
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


# ---------------------------------------------------------------------------
# Composite (Faz 1: AND/OR mantiksal birlesim)
# ---------------------------------------------------------------------------


def _parse_composite_expression(raw: Any) -> CompositeExpression | None:
    """Backend'den gelen 'expression' alanini CompositeExpression'a cevirir.

    Backend dict olarak doner (Pydantic model_dump). Bazi durumlarda string
    JSON da gelebilir (eski client). Her ikisini de destekler.
    """
    if raw is None:
        return None
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except json.JSONDecodeError:
            return None
    if not isinstance(raw, dict):
        return None
    logic = (raw.get("logic") or "AND").upper()
    if logic not in ("AND", "OR"):
        return None
    raw_terms = raw.get("terms") or []
    if not isinstance(raw_terms, list) or not raw_terms:
        return None
    terms: list[CompositeTerm] = []
    for t in raw_terms:
        if not isinstance(t, dict):
            return None
        sig = t.get("signal_key")
        cmp = t.get("comparator")
        if not isinstance(sig, str) or not isinstance(cmp, str):
            return None
        try:
            th = float(t.get("threshold") or 0.0)
        except (TypeError, ValueError):
            return None
        th_hi_raw = t.get("threshold_high")
        try:
            th_hi = float(th_hi_raw) if th_hi_raw is not None else None
        except (TypeError, ValueError):
            return None
        kind = t.get("kind") or "compare"
        agg_fn = t.get("agg_fn")
        try:
            agg_window = int(t.get("agg_window_sec") or 60)
        except (TypeError, ValueError):
            agg_window = 60
        try:
            agg_arg = float(t.get("agg_arg") or 0.0)
        except (TypeError, ValueError):
            agg_arg = 0.0
        if kind == "agg" and agg_fn not in {"avg", "min", "max", "sum", "count_above", "count_below"}:
            # Bilinmeyen agg fonksiyonu — bu kurali atla.
            return None
        terms.append(
            CompositeTerm(
                signal_key=sig,
                device_code=(t.get("device_code") or "*"),
                comparator=cmp,
                threshold=th,
                threshold_high=th_hi,
                kind=kind if kind in ("compare", "agg") else "compare",
                agg_fn=agg_fn if kind == "agg" else None,
                agg_window_sec=max(1, min(agg_window, 86400)),
                agg_arg=agg_arg,
            )
        )
    return CompositeExpression(logic=logic, terms=tuple(terms))


def _eval_compare(cmp: str, value: float, threshold: float, threshold_high: float | None) -> bool:
    """Tek bir terim icin karsilastirma (composite — hysteresis YOK, kullanici
    isterse simple modda ekleyebilir)."""
    if cmp == "gt":
        return value > threshold
    if cmp == "gte":
        return value >= threshold
    if cmp == "lt":
        return value < threshold
    if cmp == "lte":
        return value <= threshold
    if cmp == "eq":
        return value == threshold
    if cmp == "ne":
        return value != threshold
    if cmp == "between":
        if threshold_high is None:
            return False
        return threshold <= value <= threshold_high
    if cmp == "outside":
        if threshold_high is None:
            return False
        return value < threshold or value > threshold_high
    if cmp == "boolean_true":
        return value >= 0.5
    if cmp == "boolean_false":
        return value < 0.5
    return False


def evaluate_composite(
    rule: AlarmRule,
    *,
    anchor_device_code: str,
    live_value: "LiveValueLookup",
    agg_lookup: "AggLookup | None" = None,
) -> bool | None:
    """Composite kurali degerlendirir.

    `live_value(signal_key, device_code)` -> float | None : son canli deger
    yoksa None doner.
    `agg_lookup(signal_key, device_code, agg_fn, window_sec, agg_arg)` ->
      float | None : pencere icindeki istatistik. Yeterli veri yoksa None.
    Eger gerekli sinyallerden herhangi biri henuz gelmediyse sonuc None
    doner (engine kurali atlar, yarim degerlendirme yapmaz).
    """
    expr = rule.expression
    if expr is None or not expr.terms:
        return None
    results: list[bool] = []
    for term in expr.terms:
        dev = anchor_device_code if term.device_code == "*" else term.device_code
        if term.kind == "agg":
            if agg_lookup is None or term.agg_fn is None:
                return None
            v = agg_lookup(
                term.signal_key,
                dev,
                term.agg_fn,
                term.agg_window_sec,
                term.agg_arg,
            )
        else:
            v = live_value(term.signal_key, dev)
        if v is None:
            # Bilinmeyen veri — kurali bu turda atla (yanlis tetikleme yapma).
            return None
        results.append(_eval_compare(term.comparator, v, term.threshold, term.threshold_high))
    if expr.logic == "AND":
        return all(results)
    return any(results)


# Tip yardimcilari — main.py'de live-value cache + ring buffer'in cagri imzasi.
from typing import Callable, Optional

LiveValueLookup = Callable[[str, str], Optional[float]]
# agg_lookup(signal_key, device_code, agg_fn, window_sec, agg_arg) -> value | None
AggLookup = Callable[[str, str, str, int, float], Optional[float]]
