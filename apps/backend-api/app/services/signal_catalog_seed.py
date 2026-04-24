"""Horstmann Smart Navigator 2.0 standart sinyal kataloğu seed'i.

Sinyal tanımları `app/data/horstmann_sn2_signals.json` içinde saklanır.
Cihazın DNP3 listesi master + 2 satellite üzerinden toplam 3 faz bilgisini
getirdiği için her sinyal kaynağa (`master`, `sat01`, `sat02`) göre ayrı
`key` ile tutulur. Bu sayede alarmın hangi fazdan/uniteden geldiği karışmaz.
"""

from __future__ import annotations

import json
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.signal_catalog import SignalCatalog


DATA_FILE = Path(__file__).resolve().parents[1] / "data" / "horstmann_sn2_signals.json"


def load_default_signals() -> list[dict]:
    """Horstmann SN2 varsayılan sinyal listesini JSON'dan yükler."""
    if not DATA_FILE.exists():
        return []
    with open(DATA_FILE, "r", encoding="utf-8") as fh:
        items = json.load(fh)
    if not isinstance(items, list):
        return []
    return items


# Geriye uyumluluk için eski import yolları (app.main tarafında log/diagnostik kullanabilir).
DEFAULT_SIGNALS: list[dict] = load_default_signals()


_MUTABLE_FIELDS = (
    "label",
    "unit",
    "description",
    "source",
    "dnp3_class",
    "data_type",
    "dnp3_object_group",
    "dnp3_index",
    "scale",
    "offset",
    "supports_alarm",
    "display_order",
    "iec104_type_id",
    "iec104_ioa_offset",
)


def seed_default_signals(db: Session, *, strict: bool = True) -> dict:
    """Horstmann SN2 standart sinyal kataloğunu senkronize eder.

    Dönüş: {"inserted": N, "updated": M, "removed": R, "total": T}

    `strict=True` (varsayılan):
      - Aynı `key` varsa alanları günceller.
      - Yeni `key`'ler eklenir.
      - JSON listesinde **olmayan** tüm sinyaller SİLİNİR (mock / test /
        eski kayıtlar otomatik temizlenir). Bu mod; sinyal kataloğunu
        standart fabrika listesi olarak tutmak için kullanılır.

    `strict=False`:
      - Yalnızca upsert yapılır; listede olmayan kayıtlar dokunulmaz
        (kurulumcunun custom eklediği sinyaller korunur).
    """
    items = load_default_signals()
    if not items:
        return {"inserted": 0, "updated": 0, "removed": 0, "total": 0, "skipped": True}

    existing = {row.key: row for row in db.scalars(select(SignalCatalog)).all()}
    default_keys = {item.get("key") for item in items if item.get("key")}
    inserted = 0
    updated = 0
    removed = 0

    for data in items:
        key = data.get("key")
        if not key:
            continue
        current = existing.get(key)
        if current is None:
            db.add(SignalCatalog(**data))
            inserted += 1
            continue
        changed = False
        for field in _MUTABLE_FIELDS:
            new_value = data.get(field, getattr(current, field))
            if getattr(current, field) != new_value:
                setattr(current, field, new_value)
                changed = True
        if changed:
            updated += 1

    if strict:
        for key, row in existing.items():
            if key not in default_keys:
                db.delete(row)
                removed += 1

    db.commit()
    return {
        "inserted": inserted,
        "updated": updated,
        "removed": removed,
        "total": inserted + updated,
        "skipped": False,
    }
