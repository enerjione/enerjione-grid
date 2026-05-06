from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.alarm import AlarmEvent
from app.models.device import Device
from app.models.enums import CommunicationStatus
from app.models.telemetry import Telemetry
from app.schemas.telemetry import TelemetryIn


def normalize_quality(raw_quality: str) -> str:
    return (raw_quality or "good").strip().lower()


# Gateway'in DNP3 adapter'leri "comm_lost" (TCP/link kopuk veya veri eskidi) ve
# "restart" (cihaz reboot etti, baseline bekleniyor) kalitelerini de yayinlar.
# Bu kalitelerin de OFFLINE'a map edilmesi sart — aksi halde gateway saha
# cihazinin haberlesmesi koptugunda son iyi degerin "online" gozukmesi devam
# eder. "no_change" zaten poller tarafindan yayinlanmadigi icin burada gormeyiz.
_OFFLINE_QUALITIES = frozenset({"bad", "offline", "invalid", "comm_lost", "restart"})


def map_quality_to_status(quality: str) -> CommunicationStatus:
    return (
        CommunicationStatus.OFFLINE
        if quality in _OFFLINE_QUALITIES
        else CommunicationStatus.ONLINE
    )


# Lithium pil voltaj-yüzde haritası (default; proje ayarlarindan override edilebilir).
DEFAULT_BATTERY_VOLTAGE_FULL = 3.71
DEFAULT_BATTERY_VOLTAGE_LOW = 3.40

# ProjectSettings DB query cache: her telemetry mesajinda DB'ye gitmeyelim.
# 600 cihazda saniyede ~10 battery sinyali olur, hepsi ayni satiri okur.
# 60 saniyelik cache yeterli (kullanici ayar degistirmesinden sonra max 1 dk).
_BATTERY_THRESHOLDS_CACHE: tuple[float, float, float] | None = None  # (low, full, cached_at_epoch)
_BATTERY_THRESHOLDS_TTL_SEC = 60.0


def _battery_thresholds(db: Session | None) -> tuple[float, float]:
    """Proje ayarlarindan (low, full) cek; yoksa default. 60sn TTL ile cache."""
    global _BATTERY_THRESHOLDS_CACHE
    import time as _time
    now = _time.monotonic()
    cached = _BATTERY_THRESHOLDS_CACHE
    if cached is not None and (now - cached[2]) < _BATTERY_THRESHOLDS_TTL_SEC:
        return cached[0], cached[1]
    if db is None:
        return DEFAULT_BATTERY_VOLTAGE_LOW, DEFAULT_BATTERY_VOLTAGE_FULL
    low = DEFAULT_BATTERY_VOLTAGE_LOW
    full = DEFAULT_BATTERY_VOLTAGE_FULL
    try:
        from app.models.project_settings import ProjectSettings
        row = db.get(ProjectSettings, 1)
        if row is not None:
            low = row.battery_voltage_low if row.battery_voltage_low is not None else DEFAULT_BATTERY_VOLTAGE_LOW
            full = row.battery_voltage_full if row.battery_voltage_full is not None else DEFAULT_BATTERY_VOLTAGE_FULL
            if full <= low:
                low, full = DEFAULT_BATTERY_VOLTAGE_LOW, DEFAULT_BATTERY_VOLTAGE_FULL
    except Exception:  # noqa: BLE001
        pass
    _BATTERY_THRESHOLDS_CACHE = (float(low), float(full), now)
    return float(low), float(full)


def _battery_percent_from_signal(
    signal_key: str, value: float, db: Session | None = None
) -> float | None:
    """Master `battery_voltage_satellite` sinyalinden yuzde turet.

    Eşikler proje ayarlarindan okunur (`battery_voltage_low/full`); ayar yoksa
    fallback 3.40 / 3.71 V kullanilir. value <= low → 0, value >= full → 100,
    arasi lineer."""
    if not signal_key:
        return None
    key = signal_key.lower()
    if key != "master.battery_voltage_satellite":
        return None
    low, full = _battery_thresholds(db)
    if value <= low:
        return 0.0
    if value >= full:
        return 100.0
    span = full - low
    if span <= 0:
        return None
    return round((value - low) / span * 100.0, 1)


def _auto_clear_quality_alarms(db: Session, device: Device) -> None:
    """Cihaz OFFLINE→ONLINE gectiginde acik haberlesme arizalarini otomatik reset et.

    Telemetri ingestion'da cagirilir; alarm-service'in in-memory state'ine bagli
    kalmak yerine backend'in kendi gercegine (Device.communication_status) gore
    karar verir. Bu sayede alarm-service yeniden baslasa bile dogru calisir.

    Eslesme kriteri: ayni device + reset=False + title icinde "haber" gecen.
    Eski/yeni format ("4853 haberlesme arizasi" vs "Haberleşme arızası") kapsanir.
    """
    rows = db.scalars(
        select(AlarmEvent)
        .where(AlarmEvent.device_id == device.id)
        .where(AlarmEvent.reset.is_(False))
        .where(AlarmEvent.title.ilike("%haber%"))
    ).all()
    now = datetime.now(timezone.utc)
    for alarm in rows:
        alarm.reset = True
        alarm.reset_at = now


def process_telemetry_reading(
    device: Device,
    reading: TelemetryIn,
    db: Session | None = None,
) -> tuple[Telemetry, dict[str, Any]]:
    normalized_quality = normalize_quality(reading.quality)
    previous_status = device.communication_status
    next_status = map_quality_to_status(normalized_quality)

    telemetry = Telemetry(
        device_id=device.id,
        signal_key=reading.signal_key,
        # DNP3 Group 110 (Octet String) sinyallerinde value=None gelir; numeric
        # tipler her zaman dolu olur. value_string sadece string sinyalde dolar.
        value=reading.value,
        value_string=reading.value_string,
        quality=normalized_quality,
        source_timestamp=reading.source_timestamp,
    )

    device.communication_status = next_status
    # last_update_at sadece gercek (online) bir okuma geldiginde guncellenir.
    # comm_lost/offline yayinlari "son iyi degerin uzerinden ne kadar gecti"
    # bilgisini bozmamali — frontend "Son veri: 5 dk once" sayacinin dogru
    # calismasi icin bu kritik.
    if next_status == CommunicationStatus.ONLINE:
        device.last_update_at = datetime.now(timezone.utc)
        # Batarya sinyali ise cihaz row'undaki battery_percent'i de senkronize et.
        # Sadece gercek (online) okumada — comm_lost/restart sirasinda son iyi
        # deger korunur.
        if reading.value is not None:
            try:
                derived = _battery_percent_from_signal(
                    reading.signal_key, float(reading.value), db=db
                )
            except (TypeError, ValueError):
                derived = None
            if derived is not None:
                device.battery_percent = derived
        # NOT: Otomatik "haberlesme arizasi" alarmlarini reset etmiyoruz —
        # haberlesme alarmi kullanici kendi tanimladigi kural uzerinden
        # uretiliyor; alarm-service zaten kosul karsilanmazsa clear cagrisi
        # yapiyor. Backend'in burada eskisi gibi otomatik silmesi yanlis
        # alarmlari kapatma riski tasiyordu.
        _ = previous_status  # ileride gerekirse kullanilmak uzere
        _ = db

    event_payload = {
        "message_id": reading.message_id,
        "correlation_id": reading.correlation_id or reading.message_id,
        "device_id": device.id,
        "device_code": device.code,
        "device_name": device.name,
        "signal_key": reading.signal_key,
        "quality": normalized_quality,
        "previous_status": previous_status.value if previous_status else None,
        "next_status": next_status.value,
        "source_timestamp": reading.source_timestamp.isoformat(),
    }
    return telemetry, event_payload
