"""Desteklenen cihaz modelleri.

Yeni bir model eklemek icin:
  1. MODELS sozlugune yeni satir ekle (code -> label).
  2. Modele ait sinyal seed JSON'unu `app/data/<model_code>_signals.json`
     olarak ekle. `signal_catalog_seed.seed_default_signals` calistiginda
     otomatik olarak yuklenip ilgili modele baglanir.
  3. Frontend'de cihaz formundaki dropdown bu listeyi otomatik kullanir
     (GET /device-models endpoint'i uzerinden cekiliyor).

`code` veritabaninda `devices.model` ve `signal_catalog.model` kolonlarinda
saklanan kanonik degerdir; degistirme.
"""

from __future__ import annotations

DEFAULT_MODEL: str = "horstmann_sn_2_0"

MODELS: dict[str, str] = {
    "horstmann_sn_2_0": "Horstmann Smart Navigator 2.0",
}


def is_valid_model(code: str) -> bool:
    return code in MODELS


def model_label(code: str) -> str:
    return MODELS.get(code, code)


def list_models() -> list[dict[str, str]]:
    return [{"code": code, "label": label} for code, label in MODELS.items()]
