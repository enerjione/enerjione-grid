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


# Lithium pil voltaj-yüzde haritası (Horstmann SN2 sahası).
# 3.71 V ve uzeri → %100 (full), 3.40 V ve alti → %0 (low). Aralarinda lineer.
BATTERY_VOLTAGE_FULL = 3.71
BATTERY_VOLTAGE_LOW = 3.40


def _battery_percent_from_signal(signal_key: str, value: float) -> float | None:
    """Sinyal degerinden master cihazin batarya yuzdesini turet.

    Master cihaz icin SADECE `master.battery_voltage_satellite` (analog, V)
    kullanilir. Voltaj 3.40V ve alti → 0%, 3.71V ve uzerinde → 100%; arasinda
    lineer interpolation. Saha ekibinin belirttigi lithium pil calisma araligi.

    `master.battery_status` (binary AKTIF/PASIF) GERCEK SoC degil — sadece
    pil takili mi bilgisi. Bu yuzden burada hesaba dahil etmiyoruz; aksi
    halde 1 doner ve hep %100 gozukur (mevcut hatanin sebebi).

    Sat01/sat02 batarya sinyalleri ileride genisletilecek (kullanicinin sonraki
    asamada istedigi 'satellite tiklayinca onun bataryasini gostermek')."""
    if not signal_key:
        return None
    key = signal_key.lower()
    if key == "master.battery_voltage_satellite":
        if value <= BATTERY_VOLTAGE_LOW:
            return 0.0
        if value >= BATTERY_VOLTAGE_FULL:
            return 100.0
        span = BATTERY_VOLTAGE_FULL - BATTERY_VOLTAGE_LOW
        return round((value - BATTERY_VOLTAGE_LOW) / span * 100.0, 1)
    return None


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
        value=reading.value,
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
                derived = _battery_percent_from_signal(reading.signal_key, float(reading.value))
            except (TypeError, ValueError):
                derived = None
            if derived is not None:
                device.battery_percent = derived
        # OFFLINE → ONLINE gecisinde acik "haberlesme arizasi" alarmlarini otomatik
        # reset et. Bu sayede alarm-service'in restart sonrasi state kaybi olsa
        # bile UI'daki aktif alarm dogru sekilde "Normale Donen" listesine duser.
        if previous_status == CommunicationStatus.OFFLINE and db is not None:
            _auto_clear_quality_alarms(db, device)

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
