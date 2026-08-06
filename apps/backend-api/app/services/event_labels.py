"""Olay etiketleri — EKRANDA ne yaziyorsa export'ta da o yazsin.

Arayuz olay mesajlarini `metadata_json._i18n` (key + params) uzerinden
`events.message.<key>` sablonuyla cevirir; kolon rozetlerini de olay
tipinden turetir. Export (PDF/Excel/CSV) ayni metni uretmezse kullanici
ekranda gordugunden BASKA bir rapor indirir.

Bu modul frontend'teki `formatEventMessage.ts` + `eventStatus.ts` +
`eventDisplayLabels.ts` mantiginin Python karsiligidir. Sablonlar
`app/data/event_labels_tr.json` dosyasindan okunur; bu dosya frontend
`tr.json`'un AYNASIDIR ve `tests/test_event_labels_parity.py` ikisinin
ayrismasini engeller (yeni bir olay mesaji eklenip buraya yansitilmazsa
test kirmizi olur).
"""

from __future__ import annotations

import json
import re
from functools import lru_cache
from pathlib import Path
from typing import Any

_LABELS_PATH = Path(__file__).resolve().parent.parent / "data" / "event_labels_tr.json"

# i18next yer tutucusu: {{title}}
_PLACEHOLDER_RE = re.compile(r"\{\{\s*(\w+)\s*\}\}")

# Kategori kodu -> i18n anahtari (frontend eventDisplayLabels.ts CATEGORY_KEY).
_CATEGORY_KEY = {
    "auth": "auth",
    "user": "user",
    "alarm": "alarm",
    "alarm-rule": "alarmRule",
    "alarm_assignment": "alarmAssignment",
    "alarm_comment": "alarmComment",
    "notification": "notification",
    "settings": "settings",
    "project-settings": "projectSettings",
    "outbound": "outbound",
    "outbound_target": "outboundTarget",
    "telemetry": "telemetry",
    "device": "device",
    "signal": "signal",
    "gateway": "gateway",
    "grid": "grid",
    "responsibility-area": "responsibilityArea",
    "responsibility_area": "responsibilityArea",
    "fault": "fault",
    "fault_assignment": "faultAssignment",
    "fault_comment": "faultComment",
    "system": "system",
    "security": "security",
}

# Olay tipi -> durum anahtari (frontend eventStatus.ts EXACT).
_STATUS_EXACT = {
    "alarm_triggered": "triggered",
    "alarm_created": "triggered",
    "alarm_auto_cleared": "cleared",
    "alarm_auto_cleared_acked": "cleared",
    "alarm_acknowledged": "acknowledged",
    "alarm_acknowledge_all": "acknowledged",
    "alarm_reset": "reset",
    "alarm_reset_all": "reset",
    "fault_opened": "faultOpen",
    "fault_resolved": "cleared",
    "fault_status_changed": "updated",
    "user_login": "login",
    "user_logout": "logout",
    "login_failed_locked": "failed",
    "device_command_queued": "queued",
    "device_command_sent": "sent",
    "device_command_result": "completed",
    "device_command_failed": "failed",
    "bulk_notification_sent": "sent",
}

# SIRA ONEMLI — ilk eslesen kazanir (frontend SUFFIX_RULES ile birebir).
_STATUS_SUFFIX_RULES: list[tuple[re.Pattern[str], str]] = [
    (
        re.compile(
            r"(_failed|_rejected|_error|_unreachable|_dead_letter|_denied|_locked|_tasmasi|_critical)$"
        ),
        "failed",
    ),
    (re.compile(r"(_warning|_backlog_high|_stale_devices_unknown)$"), "warning"),
    (re.compile(r"(_queued|_scheduled|_requested|_started|_pending)$"), "inProgress"),
    (re.compile(r"(_created|_added|_imported|_uploaded|_granted|_invited)$"), "created"),
    (re.compile(r"(_deleted|_removed|_purged|_revoked|_forgotten)$"), "deleted"),
    (
        re.compile(r"(_updated|_changed|_edited|_reordered|_reversed|_synced|_resent|_extended|_assigned)$"),
        "updated",
    ),
    (
        re.compile(
            r"(_ok|_delivered|_ingested|_finished|_recovered|_applied|_dispatched|_sent|_setup"
            r"|_enabled|_downloaded|_viewed|_reindexed|_ended)$"
        ),
        "success",
    ),
    (re.compile(r"_disabled$"), "disabled"),
]


@lru_cache(maxsize=1)
def _labels() -> dict[str, dict[str, str]]:
    with _LABELS_PATH.open(encoding="utf-8") as handle:
        return json.load(handle)


def _parse_metadata(metadata_json: str | None) -> dict[str, Any]:
    if not metadata_json:
        return {}
    try:
        parsed = json.loads(metadata_json)
    except (json.JSONDecodeError, TypeError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def format_message(message: str, metadata_json: str | None) -> str:
    """Ekrandaki tam mesaj: `_i18n` sablonu + params; yoksa ham `message`."""
    meta = _parse_metadata(metadata_json)
    tag = meta.get("_i18n")
    if not isinstance(tag, dict):
        return message or ""
    key = tag.get("key")
    if not isinstance(key, str) or not key:
        return message or ""
    template = _labels()["message"].get(key)
    if not template:
        return message or ""
    params = tag.get("params") if isinstance(tag.get("params"), dict) else {}

    def replace(match: re.Match[str]) -> str:
        name = match.group(1)
        # Eksik parametre: yer tutucuyu SILME, aksi halde "Alarm: " gibi yarim
        # metin cikar; frontend i18next de anahtari oldugu gibi birakir.
        return "" if name not in params else str(params[name])

    return _PLACEHOLDER_RE.sub(replace, template).strip()


def message_subject(message: str, metadata_json: str | None) -> str:
    """Ekrandaki MESAJ sutunu: eylem onekinden arindirilmis ozne.

    "Alarm kurali gerceklesti: Test alarmi" -> "Test alarmi"
    (frontend eventSubject ile ayni kural: ilk ": " ayraci, en fazla 60.
    karakterde).
    """
    full = format_message(message, metadata_json)
    idx = full.find(": ")
    if 0 < idx <= 60:
        rest = full[idx + 2 :].strip()
        if rest:
            return rest
    return full


def status_key(event_type: str) -> str:
    exact = _STATUS_EXACT.get(event_type)
    if exact:
        return exact
    for pattern, key in _STATUS_SUFFIX_RULES:
        if pattern.search(event_type):
            return key
    return "info"


def status_label(event_type: str) -> str:
    return _labels()["status"].get(status_key(event_type), event_type)


def category_label(category: str) -> str:
    key = _CATEGORY_KEY.get(category)
    if key:
        return _labels()["category"].get(key, category)
    # Bilinmeyen kategori: frontend _humanize ile ayni ("alarm-rule" -> "Alarm rule")
    cleaned = re.sub(r"[_-]+", " ", category or "").strip()
    if not cleaned:
        return _labels()["category"].get("other", "Diger")
    return cleaned[0].upper() + cleaned[1:]


def severity_label(severity: str) -> str:
    return _labels()["severity"].get(severity, severity)
