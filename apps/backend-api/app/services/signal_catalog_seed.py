"""Standart sinyal kataloğu seed'i (model bazli).

Sinyal tanımları `app/data/<model_code>_signals.json` dosyalarında saklanır.
Yeni bir cihaz modeli eklendiğinde aynı dizine `<model_code>_signals.json`
ekleyip `app/data/device_models.py` icinde MODELS sozlugune yeni satir
eklemek yeterlidir.

Horstmann SN2 cihazinda sinyaller 3 kaynaktan gelir (`master`, `sat01`,
`sat02`). Ayni etiket (ornegin "overcurrent_tripped") farkli kaynaklarda ayri
`key` ile tutulur ki alarmin hangi fazdan/uniteden geldigi karismasin.
"""

from __future__ import annotations

import json
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.data.device_models import DEFAULT_MODEL, MODELS
from app.models.signal_catalog import SignalCatalog


DATA_DIR = Path(__file__).resolve().parents[1] / "data"
# Geriye uyumluluk: erken kurulumlarda dosya `horstmann_sn2_signals.json`
# olarak isimlendirilmisti. Yeni kanon `<model_code>_signals.json`; ikisini de
# destekliyoruz ki eski deploylar bozulmasin.
_LEGACY_FILES: dict[str, str] = {
    "horstmann_sn_2_0": "horstmann_sn2_signals.json",
}


def _seed_path(model_code: str) -> Path:
    canonical = DATA_DIR / f"{model_code}_signals.json"
    if canonical.exists():
        return canonical
    legacy_name = _LEGACY_FILES.get(model_code)
    if legacy_name:
        return DATA_DIR / legacy_name
    return canonical  # var olmasa bile path geri don


def load_default_signals(model_code: str = DEFAULT_MODEL) -> list[dict]:
    """Verilen modele ait sinyal listesini JSON'dan yukler.

    Geri donen her satira `model` alani enjekte edilir; JSON'larda model
    bilgisi yoksa default olarak `model_code` set edilir.
    """
    path = _seed_path(model_code)
    if not path.exists():
        return []
    with open(path, "r", encoding="utf-8") as fh:
        items = json.load(fh)
    if not isinstance(items, list):
        return []
    enriched: list[dict] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        normalized = dict(item)
        normalized.setdefault("model", model_code)
        enriched.append(normalized)
    return enriched


# Geriye uyumluluk için eski import yolları (app.main tarafında log/diagnostik kullanabilir).
DEFAULT_SIGNALS: list[dict] = load_default_signals(DEFAULT_MODEL)


_MUTABLE_FIELDS = (
    "model",
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
    "iec104_ioa",
)


def _load_all_default_items() -> tuple[list[dict], set[str]]:
    """Tum desteklenen modellerin seed JSON'larini birlestirir."""
    items: list[dict] = []
    seen_models: set[str] = set()
    for model_code in MODELS.keys():
        loaded = load_default_signals(model_code)
        if not loaded:
            continue
        items.extend(loaded)
        seen_models.add(model_code)
    return items, seen_models


def seed_default_signals(
    db: Session, *, strict: bool = True, respect_user_overrides: bool = True
) -> dict:
    """Tum modellerin standart sinyal kataloglarini senkronize eder.

    Dönüş: {"inserted": N, "updated": M, "removed": R, "total": T, "kept": K}

    `strict=True`:
      - Aynı `key` varsa alanları günceller.
      - Yeni `key`'ler eklenir.
      - Seed dosyalarinda olmayan VE bu calistirmada islenen modellere ait
        eski kayitlar SILINIR (test / mock / silinmis sinyaller temizlenir).
        Sadece **bu calistirmada islenen modeller icin** silme yapilir;
        diger modellere ait kayitlar etkilenmez.

    `strict=False`:
      - Yalnızca upsert yapılır; listede olmayan kayıtlar dokunulmaz
        (kurulumcunun custom eklediği sinyaller korunur).

    `respect_user_overrides=True` (varsayilan):
      - `SignalCatalog.user_overrides` icinde adi gecen alanlar GUNCELLENMEZ.

      NEDEN: bu fonksiyon her backend acilisinda kosuyor ve `_MUTABLE_FIELDS`
      tam da kurulumcunun arayuzden degistirdigi alanlari iceriyor. Sistem
      "kaydedildi" deyip denetim kaydi tutuyor, sonra ilk yeniden baslatmada
      sessizce geri aliyordu — SCADA yanlis IOA'dan okuyor, olcek 10 kat
      sapiyor ve hicbir hata logu olusmuyordu.

      `False` yalnizca "fabrika ayarlarina don" ucu icin: orada geri donmek
      operatorun BILINCLI tercihidir.
    """
    items, seeded_models = _load_all_default_items()
    if not items:
        return {"inserted": 0, "updated": 0, "removed": 0, "total": 0, "skipped": True}

    existing = {row.key: row for row in db.scalars(select(SignalCatalog)).all()}
    default_keys = {item.get("key") for item in items if item.get("key")}
    inserted = 0
    updated = 0
    removed = 0
    kept = 0  # kullanici degisikligi korundugu icin ezilmeyen alan sayisi

    for data in items:
        key = data.get("key")
        if not key:
            continue
        current = existing.get(key)
        if current is None:
            db.add(SignalCatalog(**data))
            inserted += 1
            continue

        korunan = set()
        if respect_user_overrides:
            ham = getattr(current, "user_overrides", None)
            if isinstance(ham, list):
                korunan = {str(f) for f in ham}

        changed = False
        for field in _MUTABLE_FIELDS:
            if field in korunan:
                # Operator bu alani elle degistirmis — fabrika degeri EZMEZ.
                kept += 1
                continue
            new_value = data.get(field, getattr(current, field))
            if getattr(current, field) != new_value:
                setattr(current, field, new_value)
                changed = True
        if changed:
            updated += 1

    if strict:
        for key, row in existing.items():
            if key in default_keys:
                continue
            if row.model not in seeded_models:
                # Bu modeli bu calistirmada seed etmiyoruz -> dokunma.
                continue
            db.delete(row)
            removed += 1

    db.commit()
    return {
        "inserted": inserted,
        "updated": updated,
        "removed": removed,
        "kept": kept,
        "total": inserted + updated,
        "skipped": False,
    }


def clear_user_overrides(db: Session) -> int:
    """Tum sinyallerdeki kullanici degisiklik isaretlerini temizler.

    Yalnizca "fabrika ayarlarina don" ucu icin. Isaretler kalirsa o eylem
    yarim kalir: kullanicinin degistirdigi alanlar fabrika degerine DONMEZ
    ve operator "dondurdum" sanir.
    """
    temizlenen = 0
    for row in db.scalars(select(SignalCatalog)).all():
        if getattr(row, "user_overrides", None):
            row.user_overrides = None
            temizlenen += 1
    return temizlenen
