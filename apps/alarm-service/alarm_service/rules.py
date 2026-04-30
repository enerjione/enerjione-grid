"""Alarm kurali cekmeyi ve degerlendirmeyi yoneten yardimci modul."""

from dataclasses import dataclass
from threading import Lock
from typing import Any

import requests


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
            )
            by_signal.setdefault(rule.signal_key, []).append(rule)
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
