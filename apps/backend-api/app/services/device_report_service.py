"""CIHAZ DURUM RAPORU (PDF) — cihazin O ANKI durumunun tek dosyalik ozeti.

NE ISE YARIYOR
--------------
Cihaz detay sayfasi bes sekmeye yayilmis bir panodur: sol panelde kimlik ve
haberlesme, "Genel Bakis"ta olcumler, "Tumu"nde kanal kanal sinyaller, "Pole
Master"da kit seviyesi degerler, "Olaylar"da gecmis. Sahaya cikan ekibe ya da
musteriye bunun TAMAMI tek belge olarak lazim oluyor — ekran goruntusu
yollamak kanal secimine, pencere genisligine ve o anki sekmeye bagli bir
sey uretir.

Rapor Ariza Raporu ile AYNI sablondan cikar (`report_layout`): solda
EnerjiOne, sagda musteri logosu, altta "Sayfa X / Y". Iki belge de ayni
kurumdan cikmis gorunmeli.

CIHAZ TURU RAPORUN ISKELETINI DEGISTIRIR
----------------------------------------
Sistemde uc ayri sey "cihaz" diye gecer ve UCU DE FARKLI bir belge ister:

  simple (Horstmann SN 2.0)
      Kendi RTU'su + uc olcum unitesi (master/sat01/sat02). `master` HEM
      RTU'dur HEM olcum yapar.

  kit (Horstmann Pole Master Kit — fiziksel kayit)
      OLCUM YAPMAZ. Uzerindeki dokuz uydu setlere yonlendirilir; kitin
      kaydinda yalnizca `master.*` (modem, GPS, solar/AC besleme, cihaz
      sicakligi) kalir. Raporu bir "kit sagligi + bagli setler" belgesidir;
      olcum kanali bolumu HIC basilmaz.

  set (Pole Master Kit seti — sanal kayit)
      Ucu de uydu olan olcum kanallari (sat01/sat02/sat03), ama kendi
      modemi/IP'si/RTU pili YOKTUR — o degerler KIT kaydindan okunur
      (bkz. `device_kit_service.master_source_device`). Sette bunlari kendi
      kaydindan okumak sonsuza kadar bos bir bolum basardi.

ISKELET SABIT DEGIL, VERIDEN TURETILIR
--------------------------------------
Yukaridaki uc tur ISIMLE degil, sinyal katalogundan cikarilan olculerle
ayrilir (bkz. `_olcum_kanallari`): "bu kaynak bir olcum unitesi mi" sorusu,
kaynagin katalogunda akim/ariza noktasi olup olmadigina bakilarak
yanitlanir. Boylece katalogdan tanimlanan YENI bir model (sistem bunu surum
cikarmadan destekliyor, bkz. `app/data/device_models.py`) rapora
kendiliginden dogru iskeletle girer — kod degisikligi gerekmez.

DIL: rapor TURKCE tek dilde uretilir (Ariza ve Olay Raporu ile ayni tercih).
Sinyal adlari ekranda ne yaziyorsa raporda da o yazar; ceviri
`signal_labels` uzerinden, frontend `tr.json`'un aynasindan gelir.
"""

from __future__ import annotations

import io
import math
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.platypus import (
    Image,
    KeepTogether,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.data.device_models import (
    is_kit_model,
    is_stored_signal,
    model_label,
    resolve_subunit_satellites,
)
from app.models.alarm import AlarmEvent
from app.models.device import Device
from app.models.gateway import Gateway
from app.models.grid_topology import Line, LineSegment, Pole, Region
from app.models.project_settings import ProjectSettings
from app.models.signal_catalog import SignalCatalog
from app.models.system_event import SystemEvent
from app.models.telemetry_latest import TelemetryLatest
from app.services import device_kit_service, device_profile_service, event_labels
from app.services.report_layout import (
    CONTENT_WIDTH,
    FOOTER_HEIGHT,
    HEADER_HEIGHT,
    INK,
    RULE,
    ReportCanvas,
    ReportStyles,
    block,
    data_table,
    decode_data_url_image,
    esc,
    format_report_time,
    kv_grid,
    section_head,
    stat_strip,
    status_pill,
    upper_tr,
)
from app.services.signal_labels import signal_label, source_label

CONTENT_W = CONTENT_WIDTH

# --- Renkler (arayuzdeki rozetlerle ayni) ----------------------------------
C_OK = "#10b981"
C_ALARM = "#dc2626"
C_WARN = "#b45309"
C_MUTED = "#64748b"

#: Gateway'i "canli" sayma esigi — arayuzdeki `GATEWAY_LIVE_SEC` ile AYNI.
#: Ayrisirsa ayni okuma icin ekran "guvenilir", rapor "guvenilmez" der.
GATEWAY_LIVE_SEC = 60

#: Rapora girecek en fazla olay satiri. Cihaz detayindaki "Son Olaylar"
#: karti gibi: belge bir denetim dokumu degil, o anki durumun ozeti.
MAX_EVENTS = 14

#: BILGI / ALTYAPI sinyalleri — kanal tablolarinda DEGIL, RTU bolumunde
#: gorunur. Arayuzdeki `INFO_SUFFIX_RE` ile birebir ayni desen; ayrisirsa
#: ayni sinyal ekranda bir bolumde, raporda baskasinda cikar.
_INFO_SUFFIX_RE = re.compile(
    r"^info_|(serial_number|ipv4_address|ip_address|firmware|fw_version|modem|imei"
    r"|sim_serial|gps|latitude|longitude|hardware_revision|part_no|rtu_status"
    r"|network|operation_mode|device_position|test_point_level|comm_library|dial_in)"
)

#: Bir kaynagin OLCUM UNITESI olup olmadiginin isareti.
#:
#: Kaynak adina ("master uydu degildir" gibi) bakmak yanlis olurdu: SN 2.0'da
#: `master` olcum yapar, Pole Master Kit'te YAPMAZ — ayni ad, iki farkli rol.
#: Dogru soru "bu kaynagin katalogunda akim/ariza noktasi var mi"; cevap
#: veridedir ve yeni modeller icin de kendiliginden dogrudur.
_OLCUM_ISARETI = frozenset({"actual_current", "overcurrent_tripped", "fault_current"})

#: Kanal tablolarinin grup sirasi — arayuzdeki `GROUP_ORDER` ile ayni.
_GRUP_SIRASI: tuple[tuple[str, str], ...] = (
    ("protection", "Koruma / Arıza Yönü"),
    ("measure", "Ölçümler"),
    ("status", "Durum"),
    ("counter", "Sayaçlar"),
)

#: RTU / kit seviyesi satirlarin gruplari — arayuzdeki `PoleMasterTab.GROUPS`.
_RTU_GRUPLARI: tuple[tuple[str, str, re.Pattern[str]], ...] = (
    ("power", "Besleme", re.compile(r"(solar_power|ac_power|battery|boost_mode)")),
    (
        "comm",
        "Haberleşme",
        re.compile(
            r"(modem|rssi|network|sim_serial|ipv4|ip_address|imei|dial_in"
            r"|comm_library|rtu_status)"
        ),
    ),
    (
        "identity",
        "Kimlik ve Konum",
        re.compile(
            r"(gps|latitude|longitude|serial|part_no|firmware|fw_version"
            r"|hardware_revision|last_configuration)"
        ),
    ),
    (
        "status",
        "Cihaz Durumu",
        re.compile(
            r"(temperature|tamper|operation_mode|password|local_comm|fast_curve"
            r"|test|voltage_loss_all_units)"
        ),
    ),
)

#: Alarm engelleyen kaliteler — `tag_engine_service.ALARM_BLOCKING_QUALITIES`
#: ve frontend `signalQuality.BLOCKING_QUALITIES` ile AYNI kume olmali.
#: Ayrisirsa rapor, sunucunun "bu okumaya guvenme" kararini gecersiz kilar.
_ENGELLEYEN_KALITELER = frozenset(
    {"bad", "offline", "invalid", "comm_lost", "restart", "forced"}
)

_FAZ_ETIKET = {"a": "A", "b": "B", "c": "C"}


# ===========================================================================
# Veri toplama
# ===========================================================================
@dataclass
class ReportChannel:
    """Bir olcum unitesi (kanal) — ekrandaki kanal dugmesinin karsiligi."""

    source: str
    label: str
    #: Sette kanalin FIZIKSEL uydu numarasi (set 2 -> 4/5/6). Sade cihazda None.
    satellite_no: int | None
    serial: str | None
    battery_percent: float | None
    phase: str | None
    current: str
    voltage: str
    temperature: str


@dataclass
class ReportSignalRow:
    """Bir sinyalin TUM kanallardaki hali — satir sinyal, sutun kanal."""

    suffix: str
    label: str
    #: kaynak -> (metin, renk). Renk None ise duz metin.
    values: dict[str, tuple[str, str | None]]


@dataclass
class ReportSubunit:
    """Kit raporundaki bir bagli set satiri."""

    code: str
    name: str
    satellites: str
    line_name: str
    battery: str
    alarm_count: int


@dataclass
class DeviceReportData:
    device: Device
    #: "simple" | "kit" | "set"
    kind: str
    model_label: str
    #: Kit seviyesi/RTU verisini TASIYAN kayit (sette kit, digerlerinde kendisi).
    rtu_device: Device | None
    gateway: Gateway | None
    gateway_online: bool
    region_name: str
    line_name: str
    pole_span: str
    latitude: float | None
    longitude: float | None
    channels: list[ReportChannel] = field(default_factory=list)
    #: grup anahtari -> satirlar
    groups: dict[str, list[ReportSignalRow]] = field(default_factory=dict)
    #: Veri gelmedigi icin kanal tablolarina girmeyen nokta sayisi.
    hidden_signal_count: int = 0
    #: RTU grup anahtari -> (etiket, deger) ciftleri
    rtu_groups: dict[str, list[tuple[str, str]]] = field(default_factory=dict)
    rtu_missing_count: int = 0
    alarms: list[AlarmEvent] = field(default_factory=list)
    events: list[SystemEvent] = field(default_factory=list)
    subunits: list[ReportSubunit] = field(default_factory=list)
    network_dbm: float | None = None
    network_operator: str | None = None
    permanent_faults: str = "—"
    momentary_faults: str = "—"
    active_alarm_count: int = 0


def _aware(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    return value if value.tzinfo is not None else value.replace(tzinfo=timezone.utc)


def _num(value: float, decimals: int = 6) -> str:
    """1234.5 -> '1234,5'. Arayuzdeki bicimleyici ile ayni: BINLIK AYRACI YOK.

    Rapor sayilari kopyalanip hesap tablosuna yapistiriliyor; binlik ayraci
    orada sayiyi metne cevirirdi.

    KUYRUK KIRPMA YALNIZCA ONDALIK VARSA: `decimals=0` ile bicimlenen bir
    sayida ondalik nokta YOKTUR, dolayisiyla kosulsuz `rstrip("0")` sayinin
    KENDISINI yer — 40 arizalik bir sayac "4" diye basilirdi ve rakam makul
    gorundugu icin kimse fark etmezdi.
    """
    if not math.isfinite(value):
        return "—"
    text = f"{value:.{decimals}f}"
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    if text in ("", "-", "-0"):
        text = "0"
    return text.replace(".", ",")


def _trust(value: float | None, quality: str | None, gateway_online: bool) -> str:
    """"trusted" | "missing" | "untrusted" — frontend `signalTrust` ile ayni.

    Bir ariza izleme urununde en agir hata sinifi, sistemin BILMEDIGINI
    "sorun yok" diye gostermesidir. Haberlesmesi kopan cihaz icin gateway
    `comm_lost` kalitesiyle 0.0 basiyor; bunu "Normal" yazmak, sunucunun
    kararini raporda gecersiz kilardi.
    """
    if value is None:
        return "missing"
    if not gateway_online:
        return "untrusted"
    q = (quality or "").strip().lower()
    return "untrusted" if q and q in _ENGELLEYEN_KALITELER else "trusted"


def _grup_of(suffix: str, data_type: str | None) -> str:
    """Sinyali kanal tablolarindaki gruba yerlestir — frontend `groupOfSuffix`."""
    s = suffix.lower()
    if re.search(
        r"(overcurrent|delta_i_delta_t|fault_direction|load_flow|_tripped|voltage_loss"
        r"|current_loss|tamper|pick_up|_alarm|permanent_fault$|momentary_fault$"
        r"|fault_current|fault_duration|last_good|minimum_current|minimum_voltage"
        r"|maximum_current|maximum_voltage|trip_level)",
        s,
    ):
        return "protection"
    if data_type == "counter" or s.endswith("_counter"):
        return "counter"
    if data_type == "analog" or re.search(
        r"(current|voltage|temperature|phase_angle|pitch_angle|nominal)", s
    ):
        return "measure"
    return "status"


def _rtu_grup_of(suffix: str) -> str:
    s = suffix.lower()
    for key, _label, pattern in _RTU_GRUPLARI:
        if pattern.search(s):
            return key
    return "other"


def modem_durumu(raw: str | None) -> tuple[float | None, str | None]:
    """Modemin ham NWS yanitindan (dBm, operator) cozer.

    Frontend `modemStatus.modemDurumuCoz` ile AYNI kurallar ve AYNI gerekce:
    okuma KONUMA degil BICIME dayanir (ilk gecerli negatif sayi = alim
    seviyesi; tirnak icindeki ilk RAKAM OLMAYAN alan = operator). Modem
    degisip alan sirasi kayarsa sessizce yanlis bir sayi gostermektense bos
    gostermek dogru.
    """
    text = (raw or "").strip()
    if not text:
        return None, None
    body = re.sub(r"^[A-Za-z^]+\s*:\s*", "", text)
    dbm: float | None = None
    operator: str | None = None
    for part in (p.strip() for p in body.split(",")):
        quoted = re.fullmatch(r'"(.*)"', part)
        if quoted:
            inner = quoted.group(1).strip()
            # "286 01" (MCC/MNC) ve "286016681396681" (IMSI) sayisaldir.
            if operator is None and inner and not re.fullmatch(r"[\d\s]+", inner):
                operator = inner
            continue
        if dbm is None and re.fullmatch(r"-\d+", part):
            n = float(part)
            if -140 <= n <= -30:
                dbm = n
    return dbm, operator


def _firmware_text(raw_str: str | None, raw_num: float | None) -> str | None:
    """Cihaz 2338 gonderir, gercek surum "2.338" (arayuzdeki kural)."""
    if raw_str:
        return raw_str
    if raw_num is None or not math.isfinite(raw_num):
        return None
    return f"{raw_num / 1000:.3f}" if raw_num >= 1000 else _num(raw_num)


def _resolve_kind(device: Device) -> str:
    if device.parent_device_id is not None:
        return "set"
    if is_kit_model(device.model):
        return "kit"
    return "simple"


def _olcum_kanallari(catalog: list[SignalCatalog], model: str | None) -> list[str]:
    """Modelin GERCEKTEN olcum yapan kaynaklari, sirali.

    Iki suzgec birlikte calisir:
      1. `is_stored_signal` — kaynagin telemetrisi bu cihaz kaydinda SAKLANIYOR
         mu. Pole Master Kit'in dokuz uydusu katalogda vardir ama setlere
         yonlendirilir; kit raporunda dokuz bos kanal basmak yanlis olurdu.
      2. `_OLCUM_ISARETI` — kaynakta akim/ariza noktasi var mi. Kitin
         `master`i saklanir ama olcum yapmaz (modem/besleme/GPS tasir).
    """
    by_source: dict[str, set[str]] = {}
    for row in catalog:
        by_source.setdefault(row.source, set()).add(row.key.split(".", 1)[-1])
    result = [
        source
        for source, suffixes in by_source.items()
        if is_stored_signal(model, source) and (suffixes & _OLCUM_ISARETI)
    ]
    # `master` once, uydular numara sirasiyla — ekrandaki kanal listesiyle ayni.
    return sorted(result, key=lambda s: (s != "master", s))


def _latest_map(db: Session, device_ids: list[int]) -> dict[tuple[int, str], TelemetryLatest]:
    """(cihaz, sinyal) -> son deger. ORM entity DEGIL kolon tuple'i cekmek
    burada gerekmiyor: tek cihazin (en fazla iki kaydin) satirlari okunuyor."""
    if not device_ids:
        return {}
    rows = db.scalars(
        select(TelemetryLatest).where(TelemetryLatest.device_id.in_(device_ids))
    ).all()
    return {(r.device_id, r.signal_key): r for r in rows}


def collect_device_report(db: Session, device: Device) -> DeviceReportData:
    """Raporun ihtiyaci olan her seyi TEK yerde toplar.

    Router'in isi yetki + HTTP; belgenin ne icerdigi buranin karari
    (bkz. proje kilavuzu, katman akisi).
    """
    kind = _resolve_kind(device)
    rtu_device = device_kit_service.master_source_device(db, device)

    gateway = None
    if device.gateway_code:
        gateway = db.scalar(select(Gateway).where(Gateway.code == device.gateway_code))
    gateway_online = True
    if gateway is not None:
        last_seen = _aware(gateway.last_seen_at)
        gateway_online = bool(
            gateway.is_active
            and last_seen is not None
            and (datetime.now(timezone.utc) - last_seen).total_seconds() < GATEWAY_LIVE_SEC
        )

    ids = [device.id] + ([rtu_device.id] if rtu_device and rtu_device.id != device.id else [])
    latest = _latest_map(db, ids)

    def own(key: str) -> TelemetryLatest | None:
        return latest.get((device.id, key))

    def rtu(key: str) -> TelemetryLatest | None:
        return latest.get((rtu_device.id, key)) if rtu_device else None

    def rtu_str(key: str) -> str | None:
        row = rtu(key)
        text = (row.value_string or "").strip() if row else ""
        return text or None

    def rtu_num(key: str) -> float | None:
        row = rtu(key)
        return row.value if row and row.value is not None else None

    catalog = list(
        db.scalars(
            select(SignalCatalog)
            .where(SignalCatalog.model == device.model)
            .where(SignalCatalog.is_active.is_(True))
            .order_by(SignalCatalog.display_order.asc(), SignalCatalog.key.asc())
        ).all()
    )

    data = DeviceReportData(
        device=device,
        kind=kind,
        model_label=model_label(device.model, db),
        rtu_device=rtu_device,
        gateway=gateway,
        gateway_online=gateway_online,
        region_name="",
        line_name="",
        pole_span="",
        latitude=None,
        longitude=None,
    )

    _fill_topology(db, device, data)
    _fill_channels(db, device, kind, catalog, latest, data)
    _fill_signal_groups(catalog, device, latest, gateway_online, data)
    _fill_rtu_groups(db, device, rtu_device, latest, data)

    # SEBEKE SINYALI + OPERATOR: sayisal `modem_rssi` noktasi cogu kurulumda
    # HIC gelmiyor; gercek deger modemin ham NWS metninin icinde. Sira
    # arayuzdekiyle ayni ve `0` bir DEGERDIR — `or` kullanilsaydi sifir
    # okuma sessizce metinden cozulene dusulurdu.
    dbm, operator = modem_durumu(rtu_str("master.info_network_rf_status_information"))
    olculen = rtu_num("master.modem_rssi")
    data.network_dbm = olculen if olculen is not None else dbm
    data.network_operator = rtu_str("master.info_network_operator") or operator

    # Sayaclar: hangi kanalda gelirse. SN 2.0'da `master`, sette uydularda;
    # kanali olmayan bir kayitta (kit) `master`a bakilir ve genelde bos doner.
    sayac_kaynaklari = [c.source for c in data.channels] or ["master"]
    for suffix, attr in (
        ("permanent_fault_counter", "permanent_faults"),
        ("momentary_fault_counter", "momentary_faults"),
    ):
        for source in sayac_kaynaklari:
            row = own(f"{source}.{suffix}")
            if row is not None and row.value is not None:
                setattr(data, attr, _num(row.value, 0))
                break

    data.alarms = list(
        db.scalars(
            select(AlarmEvent)
            .where(AlarmEvent.device_id == device.id)
            .where(AlarmEvent.reset.is_(False))
            .where(AlarmEvent.superseded_at.is_(None))
            .order_by(AlarmEvent.created_at.desc())
        ).all()
    )
    data.active_alarm_count = len(data.alarms)

    data.events = list(
        db.scalars(
            select(SystemEvent)
            .where(SystemEvent.device_code == device.code)
            .order_by(SystemEvent.created_at.desc())
            .limit(MAX_EVENTS)
        ).all()
    )

    if kind == "kit":
        _fill_subunits(db, device, data)
    return data


def _fill_topology(db: Session, device: Device, data: DeviceReportData) -> None:
    """Bolge / hat / direk araligi + konum.

    Konum icin ONCE cihazin kendi koordinati: (0, 0) ya da bos ise segmentin
    direkleri kullanilir — sol paneldeki mini haritanin kurali.
    """
    segment = db.scalar(select(LineSegment).where(LineSegment.device_id == device.id))
    if segment is not None:
        line = db.get(Line, segment.line_id)
        if line is not None:
            data.line_name = line.name
            region = db.get(Region, line.region_id)
            if region is not None:
                data.region_name = region.name
        start = db.get(Pole, segment.from_pole_id)
        end = db.get(Pole, segment.to_pole_id)
        if start is not None and end is not None:
            data.pole_span = f"Direk #{start.sequence_no} — Direk #{end.sequence_no}"

    lat, lon = device.latitude, device.longitude
    gecerli = (
        lat is not None
        and lon is not None
        and math.isfinite(lat)
        and math.isfinite(lon)
        and not (lat == 0 and lon == 0)
    )
    if not gecerli and segment is not None:
        start = db.get(Pole, segment.from_pole_id)
        end = db.get(Pole, segment.to_pole_id)
        if start is not None and end is not None:
            lat, lon = (start.latitude + end.latitude) / 2, (start.longitude + end.longitude) / 2
            gecerli = True
    if gecerli:
        data.latitude, data.longitude = lat, lon


def _fill_channels(
    db: Session,
    device: Device,
    kind: str,
    catalog: list[SignalCatalog],
    latest: dict[tuple[int, str], TelemetryLatest],
    data: DeviceReportData,
) -> None:
    """Olcum kanallari serisi: unite, uydu no, seri no, faz, pil, anlik olcum.

    Bu serit HAM olcumu gosterir (guven rozeti yok): kanal basliginin isi
    "hangi unite neyi olcuyor" sorusunu bir bakista yanitlamak. Kalite
    isareti asagidaki sinyal tablolarinda, satir satir duruyor.
    """
    from app.services.fault_snapshot import resolve_source_phase

    sources = _olcum_kanallari(catalog, device.model)
    if not sources:
        return

    satellites = (
        list(resolve_subunit_satellites(device.subunit_index, device.subunit_satellites))
        if kind == "set"
        else []
    )
    phases = resolve_source_phase(db, device_id=device.id) or {}
    rtu_device = data.rtu_device

    def val(key: str) -> TelemetryLatest | None:
        return latest.get((device.id, key))

    def olcum(source: str, suffix: str) -> str:
        row = val(f"{source}.{suffix}")
        if row is None or row.value is None:
            return "—"
        unit = next(
            (s.unit for s in catalog if s.key == f"{source}.{suffix}" and s.unit), None
        )
        text = _num(row.value)
        return f"{text} {unit}" if unit else text

    for index, source in enumerate(sources):
        # --- Seri no: analog nokta (sayi) once, string variant yedek --------
        serial: str | None = None
        row = val(f"{source}.serial_number")
        if row is not None and row.value is not None and row.value > 0:
            serial = str(int(round(row.value)))
        if serial is None:
            row = val(f"{source}.info_serial_number")
            serial = ((row.value_string or "").strip() or None) if row else None

        # --- Pil ------------------------------------------------------------
        battery: float | None = None
        if source == "master":
            # SN 2.0'da cihazin bataryasi master unitenindir ve zaten kayitta
            # hesaplanmis olarak duruyor.
            if device.battery_percent is not None and math.isfinite(device.battery_percent):
                battery = float(device.battery_percent)
        else:
            # SETTE PIL FIZIKSEL UYDUDAN OKUNUR: setin `sat01..03` bolmesi
            # telemetri yonlendirmesinin urettigi SANAL adlardir ve batarya
            # gerilimi orada bos kalabiliyor. Uydunun kendisi kit kaydinda
            # gercek numarasiyla durur.
            voltage: float | None = None
            unit_for_threshold = source
            if index < len(satellites) and rtu_device is not None:
                fiziksel = f"sat{satellites[index]:02d}"
                kit_row = latest.get((rtu_device.id, f"{fiziksel}.battery_voltage_satellite"))
                if kit_row is not None and kit_row.value is not None:
                    voltage, unit_for_threshold = kit_row.value, fiziksel
            if voltage is None:
                own_row = val(f"{source}.battery_voltage_satellite")
                if own_row is not None and own_row.value is not None:
                    voltage = own_row.value
            if voltage is not None:
                low, full = device_profile_service.battery_thresholds(
                    db, device.model, unit=unit_for_threshold
                )
                battery = device_profile_service.voltage_to_percent(voltage, low, full)

        faz = phases.get(source)
        data.channels.append(
            ReportChannel(
                source=source,
                label=source_label(source),
                satellite_no=satellites[index] if index < len(satellites) else None,
                serial=serial,
                battery_percent=battery,
                phase=_FAZ_ETIKET.get((faz or "").lower()) if faz else None,
                current=olcum(source, "actual_current"),
                voltage=olcum(source, "actual_voltage"),
                temperature=(
                    olcum(source, "device_temperature")
                    if val(f"{source}.device_temperature")
                    else olcum(source, "conductor_temperature")
                ),
            )
        )


def _fill_signal_groups(
    catalog: list[SignalCatalog],
    device: Device,
    latest: dict[tuple[int, str], TelemetryLatest],
    gateway_online: bool,
    data: DeviceReportData,
) -> None:
    """Satir = sinyal, sutun = kanal. Bilgi/komut noktalari HARIC.

    VERI GELMEYEN SATIR BASILMAZ. Bir cihazin katalogunda 150'yi askin nokta
    var; hicbiri gelmemis satirlari da basmak, gercekten bilinen degerleri
    "Veri yok" duvarinin icinde gorunmez kilardi (Ariza Raporu ile ayni
    ilke). Elenen nokta SAYISI bolum basliginda yazar — okuyan kisi eksigin
    farkinda olsun.
    """
    if not data.channels:
        return
    sources = [c.source for c in data.channels]
    tipler = {s.key: s.data_type for s in catalog}
    birimler = {s.key: s.unit for s in catalog}
    etiketler = {s.key: s.label for s in catalog}

    # Sonek -> hangi kanallarda tanimli (katalog sirasini koruyarak).
    sonekler: list[str] = []
    gorulen: set[str] = set()
    for row in catalog:
        if row.source not in sources:
            continue
        suffix = row.key.split(".", 1)[-1]
        if row.data_type == "binary_output":
            continue  # komut noktasi -> Komutlar sekmesi; olcum degil
        if _INFO_SUFFIX_RE.search(suffix):
            continue  # IP/seri/firmware -> RTU bolumu
        if suffix in gorulen:
            continue
        gorulen.add(suffix)
        sonekler.append(suffix)

    gizli = 0
    gruplar: dict[str, list[ReportSignalRow]] = {}
    for suffix in sonekler:
        values: dict[str, tuple[str, str | None]] = {}
        dolu = False
        data_type: str | None = None
        for source in sources:
            key = f"{source}.{suffix}"
            if key not in tipler:
                values[source] = ("·", C_MUTED)  # bu kanalda TANIMLI DEGIL
                continue
            data_type = data_type or tipler[key]
            row = latest.get((device.id, key))
            value = row.value if row else None
            quality = row.quality if row else None
            trust = _trust(value, quality, gateway_online)
            if tipler[key] == "string":
                text = ((row.value_string or "").strip() if row else "") or ""
                if text:
                    dolu = True
                values[source] = (text or "Veri yok", None if text else C_MUTED)
                continue
            if trust == "missing":
                # Deger HIC yoksa gosterilecek bir sey de yok.
                values[source] = ("Veri yok", C_MUTED)
                continue
            dolu = True
            bayat = trust == "untrusted"
            if tipler[key] in ("binary", "binary_output"):
                # GUVENILMEZ OLCUM DEGERI GIZLEMEZ, DAMGALAR.
                #
                # Rapor once yalnizca "Guvenilmez" yaziyordu ve SON DEGER
                # kayboluyordu — ekranda da oyleydi ve kullanici bunu
                # duzelttirdi: "canli degerler sayfasinda sinyalin son
                # degerini nasil gorebiliyorsam burada da gorebilmeliyim".
                #
                # Kural yine de korunuyor: bayat bir okuma duz YESIL "Normal"
                # basilmaz (yesil yalan). Deger notr renkte ve "son bilinen"
                # damgasiyla cikar; okuyan hem son durumu gorur hem tazeligini
                # bilir.
                metin = "Aktif" if value == 1 else "Normal"
                if bayat:
                    values[source] = (f"{metin} · son bilinen", C_MUTED)
                else:
                    values[source] = (metin, C_ALARM if value == 1 else C_OK)
                continue
            unit = birimler.get(key)
            text = _num(value, 0) if tipler[key] == "counter" else _num(value)
            text = f"{text} {unit}" if unit else text
            # Analog/sayacta da ayni ilke: sayi durur, tazeligi damgalanir.
            values[source] = (
                (f"{text} · son bilinen", C_MUTED) if bayat else (text, None)
            )
        if not dolu:
            gizli += 1
            continue
        ilk_key = next((f"{s}.{suffix}" for s in sources if f"{s}.{suffix}" in tipler), suffix)
        grup = _grup_of(suffix, data_type)
        gruplar.setdefault(grup, []).append(
            ReportSignalRow(
                suffix=suffix,
                label=signal_label(ilk_key, etiketler.get(ilk_key)),
                values=values,
            )
        )

    # Grup ici sira alfabetik: rapor taranarak okunur (ekranda oldugu gibi
    # tiklanarak degil), aranan sinyalin yerini ad belirler.
    for rows in gruplar.values():
        rows.sort(key=lambda r: r.label.lower())
    data.groups = gruplar
    data.hidden_signal_count = gizli


def _fill_rtu_groups(
    db: Session,
    device: Device,
    rtu_device: Device | None,
    latest: dict[tuple[int, str], TelemetryLatest],
    data: DeviceReportData,
) -> None:
    """RTU / kit seviyesi degerler (modem, GPS, besleme, kimlik).

    KAPSAM "KANAL TABLOLARINA GIRMEYEN HER SEY" olarak tanimlanir, "bilgi
    sinyalleri" diye DEGIL. Fark cihaz turunde ortaya cikiyor:

      * SN 2.0'da `master` bir olcum kanalidir; oradaki akim/ariza noktalari
        yukarida basildi, buraya yalnizca IP/firmware/modem satirlari kalir.
      * Pole Master Kit'te `master` olcum yapmaz: solar/AC besleme, cihaz
        sicakligi ve kurcalama da BURAYA duser. "Yalnizca bilgi sinyalleri"
        deseydik kitin gercek olcumleri raporda HIC gorunmezdi.
    """
    if rtu_device is None:
        return
    kanal_kaynaklari = {c.source for c in data.channels}
    catalog = list(
        db.scalars(
            select(SignalCatalog)
            .where(SignalCatalog.model == rtu_device.model)
            .where(SignalCatalog.source == "master")
            .where(SignalCatalog.is_active.is_(True))
            .order_by(SignalCatalog.display_order.asc(), SignalCatalog.key.asc())
        ).all()
    )
    master_kanal = "master" in kanal_kaynaklari
    gruplar: dict[str, list[tuple[str, str]]] = {}
    eksik = 0
    for row in catalog:
        suffix = row.key.split(".", 1)[-1]
        if row.data_type == "binary_output":
            continue
        if master_kanal and not _INFO_SUFFIX_RE.search(suffix):
            continue  # kanal tablosunda zaten basildi
        live = latest.get((rtu_device.id, row.key))
        if live is None:
            eksik += 1
            continue
        if row.data_type == "string":
            text = (live.value_string or "").strip()
        elif live.value is None:
            text = ""
        elif row.data_type in ("binary", "binary_output"):
            text = "Aktif" if live.value == 1 else "Normal"
        else:
            sayi = _num(live.value, 0) if row.data_type == "counter" else _num(live.value)
            text = f"{sayi} {row.unit}" if row.unit else sayi
        if not text:
            eksik += 1
            continue
        gruplar.setdefault(_rtu_grup_of(suffix), []).append(
            (signal_label(row.key, row.label), text)
        )
    data.rtu_groups = gruplar
    data.rtu_missing_count = eksik


def _fill_subunits(db: Session, kit: Device, data: DeviceReportData) -> None:
    """Kite bagli setler — kit raporunun asil konusu."""
    children = device_kit_service.list_subunits(db, kit.id)
    if not children:
        return
    alarm_counts = dict(
        db.execute(
            select(AlarmEvent.device_id, func.count(AlarmEvent.id))
            .where(AlarmEvent.device_id.in_([c.id for c in children]))
            .where(AlarmEvent.reset.is_(False))
            .where(AlarmEvent.superseded_at.is_(None))
            .group_by(AlarmEvent.device_id)
        ).all()
    )
    for child in children:
        line_name = ""
        segment = db.scalar(select(LineSegment).where(LineSegment.device_id == child.id))
        if segment is not None:
            line = db.get(Line, segment.line_id)
            if line is not None:
                line_name = line.name
        uydular = resolve_subunit_satellites(child.subunit_index, child.subunit_satellites)
        battery = (
            f"%{round(child.battery_percent)}"
            if child.battery_percent is not None and math.isfinite(child.battery_percent)
            else "—"
        )
        data.subunits.append(
            ReportSubunit(
                code=child.code,
                name=child.name,
                satellites=", ".join(f"{n:02d}" for n in uydular) or "—",
                line_name=line_name or "Hatta yerleştirilmemiş",
                battery=battery,
                alarm_count=int(alarm_counts.get(child.id, 0)),
            )
        )


# ===========================================================================
# Belge
# ===========================================================================
def build_device_report_pdf(
    data: DeviceReportData,
    *,
    settings_row: ProjectSettings | None = None,
    map_image: bytes | None = None,
    generated_by: str = "",
) -> bytes:
    """Tek cihazin A4 dikey durum raporu -> PDF bayt dizisi."""
    st = ReportStyles()
    device = data.device
    customer_name = (settings_row.customer_name if settings_row else None) or ""
    project_name = (settings_row.project_name if settings_row else None) or ""
    customer_logo = decode_data_url_image(settings_row.customer_logo if settings_row else None)

    generated = format_report_time(datetime.now(timezone.utc), with_seconds=True)
    footer_bits = [f"Oluşturma: {generated}", f"Cihaz {device.code}"]
    if generated_by:
        footer_bits.append(f"Düzenleyen: {generated_by}")
    ReportCanvas.configure(
        title="Cihaz Durum Raporu",
        subtitle=" · ".join(p for p in (project_name, customer_name) if p),
        footer_left="  ·  ".join(footer_bits),
        customer_logo=customer_logo,
        customer_name=customer_name,
    )

    margin = 12 * mm
    buffer = io.BytesIO()
    doc = SimpleDocTemplate(
        buffer,
        pagesize=A4,
        leftMargin=margin,
        rightMargin=margin,
        # Ustbilgi/altbilgi seritleri canvas'ta ciziliyor; govde onlarla
        # cakismasin diye kenar bosluklari serit yuksekligi kadar buyuk.
        topMargin=margin + HEADER_HEIGHT,
        bottomMargin=margin + FOOTER_HEIGHT,
        title=f"EnerjiOne — Cihaz Durum Raporu {device.code}",
        author="EnerjiOne Grid",
        subject=f"{data.region_name} / {data.line_name}".strip(" /"),
    )

    story: list = [_title_block(st, data), Spacer(1, 8), _summary_strip(st, data)]
    story.extend(_location_section(st, data, map_image))
    story.extend(_identity_section(st, data))
    story.extend(_channels_section(st, data))
    story.extend(_signal_sections(st, data))
    story.extend(_rtu_section(st, data))
    story.extend(_subunits_section(st, data))
    story.extend(_alarms_section(st, data))
    story.extend(_events_section(st, data))
    story.append(Spacer(1, 14))
    story.append(
        Paragraph(
            "Bu rapor, oluşturulduğu anda sistemde kayıtlı SON değerlerden "
            "üretilmiştir; cihazın canlı durumu bu andan sonra değişmiş olabilir. "
            "Haberleşmesi kopmuş bir cihazın gönderdiği son okuma &quot;son "
            "bilinen&quot; olarak damgalanır — değerin kendisi basılır ama ona "
            "dayanarak karar verilmemelidir.",
            st.caption,
        )
    )

    doc.build(story, canvasmaker=ReportCanvas)
    return buffer.getvalue()


def _title_block(st: ReportStyles, data: DeviceReportData) -> Table:
    device = data.device
    crumb = " · ".join(
        part
        for part in (
            data.model_label,
            data.region_name or None,
            data.line_name or None,
            data.pole_span or None,
        )
        if part
    )
    left = [
        Paragraph(f"CİHAZ DURUM RAPORU · {esc(device.code)}", st.eyebrow),
        Paragraph(esc(device.name), st.title),
        Paragraph(esc(crumb), st.crumb),
    ]
    online = _comm_online(data)
    pill = status_pill(
        st,
        "Çevrimiçi" if online else "Çevrimdışı",
        C_OK if online else C_MUTED,
    )
    table = Table([[left, [pill]]], colWidths=[CONTENT_W * 0.66, CONTENT_W * 0.34])
    table.setStyle(
        TableStyle(
            [
                ("VALIGN", (0, 0), (-1, 0), "TOP"),
                # Ic tablonun kendi `hAlign`i bir HUCRE icinde gecerli degil;
                # hizayi hucrenin ALIGN'i belirler.
                ("ALIGN", (1, 0), (1, 0), "RIGHT"),
                ("LINEBELOW", (0, 0), (-1, -1), 1.2, INK),
                ("LEFTPADDING", (0, 0), (-1, -1), 0),
                ("RIGHTPADDING", (0, 0), (-1, -1), 0),
                ("TOPPADDING", (0, 0), (-1, -1), 0),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 8),
            ]
        )
    )
    return table


def _comm_online(data: DeviceReportData) -> bool:
    """Haberlesme durumu — SETTE KITIN durumu.

    Setin kendi DNP3 oturumu yok; hepsi tek fiziksel baglantidan besleniyor
    (bkz. `device_kit_service.annotate`). Setin kendi kolonuna bakmak,
    "set cevrimici ama kiti degil" gibi imkansiz bir durum uretirdi.
    """
    owner = data.rtu_device or data.device
    return str(getattr(owner.communication_status, "value", owner.communication_status)) == "online"


def _summary_strip(st: ReportStyles, data: DeviceReportData) -> Table:
    device = data.device
    owner = data.rtu_device or device
    online = _comm_online(data)
    battery = owner.battery_percent
    cells: list[tuple[str, str, str | None]] = [
        ("Haberleşme", "Çevrimiçi" if online else "Çevrimdışı", C_OK if online else C_MUTED),
        ("Son iletişim", format_report_time(owner.last_update_at) or "—", None),
        (
            "Aktif alarm",
            str(data.active_alarm_count),
            C_ALARM if data.active_alarm_count else C_OK,
        ),
        (
            "Batarya",
            f"%{round(battery)}" if battery is not None and math.isfinite(battery) else "—",
            C_ALARM if battery is not None and battery <= 20 else None,
        ),
        ("Kalıcı arıza sayacı", data.permanent_faults, None),
        ("Geçici arıza sayacı", data.momentary_faults, None),
        (
            "Şebeke sinyali",
            f"{round(data.network_dbm)} dBm" if data.network_dbm is not None else "—",
            None,
        ),
        (
            "Kurulum tarihi",
            device.installation_date.strftime("%d.%m.%Y") if device.installation_date else "—",
            None,
        ),
    ]
    return stat_strip(st, cells)


def _location_section(st: ReportStyles, data: DeviceReportData, map_image: bytes | None) -> list:
    if data.latitude is None or data.longitude is None:
        return []
    coords = f"{data.latitude:.6f}".replace(".", ",") + " / " + f"{data.longitude:.6f}".replace(".", ",")
    if not map_image:
        # HARITA ZORUNLU DEGIL: karo yoksa (cevrimdisi kurulum, indirilmemis
        # alan) ya da cihaz hatta yerlestirilmemisse figur uretilmez.
        # Koordinat tek basina da sahaya gidilebilir bilgidir — bolum
        # basliginda tekrar etmez, tek satirlik izgara olarak durur.
        return block(
            section_head(st, "Konum"),
            kv_grid(st, [("Enlem / Boylam", coords)], columns=1),
        )
    out: list = [
        Spacer(1, 12),
        section_head(st, "Konum", "Enlem / Boylam: " + coords),
        Spacer(1, 6),
    ]
    figure = Image(io.BytesIO(map_image))
    ratio = figure.imageHeight / figure.imageWidth if figure.imageWidth else 0.55
    frame = Table(
        [[Image(io.BytesIO(map_image), width=CONTENT_W, height=CONTENT_W * ratio)]],
        colWidths=[CONTENT_W],
    )
    frame.setStyle(
        TableStyle(
            [
                ("BOX", (0, 0), (-1, -1), 0.6, RULE),
                ("LEFTPADDING", (0, 0), (-1, -1), 0),
                ("RIGHTPADDING", (0, 0), (-1, -1), 0),
                ("TOPPADDING", (0, 0), (-1, -1), 0),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 0),
            ]
        )
    )
    out += [
        frame,
        Spacer(1, 5),
        Paragraph(
            "Cihaz hat üzerindeki yerleşimiyle gösterilmiştir; numaralar hat sıra "
            "numaralarıdır. Konum cihaz kaydından, yoksa oturduğu kesimin "
            "direklerinden türetilir.",
            st.caption,
        ),
    ]
    return out


def _identity_section(st: ReportStyles, data: DeviceReportData) -> list:
    """Cihaz kunyesi + baglanti. Tur farki BURADA en gorunur olan sey."""
    device = data.device
    pairs: list[tuple[str, str]] = [
        ("Cihaz kodu", device.code),
        ("Cihaz adı", device.name),
        ("Model", data.model_label),
    ]
    if device.serial_number:
        pairs.append(("Seri numarası", device.serial_number))
    if device.installation_date:
        pairs.append(("Kurulum tarihi", device.installation_date.strftime("%d.%m.%Y")))
    if device.description:
        pairs.append(("Açıklama", device.description))

    if data.kind == "set" and data.rtu_device is not None:
        # Set tek basina bir sey ifade etmiyor: hangi kitin parcasi oldugu ve
        # hangi FIZIKSEL uydulara bagli oldugu belgeye girmezse, sahadaki
        # ekip yanlis kelepceyi acar.
        uydular = resolve_subunit_satellites(device.subunit_index, device.subunit_satellites)
        pairs += [
            ("Bağlı olduğu kit", f"{data.rtu_device.name} ({data.rtu_device.code})"),
            ("Set sırası", str(device.subunit_index or "—")),
            ("Fiziksel uydular", ", ".join(f"Satellite {n:02d}" for n in uydular) or "—"),
        ]
    elif data.kind == "kit":
        pairs.append(("Bağlı set sayısı", str(len(data.subunits))))

    baglanti: list[tuple[str, str]] = []
    if data.kind == "set":
        # Setin baglanti alanlari kitten DEVRALINIR; kendi oturumu yoktur.
        baglanti.append(("Bağlantı", "Kit üzerinden (setin kendi DNP3 oturumu yok)"))
    baglanti += [
        ("Gateway", device.gateway_code or "—"),
        ("IP adresi", device.ip_address or "—"),
        ("DNP3 port / adres", f"{device.dnp3_outstation_port} / {device.dnp3_address}"),
        ("Sorgu aralığı", f"{device.poll_interval_sec} sn"),
        ("Zaman aşımı / deneme", f"{device.timeout_ms} ms / {device.retry_count}"),
        ("Sinyal profili", device.signal_profile or "—"),
        (
            "IEC 104 ortak adresi",
            str(device.iec104_common_address) if device.iec104_common_address else "Hedef varsayılanı",
        ),
    ]

    out = block(
        section_head(st, "Cihaz Künyesi", _KIND_HINT.get(data.kind, "")),
        kv_grid(st, pairs),
    )
    out += block(
        section_head(
            st,
            "Bağlantı ve Ağ",
            "Gateway son görülme: "
            + (format_report_time(data.gateway.last_seen_at) if data.gateway else "—"),
        ),
        kv_grid(st, baglanti),
    )
    return out


#: Tur aciklamasi — raporu okuyan kisi "bu kayit tam olarak ne" bilsin.
_KIND_HINT = {
    "simple": "Kendi RTU'su ve ölçüm üniteleri olan saha cihazı",
    "kit": "Fiziksel kit kaydı — ölçüm setleri ayrı kayıtlardır",
    "set": "Kit üzerindeki sanal set — haberleşme kite aittir",
}


def _channels_section(st: ReportStyles, data: DeviceReportData) -> list:
    if not data.channels:
        return []
    setli = any(c.satellite_no is not None for c in data.channels)
    headers = ["Ünite"]
    widths = [0.16]
    if setli:
        headers.append("Fiziksel uydu")
        widths.append(0.14)
    headers += ["Seri No", "Faz", "Batarya", "Akım", "Gerilim", "Sıcaklık"]
    widths += (
        [0.16, 0.07, 0.12, 0.12, 0.12, 0.11]
        if setli
        else [0.18, 0.10, 0.14, 0.14, 0.14, 0.14]
    )

    rows: list[list] = []
    for channel in data.channels:
        row = [Paragraph(esc(channel.label), st.cell_bold)]
        if setli:
            row.append(
                Paragraph(
                    f"Satellite {channel.satellite_no:02d}" if channel.satellite_no else "—",
                    st.cell,
                )
            )
        battery = (
            f"%{round(channel.battery_percent)}" if channel.battery_percent is not None else "—"
        )
        battery_style = (
            ParagraphStyle("battLow", parent=st.cell_bold, textColor=colors.HexColor(C_ALARM))
            if channel.battery_percent is not None and channel.battery_percent <= 20
            else st.cell
        )
        row += [
            Paragraph(esc(channel.serial or "—"), st.cell),
            Paragraph(esc(channel.phase or "—"), st.cell_center),
            Paragraph(battery, battery_style),
            Paragraph(esc(channel.current), st.cell_right),
            Paragraph(esc(channel.voltage), st.cell_right),
            Paragraph(esc(channel.temperature), st.cell_right),
        ]
        rows.append(row)

    hint = (
        "Her ünite ayrı bir faza kelepçelenir"
        if data.kind != "set"
        else "Setin üç ünitesi de uydudur; kit RTU'su ölçüm yapmaz"
    )
    return block(
        section_head(st, "Ölçüm Kanalları", hint),
        data_table(st, headers, rows, [CONTENT_W * w for w in widths]),
    )


def _signal_sections(st: ReportStyles, data: DeviceReportData) -> list:
    """Kanal kanal sinyal tablolari — raporun govdesi."""
    if not data.groups or not data.channels:
        return []
    sources = [c.source for c in data.channels]
    label_w = 0.34
    value_w = (1 - label_w) / len(sources)
    widths = [CONTENT_W * label_w] + [CONTENT_W * value_w] * len(sources)
    headers = ["Sinyal"] + [c.label for c in data.channels]

    out: list = []
    ilk = True
    for key, title in _GRUP_SIRASI:
        rows_data = data.groups.get(key)
        if not rows_data:
            continue
        table_rows: list[list] = []
        for row in rows_data:
            cells = [Paragraph(esc(row.label), st.cell)]
            for source in sources:
                text, color = row.values.get(source, ("·", C_MUTED))
                style = st.cell_center
                if color:
                    style = ParagraphStyle(
                        f"c{color}", parent=st.cell_center, textColor=colors.HexColor(color)
                    )
                cells.append(Paragraph(esc(text), style))
            table_rows.append(cells)
        hint = ""
        if ilk:
            hint = "Sütunlar cihazın ölçüm üniteleridir"
            if data.hidden_signal_count:
                hint += f" · {data.hidden_signal_count} nokta veri gelmediği için listelenmedi"
            ilk = False
        out += block(
            section_head(st, title, hint),
            data_table(st, headers, table_rows, widths),
        )
    if out:
        out.append(Spacer(1, 4))
        out.append(
            Paragraph(
                "Boş hücre (&quot;·&quot;) o noktanın ilgili ünitede tanımlı "
                "olmadığını, &quot;Veri yok&quot; hiç telemetri gelmediğini gösterir. "
                "&quot;son bilinen&quot; damgalı değerler cihazdan gelmiştir ama "
                "haberleşme kalitesi nedeniyle güncel olmayabilir — okunur, karar "
                "dayanağı yapılmaz.",
                st.caption,
            )
        )
    return out


def _rtu_section(st: ReportStyles, data: DeviceReportData) -> list:
    if not data.rtu_groups or data.rtu_device is None:
        return []
    if data.kind == "set":
        title = "Pole Master (Kit Seviyesi)"
        hint = f"{data.rtu_device.code} kitine ait, tüm setlerde ortak"
    elif data.kind == "kit":
        title = "Kit Ölçümleri ve Haberleşme"
        hint = "Kit tek DNP3 outstation'dır; bu değerler tüm setler için ortaktır"
    else:
        title = "RTU ve Haberleşme"
        hint = "Cihazın ana ünitesinden okunan altyapı bilgileri"
    if data.rtu_missing_count:
        hint += f" · {data.rtu_missing_count} nokta boş"

    blocks: list = []
    for key, group_title, _pattern in _RTU_GRUPLARI + (("other", "Diğer", re.compile("")),):
        pairs = data.rtu_groups.get(key)
        if not pairs:
            continue
        if blocks:
            blocks.append(Spacer(1, 8))
        # Alt baslik izgarasindan AYRILMASIN: "KIMLIK VE KONUM" sayfanin
        # dibinde tek basina kaldiginda, sonraki sayfadaki satirlarin neye
        # ait oldugu belirsiz kaliyordu.
        blocks.append(
            KeepTogether(
                [Paragraph(upper_tr(group_title), st.label), Spacer(1, 3), kv_grid(st, pairs)]
            )
        )
    if not blocks:
        return []
    return block(section_head(st, title, hint), blocks[0], *blocks[1:])


def _subunits_section(st: ReportStyles, data: DeviceReportData) -> list:
    if not data.subunits:
        return []
    rows = []
    for unit in data.subunits:
        rows.append(
            [
                Paragraph(esc(unit.name), st.cell_bold),
                Paragraph(esc(unit.code), st.cell),
                Paragraph(esc(unit.satellites), st.cell),
                Paragraph(esc(unit.line_name), st.cell),
                Paragraph(esc(unit.battery), st.cell_right),
                Paragraph(
                    str(unit.alarm_count) if unit.alarm_count else "—",
                    ParagraphStyle(
                        "subAlarm",
                        parent=st.cell_center,
                        textColor=colors.HexColor(C_ALARM if unit.alarm_count else C_MUTED),
                    ),
                ),
            ]
        )
    widths = [CONTENT_W * w for w in (0.26, 0.18, 0.14, 0.24, 0.09, 0.09)]
    return block(
        section_head(
            st,
            "Bağlı Setler",
            "Her set sahada ayrı bir noktada; ölçümleri kendi raporundadır",
        ),
        data_table(
            st,
            ["Set", "Kod", "Uydular", "Hat", "Batarya", "Alarm"],
            rows,
            widths,
        ),
    )


def _alarms_section(st: ReportStyles, data: DeviceReportData) -> list:
    if not data.alarms:
        return []
    rows = []
    for alarm in data.alarms:
        kaynak = alarm.signal_key.split(".", 1)[0] if alarm.signal_key else ""
        rows.append(
            [
                Paragraph(esc(alarm.title), st.cell_bold),
                Paragraph(esc((alarm.level or "").upper()), st.cell),
                Paragraph(esc(source_label(kaynak) if kaynak else "—"), st.cell),
                Paragraph(format_report_time(alarm.created_at), st.cell),
                Paragraph("Onaylandı" if alarm.acknowledged else "Bekliyor", st.cell),
            ]
        )
    widths = [CONTENT_W * w for w in (0.36, 0.12, 0.16, 0.22, 0.14)]
    return block(
        section_head(
            st,
            "Aktif Alarmlar",
            "Normale dönmemiş kayıtlar",
        ),
        data_table(st, ["Alarm", "Seviye", "Ünite", "Zaman", "Onay"], rows, widths),
    )


def _events_section(st: ReportStyles, data: DeviceReportData) -> list:
    if not data.events:
        return []
    rows = []
    for item in data.events:
        rows.append(
            [
                Paragraph(format_report_time(item.created_at), st.cell),
                Paragraph(esc(event_labels.category_label(item.category)), st.cell),
                Paragraph(
                    esc(event_labels.format_message(item.message, item.metadata_json)),
                    st.cell,
                ),
                Paragraph(esc(event_labels.status_label(item.event_type)), st.cell),
                Paragraph(esc(item.actor_username or "Sistem"), st.cell),
            ]
        )
    widths = [CONTENT_W * w for w in (0.16, 0.14, 0.40, 0.16, 0.14)]
    return block(
        section_head(st, "Son Olaylar", f"En yeni {len(rows)} kayıt"),
        data_table(st, ["Tarih", "Kategori", "Olay", "Durum", "Kullanıcı"], rows, widths),
    )


__all__ = [
    "DeviceReportData",
    "build_device_report_pdf",
    "collect_device_report",
    "modem_durumu",
]
