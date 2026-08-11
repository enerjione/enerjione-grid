"""Cihaz MODELINE ozel ayarlarin (cihaz profili) tek cozum noktasi.

Bugun tek ayar batarya voltaj esikleridir ama tablo ve cozum zinciri model
bazli her ayar icin ayni sekilde kullanilir.

COZUM ZINCIRI
-------------
    model ayari  ->  proje ayari  ->  kod varsayilani

Her katman KISMI doldurabilir: bir model icin yalnizca `low` girilmisse
`full` proje ayarindan (o da yoksa koddan) gelir. Boylece yeni bir model
eklendiginde yalnizca farkli olan deger yazilir, geri kalani mevcut
kurulumun konvansiyonundan devam eder.

NEDEN MODEL BAZLI
-----------------
Batarya esigi proje genelinde tek bir cifttti ve tum cihazlara
uygulaniyordu. Horstmann SN 2.0'in lityum hucresi ile Pole Master Kit'in
bataryasi ayni voltaj araliginda calismaz; tek esikle olculunce bir model
surekli "dolu", digeri surekli "bitmek uzere" gorunur. Bu SESSIZ bir
yanlisliktir — ne hata ne uyari uretir, yalnizca sayi yanlis olur.
"""

from __future__ import annotations

import time
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.data.device_models import PMK_SET_MODEL
from app.models.device_model_settings import DeviceModelSettings

#: Lityum pil voltaj-yuzde haritasi — zincirin EN ALT katmani.
DEFAULT_BATTERY_VOLTAGE_FULL = 3.71
DEFAULT_BATTERY_VOLTAGE_LOW = 3.40

#: Cihazin batarya yuzdesini SURUKLEYEN sinyalin slug'i.
BATTERY_SIGNAL_SLUG = "battery_voltage_satellite"

#: Modelin cihaz-seviyesi bataryasini hangi unite(ler) tasir.
#:
#: SN 2.0'da RTU'yu besleyen hucre `master` unitesindedir; sat01/sat02 kendi
#: pillerini bildirse de cihazin bataryasi master'inkidir.
#:
#: Pole Master Kit SETINDE `master` YOKTUR — set uc uydudan olusur ve ucunun
#: de AYRI bataryasi vardir. Bu yuzden burasi cok elemanlidir ve yuzde
#: hesabi EN DUSUK olanı esas alir: setin bakima ihtiyaci en zayif batarya
#: bittiginde dogar, ortalama ya da ilk unite bunu gizler.
BATTERY_UNITS_BY_MODEL: dict[str, tuple[str, ...]] = {
    PMK_SET_MODEL: ("sat01", "sat02", "sat03"),
}
DEFAULT_BATTERY_UNITS: tuple[str, ...] = ("master",)

#: Model basina esik cache'i. 600 cihazda saniyede ~10 batarya sinyali gelir
#: ve hepsi ayni satirlari okur; her mesajda DB'ye gitmek gereksiz. Ayar
#: degisikligi en gec bu sure sonunda gorunur.
_CACHE: dict[str, tuple[float, float, float]] = {}  # model -> (low, full, cached_at)
_CACHE_TTL_SEC = 60.0


def invalidate_cache(model: str | None = None) -> None:
    """Ayar yazildiktan sonra cache'i dusur (TTL beklenmesin)."""
    if model is None:
        _CACHE.clear()
    else:
        _CACHE.pop(model or "", None)


def battery_units(model: str | None) -> tuple[str, ...]:
    """Modelin batarya tasiyan uniteleri."""
    return BATTERY_UNITS_BY_MODEL.get(model or "", DEFAULT_BATTERY_UNITS)


def battery_signal_keys(model: str | None) -> tuple[str, ...]:
    """Modelin batarya yuzdesini suruklyen tam sinyal anahtarlari."""
    return tuple(f"{unit}.{BATTERY_SIGNAL_SLUG}" for unit in battery_units(model))


def _proje_esikleri(db: Session) -> tuple[float | None, float | None]:
    try:
        from app.models.project_settings import ProjectSettings

        row = db.get(ProjectSettings, 1)
    except Exception:  # noqa: BLE001
        return None, None
    if row is None:
        return None, None
    return row.battery_voltage_low, row.battery_voltage_full


def battery_thresholds(db: Session | None, model: str | None = None) -> tuple[float, float]:
    """Modelin (low, full) batarya esikleri. Zincir: model -> proje -> kod."""
    anahtar = model or ""
    now = time.monotonic()
    cached = _CACHE.get(anahtar)
    if cached is not None and (now - cached[2]) < _CACHE_TTL_SEC:
        return cached[0], cached[1]
    if db is None:
        return DEFAULT_BATTERY_VOLTAGE_LOW, DEFAULT_BATTERY_VOLTAGE_FULL

    low: float | None = None
    full: float | None = None
    if anahtar:
        try:
            row = db.get(DeviceModelSettings, anahtar)
        except Exception:  # noqa: BLE001
            row = None
        if row is not None:
            low = row.battery_voltage_low
            full = row.battery_voltage_full

    if low is None or full is None:
        proje_low, proje_full = _proje_esikleri(db)
        if low is None:
            low = proje_low
        if full is None:
            full = proje_full

    if low is None:
        low = DEFAULT_BATTERY_VOLTAGE_LOW
    if full is None:
        full = DEFAULT_BATTERY_VOLTAGE_FULL

    # Ters ya da sifir aralik yuzdeyi anlamsiz kilar (bolme sifir / negatif
    # yuzde). Katmanlar karisik geldiginde olusabilir, bu yuzden burada
    # yakalanip koda geri dusuluyor.
    if full <= low:
        low, full = DEFAULT_BATTERY_VOLTAGE_LOW, DEFAULT_BATTERY_VOLTAGE_FULL

    low_f, full_f = float(low), float(full)
    _CACHE[anahtar] = (low_f, full_f, now)
    return low_f, full_f


def voltage_to_percent(voltage: float, low: float, full: float) -> float | None:
    """Voltaji yuzdeye cevirir: <=low -> 0, >=full -> 100, arasi lineer."""
    if voltage <= low:
        return 0.0
    if voltage >= full:
        return 100.0
    span = full - low
    if span <= 0:
        return None
    return round((voltage - low) / span * 100.0, 1)


def resolve_settings(db: Session, model: str) -> dict:
    """API icin: modelin KAYITLI ve COZULMUS degerleri bir arada."""
    row = db.get(DeviceModelSettings, model)
    low, full = battery_thresholds(db, model)
    return {
        "model": model,
        "battery_voltage_low": row.battery_voltage_low if row else None,
        "battery_voltage_full": row.battery_voltage_full if row else None,
        "resolved_battery_voltage_low": low,
        "resolved_battery_voltage_full": full,
        "battery_units": list(battery_units(model)),
        "updated_at": row.updated_at if row else None,
    }


def upsert_settings(
    db: Session,
    model: str,
    *,
    battery_voltage_low: float | None,
    battery_voltage_full: float | None,
) -> DeviceModelSettings:
    """Model ayarini yaz/guncelle. NULL = "ust katmani kullan" (temizle)."""
    row = db.get(DeviceModelSettings, model)
    if row is None:
        row = DeviceModelSettings(model=model)
        db.add(row)
    row.battery_voltage_low = battery_voltage_low
    row.battery_voltage_full = battery_voltage_full
    row.updated_at = datetime.now(timezone.utc)
    invalidate_cache(model)
    return row


def battery_percent_for_device(
    db: Session,
    device_id: int,
    model: str | None,
    incoming_key: str,
    incoming_value: float,
) -> float | None:
    """Gelen batarya sinyalinden cihazin batarya yuzdesini turetir.

    Tek bataryali modellerde (SN 2.0) dogrudan gelen deger kullanilir.

    COK BATARYALI modellerde (Pole Master Kit seti) setin uc unitesinin
    ayri bataryasi vardir ve cihazin yuzdesi EN DUSUK olanidir: ekip direge
    en zayif hucre bittiginde cikar. Diger unitelerin son degeri
    `telemetry_latest`ten okunur (device_id + signal_key birincil anahtar,
    tek indeks erisimi). Henuz veri gelmemis unite hesaba katilmaz.
    """
    anahtarlar = battery_signal_keys(model)
    if incoming_key not in anahtarlar:
        return None
    low, full = battery_thresholds(db, model)

    if len(anahtarlar) == 1:
        return voltage_to_percent(incoming_value, low, full)

    from app.models.telemetry_latest import TelemetryLatest

    digerleri = [k for k in anahtarlar if k != incoming_key]
    voltajlar = [incoming_value]
    if digerleri:
        satirlar = db.execute(
            select(TelemetryLatest.value).where(
                TelemetryLatest.device_id == device_id,
                TelemetryLatest.signal_key.in_(digerleri),
            )
        ).scalars()
        voltajlar.extend(float(v) for v in satirlar if v is not None)
    return voltage_to_percent(min(voltajlar), low, full)
