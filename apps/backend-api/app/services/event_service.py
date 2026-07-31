import json
from datetime import datetime, timezone
from typing import Any

from sqlalchemy.orm import Session

from app.models.system_event import SystemEvent


def record_event(
    db: Session,
    *,
    category: str,
    event_type: str,
    message: str,
    severity: str = "info",
    actor_username: str | None = None,
    device_code: str | None = None,
    metadata: dict[str, Any] | None = None,
    i18n_key: str | None = None,
    i18n_params: dict[str, Any] | None = None,
    occurred_at: datetime | None = None,
) -> None:
    """System event yaz.

    i18n_key + i18n_params verilirse, frontend bu anahtari kendi resource
    dosyalarinda (`events.message.<key>`) cevirip gosterir; backward-compat
    icin `message` (genelde TR) yine yazilir ve frontend i18n_key yoksa
    fallback olarak onu kullanir.

    occurred_at: olay backend DISINDA gerceklestiyse (host ajani, or. uzaktan
    bakim izninin suresi dolunca kapanmasi) GERCEK zamani yaz. Verilmezse
    "simdi" kullanilir. Bu olaylar ancak bir sonraki durum okumasinda
    ogrenildigi icin, "simdi" yazmak denetim zaman cizgisini bozardi.
    """
    md: dict[str, Any] = dict(metadata) if metadata else {}
    if i18n_key:
        md["_i18n"] = {"key": i18n_key, "params": i18n_params or {}}
    row = SystemEvent(
        category=category,
        event_type=event_type,
        severity=severity,
        message=message,
        actor_username=actor_username,
        device_code=device_code,
        metadata_json=json.dumps(md, ensure_ascii=False) if md else None,
        created_at=occurred_at or datetime.now(timezone.utc),
    )
    db.add(row)
