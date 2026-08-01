from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.alarm import AlarmEvent
from app.models.device import Device
from app.models.enums import CommunicationStatus
from app.models.telemetry import Telemetry
from app.schemas.telemetry import TelemetryIn
from app.services.device_clock_service import assess_device_timestamp


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


# NOKTA seviyesinde gelebilen ama CIHAZI offline SAYMAMASI gereken kaliteler.
#
# `invalid`  : bu tek olcum gecersiz (ONLINE=0 / OVER_RANGE / REFERENCE_ERR)
# `restart`  : outstation reboot etti, bu nokta icin baseline bekleniyor
# `forced`   : operator noktayi elle zorlamis (LOCAL/REMOTE_FORCED)
#
# Cihaz seviyesinde ayni token'lar OFFLINE demektir; ayrimi `dnp3_flags`in
# VARLIGI yapar (bkz. schemas/telemetry.py).
_POINT_LEVEL_QUALITIES = frozenset({"invalid", "restart", "forced"})

# Alarm degerlendirmesini engelleyen kaliteler — HABERLESME DURUMUNDAN AYRI
# bir kume. Iki karar farklidir:
#   * haberlesme durumu : "cihaz ayakta mi?"       -> _OFFLINE_QUALITIES
#   * alarm degerlendirme: "bu olcume guvenilir mi?" -> burasi
#
# `forced` farkin somut ornegi: operator noktayi ELLE zorlamis. Cihaz
# ayaktadir (ONLINE) ama deger YAPAY bir degerdir — o degerden alarm uretmek
# ya da acik bir alarmi o degerle kapatmak yanlis olur.
ALARM_BLOCKING_QUALITIES = frozenset(_OFFLINE_QUALITIES | {"forced"})

#: `device.last_update_at` en fazla bu sıklıkta yazılır (saniye).
#:
#: OLCUM (saha test cihazi, 12 cihaz): alan her okumada yazildiginda
#: `devices` tablosuna saniyede ~36 UPDATE gidiyordu. Tablo 12 SATIR ve
#: 6 indeksli; her UPDATE yeni satir surumu + 6 indeks girdisi + WAL uretiyor,
#: autovacuum surekli o tabloda calisiyor ve `devices` neredeyse her sorguda
#: okundugu icin kirlenen sayfalar onbellegi de bozuyor.
#:
#: Alanin tek tuketicisi arayuzdeki "Son veri: X once" gostergesi; birkac
#: saniyelik gecikme orada GORUNMEZ. Karar veren bir esikte kullanilmiyor —
#: cihazin cevrimici olup olmadigi `communication_status` ile belirleniyor ve
#: O ALAN KISILMIYOR, durum degisimi aninda yazilir.
LAST_UPDATE_WRITE_THROTTLE_SEC = 5.0


def should_write_last_update(
    previous: datetime | None, now: datetime | None = None
) -> bool:
    """`device.last_update_at` SIMDI yazilmali mi?

    Karar TEK YERDE: hem ana yol hem hata yolu bunu cagirir. Kosulu iki yere
    kopyalamak, birinin sessizce eski (kisilmamis) davranisa donmesi demekti.

    `previous is None` -> HEMEN yaz. Cihaz hic gorulmemisse gecikme kabul
    edilemez; arayuzde "hic veri gelmedi" gorunurdu.
    """
    if now is None:
        now = datetime.now(timezone.utc)
    if previous is None:
        return True
    if previous.tzinfo is None:
        # Eski kayitlarda naive deger olabilir; cikarma islemi TypeError
        # atarsa telemetri alimi TAMAMEN durur.
        previous = previous.replace(tzinfo=timezone.utc)
    return (now - previous).total_seconds() >= LAST_UPDATE_WRITE_THROTTLE_SEC


def map_quality_to_status_scoped(
    quality: str, *, dnp3_flags: int | None
) -> CommunicationStatus:
    """Kaliteyi cihaz haberlesme durumuna cevirir — KAPSAM farkindalikli.

    NEDEN AYRI FONKSIYON:
      `invalid` / `restart` token'lari iki farkli kapsamda uretiliyor.
        * Eski gateway (0.4.x, legacy dnp3_master): CIHAZ seviyesi —
          "tum cihaz okunamadi". OFFLINE dogru karardir.
        * Yeni gateway (0.5.0+, map_dnp3_quality): NOKTA seviyesi —
          "bu tek olcum gecersiz". Cihaz pekala ayakta ve diger 192 sinyali
          saglikli olabilir.
      Ayrimi yapmadan yeni gateway'in bayragini acmak, tek bir noktanin
      referans hatasinda TUM CIHAZI offline gosterirdi: harita kirmizi,
      "son veri" sayaci donar, battery senkronu durur.

    `comm_lost` her iki kapsamda da CIHAZ seviyesidir (DNP3/TCP baglantisi
    yok) — nokta bazli bayrak tasisa bile OFFLINE kalir.
    """
    q = normalize_quality(quality)
    if dnp3_flags is not None and q in _POINT_LEVEL_QUALITIES:
        # Nokta bazli sorun: olcum kotu ama CIHAZ ayakta.
        return CommunicationStatus.ONLINE
    return map_quality_to_status(q)


def quality_blocks_alarm(quality: str | None) -> bool:
    """Bu kalitedeki bir okuma alarm degerlendirmesine GIRMELI Mi?

    True donerse okuma alarm mantiginda HIC KULLANILMAZ: ne yeni alarm acar
    ne de acik bir alarmi kapatir. Alarm durumu DONAR.

    NEDEN KAPATMAMAK ASIL MESELE:
      Haberlesmesi kopan bir cihaz icin gateway `comm_lost` kalitesiyle 0.0
      basar. Kalite kontrolu olmadan bu 0.0, "esik artik saglanmiyor" diye
      yorumlanip ACIK ARIZA ALARMINI KAPATIR ve harita yesile doner. Yani
      cihazla baglanti koptugu anda sistem "ariza gecti" der.
      SCADA doktrini bunun tersidir: veri kaybinda SON BILINEN DURUM korunur.

    KARA LISTE (beyaz liste DEGIL) — bilincli:
      Taninmayan bir kalite token'i alarm degerlendirmesini ENGELLEMEZ.
      Beyaz liste olsaydi, gateway ileride yeni bir kalite adi yayinladiginda
      tum alarm motoru sessizce durabilirdi. Bilinmeyen token'da degerlendirme
      devam eder — gurultulu hata, sessiz korlukten iyidir.
    """
    if quality is None:
        return False
    return normalize_quality(str(quality)) in ALARM_BLOCKING_QUALITIES


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
    # KAPSAM FARKINDALIKLI: `dnp3_flags` varsa kalite NOKTA seviyesindedir ve
    # `invalid`/`restart`/`forced` cihazi offline SAYMAZ. Bkz.
    # map_quality_to_status_scoped ve schemas/telemetry.py'deki gerekce.
    next_status = map_quality_to_status_scoped(
        normalized_quality, dnp3_flags=getattr(reading, "dnp3_flags", None)
    )

    # Cihazin kendi olay damgasi + o damganin guvenilirligi. Kaliteden AYRI
    # tutulur: saat kaymasi olcumu gecersiz kilmaz, dolayisiyla `quality`ye
    # karismaz ve alarm akisini ETKILEMEZ (bkz. models/telemetry.py).
    device_event_at, timestamp_quality = assess_device_timestamp(
        getattr(reading, "device_event_at", None),
        reported_quality=getattr(reading, "timestamp_quality", None),
    )

    telemetry = Telemetry(
        device_id=device.id,
        signal_key=reading.signal_key,
        # DNP3 Group 110 (Octet String) sinyallerinde value=None gelir; numeric
        # tipler her zaman dolu olur. value_string sadece string sinyalde dolar.
        value=reading.value,
        value_string=reading.value_string,
        quality=normalized_quality,
        source_timestamp=reading.source_timestamp,
        device_event_at=device_event_at,
        timestamp_quality=timestamp_quality,
    )

    device.communication_status = next_status
    # last_update_at sadece gercek (online) bir okuma geldiginde guncellenir.
    # comm_lost/offline yayinlari "son iyi degerin uzerinden ne kadar gecti"
    # bilgisini bozmamali — frontend "Son veri: 5 dk once" sayacinin dogru
    # calismasi icin bu kritik.
    if next_status == CommunicationStatus.ONLINE:
        # YAZMA KISMA — bkz. LAST_UPDATE_WRITE_THROTTLE_SEC.
        #
        # Bu alan HER okumada yazilirsa 12 cihazlik test kurulumunda bile
        # `devices` tablosuna saniyede ~36 UPDATE gidiyordu (olculdu). Tablo
        # 12 SATIR ve 6 indeksli; her UPDATE yeni bir satir surumu + 6 indeks
        # girdisi + WAL uretiyor ve autovacuum surekli bu tabloda calisiyor.
        # `devices` neredeyse her sorguda okundugu icin surekli kirlenen
        # sayfalar onbellegi de bozuyor.
        #
        # Alanin tek tuketicisi arayuzdeki "Son veri: 5 dk once" sayaci;
        # saniyede uc kez yazmanin kullaniciya hicbir karsiligi yok.
        simdi = datetime.now(timezone.utc)
        if should_write_last_update(device.last_update_at, simdi):
            device.last_update_at = simdi
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
