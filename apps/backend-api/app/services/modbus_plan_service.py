"""Modbus TCP outbound adres plani — TEK DOGRULUK KAYNAGI.

Hem web arayuzundeki adres tablosu/CSV disa aktarim, hem de `modbus-outbound`
worker'i bu modulun urettigi plani kullanir. Boylece SCADA'ya verilen adres
listesi ile sunucunun gercekte yayinladigi adres HER ZAMAN ayni olur; iki
yerde ayri ayri hesaplansaydi zamanla kacinilmaz olarak birbirinden ayrilirdi.

Adresleme iki modda calisir (kullanici hedef bazinda secer):

  block (1. yol) : Tek unit id. Cihazlar register/bit bloklarina dagilir.
                   adres = base_address + slot_index * stride + signal_offset
                   stride=100 ile 65536/100 = 655 cihaz sigar ve cihaz basina
                   tum register'lar TEK okumada alinir (FC3/FC4 istek basina
                   125 register siniri).

  unit (2. yol)  : Her cihaz kendi unit (slave) id'sinde, hepsi AYNI offset
                   duzenini kullanir (0'dan baslar). SCADA'da her cihaz ayri
                   slave gibi gorunur; adres tablosu tek sayfa. Port basina
                   247 cihaz (Modbus unit id araligi).

Veri alanlari (Modbus'ta 4 ayri adres uzayi vardir):
  analog + counter -> holding register (FC3) VE input register (FC4), ayni
                      icerik iki alanda birden yayinlanir. Sebep: SCADA'lar
                      hangisini okuyacagi konusunda ikiye bolunur; ayna
                      yayin entegrasyonda en sik yasanan "yanlis alan"
                      sorununu bastan kaldirir.
  binary           -> discrete input (FC2)
  binary_output    -> coil (FC1), SALT OKUNUR (yazma reddedilir)
  string           -> Modbus'a dahil EDILMEZ (protokol string tasimaya uygun
                      degil; bu sinyaller REST/MQTT kanallarindan alinir).

Sinyal offset'leri tabloya yazilmaz — katalog siralamasindan deterministik
uretilir. `signal_catalog.modbus_function` + `modbus_address` dolu ise o
sinyal icin MANUEL OVERRIDE kabul edilir (Sinyaller sayfasindan girilir;
mevcut bir SCADA adres planina uyum gerektiginde de kullanilir).

SISTEM BLOGU (0..99)
--------------------
`base_address` varsayilani 100'dur; ilk 100 adres (0..99) CIHAZ VERISI ICIN
KULLANILMAZ, sistemin kendi metrikleri icin (CPU/RAM/disk, haberlesme
durumu, servis sagligi) rezerve edilir. Boylece cihaz bloklari 100'un
katlarindan baslar ve adres okumak kolaydir:

    cihaz 1 -> 100..199     cihaz 2 -> 200..299     cihaz 3 -> 300..399

Sinyaller sayfasindan bir sinyale offset 50 verildiyse o sinyal 1. cihazda
150, 2. cihazda 250, 3. cihazda 350 adresinden yayinlanir.

NOT: Rezerve blok su an BOS — sistem metriklerini yayinlayan taraf henuz
yazilmadi (bkz. apps/modbus-outbound). Blok bilerek bos birakildi ki
adresleme duzeni sonradan degismesin.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.device import Device
from app.models.outbound_target import OutboundModbusSlot, OutboundTarget
from app.models.signal_catalog import SignalCatalog

logger = logging.getLogger(__name__)

# Modbus fonksiyon kodlari (veri alani secimi)
FC_COIL = 1
FC_DISCRETE_INPUT = 2
FC_HOLDING_REGISTER = 3
FC_INPUT_REGISTER = 4

# Modbus adres uzayi: 16-bit -> 0..65535 (her alan icin ayri).
MODBUS_ADDRESS_SPACE = 65_536
# FC3/FC4 tek istekte en fazla 125 register, FC1/FC2 en fazla 2000 bit okur.
MAX_REGISTERS_PER_READ = 125
MAX_BITS_PER_READ = 2000
# Modbus unit (slave) id araligi. 0 = broadcast, 248-255 rezerve.
MIN_UNIT_ID = 1
MAX_UNIT_ID = 247

# Otomatik blok boyutlari — cihaz basina gercek ihtiyacin ustune pay birakir.
# int16'da 100 secilmesinin ozel sebebi: 125'lik tek-okuma sinirinin altinda
# kalir, yani bir cihazin tum analoglari tek Modbus istegiyle okunur.
AUTO_STRIDE_INT16 = 100
AUTO_STRIDE_FLOAT32 = 200
# Bit alanlarinda (coil/discrete) cihaz basina ayrilan bit blogu.
BIT_STRIDE = 100

# Sistem metrikleri icin rezerve edilen ilk blok. Cihaz slotlari bu adresten
# SONRA baslar (varsayilan base_address). 0..SYSTEM_BLOCK_SIZE-1 arasi cihaz
# verisi ICIN KULLANILMAZ.
SYSTEM_BLOCK_SIZE = 100
DEFAULT_BASE_ADDRESS = SYSTEM_BLOCK_SIZE

VALUE_FORMATS = ("int16", "float32")
MODES = ("block", "unit")
WORD_ORDERS = ("big", "little")

# Modbus'a dahil edilen sinyal tipleri. 'string' bilerek disarida.
_REGISTER_TYPES = ("analog", "counter")
_BIT_TYPES = ("binary", "binary_output")


@dataclass(frozen=True)
class SignalSlot:
    """Bir sinyalin CIHAZ BLOGU ICINDEKI goreli yeri (mutlak adres degil)."""

    key: str
    label: str
    source: str
    data_type: str
    unit: str | None
    # 1=coil, 2=discrete input, 3=holding register (ayrica 4'te de yayinlanir)
    function: int
    # Register alanlarinda word offset, bit alanlarinda bit offset.
    offset: int
    # Kac word kapliyor (bit alanlarinda 0).
    word_count: int
    scale: float
    offset_value: float
    # Manuel override ile mi geldi (katalogdaki modbus_address dolu mu)?
    manual: bool = False


@dataclass
class LayoutSummary:
    """Cihaz basina yer ihtiyaci — UI'da kapasite bilgisi olarak gosterilir."""

    register_words: int = 0
    discrete_bits: int = 0
    coil_bits: int = 0
    analog_count: int = 0
    counter_count: int = 0
    binary_count: int = 0
    binary_output_count: int = 0
    excluded_string_count: int = 0


@dataclass
class SignalLayout:
    """Bir cihaz modelinin Modbus yerlesimi (tum cihazlarda ayni)."""

    value_format: str
    slots: list[SignalSlot] = field(default_factory=list)
    summary: LayoutSummary = field(default_factory=LayoutSummary)


def _analog_word_count(value_format: str) -> int:
    return 2 if value_format == "float32" else 1


def build_signal_layout(
    signals: list[SignalCatalog] | list[dict],
    *,
    value_format: str = "int16",
) -> SignalLayout:
    """Katalogdan cihaz-ici offset duzenini uret (deterministik).

    Siralama: (source, display_order, key). Ayni girdi her zaman ayni cikti —
    kullanici bir sinyali pasiflestirip tekrar aktiflestirse bile adres
    tablosunun kaymamasi icin tek kural: sadece AKTIF sinyaller yerlestirilir
    ve sira degismez.
    """
    fmt = value_format if value_format in VALUE_FORMATS else "int16"
    rows = [_as_signal_dict(s) for s in signals]
    rows = [r for r in rows if r["is_active"]]
    rows.sort(key=lambda r: (r["source"] or "", r["display_order"], r["key"]))

    layout = SignalLayout(value_format=fmt)
    analog_words = _analog_word_count(fmt)

    reg_cursor = 0
    di_cursor = 0
    coil_cursor = 0

    for row in rows:
        data_type = (row["data_type"] or "").lower()

        if data_type in _REGISTER_TYPES:
            # Counter'lar 32-bit sayaclardir; int16 formatinda bile 2 word alir
            # (bir sayaci 16 bit'e sigdirmak 65535'te sarmasi demek olurdu).
            words = 2 if data_type == "counter" else analog_words
            manual_fc = row["modbus_function"]
            manual_addr = row["modbus_address"]
            if manual_addr is not None:
                offset = int(manual_addr)
                function = int(manual_fc) if manual_fc else FC_HOLDING_REGISTER
                manual = True
            else:
                offset = reg_cursor
                function = FC_HOLDING_REGISTER
                reg_cursor += words
                manual = False
            layout.slots.append(
                SignalSlot(
                    key=row["key"], label=row["label"], source=row["source"],
                    data_type=data_type, unit=row["unit"],
                    function=function, offset=offset, word_count=words,
                    scale=row["scale"], offset_value=row["offset"], manual=manual,
                )
            )
            if data_type == "counter":
                layout.summary.counter_count += 1
            else:
                layout.summary.analog_count += 1

        elif data_type in _BIT_TYPES:
            is_output = data_type == "binary_output"
            manual_fc = row["modbus_function"]
            manual_addr = row["modbus_address"]
            if manual_addr is not None:
                offset = int(manual_addr)
                function = int(manual_fc) if manual_fc else (
                    FC_COIL if is_output else FC_DISCRETE_INPUT
                )
                manual = True
            elif is_output:
                offset = coil_cursor
                function = FC_COIL
                coil_cursor += 1
                manual = False
            else:
                offset = di_cursor
                function = FC_DISCRETE_INPUT
                di_cursor += 1
                manual = False
            layout.slots.append(
                SignalSlot(
                    key=row["key"], label=row["label"], source=row["source"],
                    data_type=data_type, unit=row["unit"],
                    function=function, offset=offset, word_count=0,
                    scale=row["scale"], offset_value=row["offset"], manual=manual,
                )
            )
            if is_output:
                layout.summary.binary_output_count += 1
            else:
                layout.summary.binary_count += 1

        elif data_type == "string":
            layout.summary.excluded_string_count += 1

    # Ozet: manuel override'lar imlecin otesine tasabilir; gercek ihtiyac
    # yerlesen en yuksek offset'e gore hesaplanir.
    layout.summary.register_words = max(
        [reg_cursor]
        + [s.offset + s.word_count for s in layout.slots if s.function in (FC_HOLDING_REGISTER, FC_INPUT_REGISTER)]
    )
    layout.summary.discrete_bits = max(
        [di_cursor] + [s.offset + 1 for s in layout.slots if s.function == FC_DISCRETE_INPUT]
    )
    layout.summary.coil_bits = max(
        [coil_cursor] + [s.offset + 1 for s in layout.slots if s.function == FC_COIL]
    )
    return layout


def _as_signal_dict(s) -> dict:
    """SignalCatalog ORM nesnesi veya dict -> tek tip dict."""
    if isinstance(s, dict):
        get = s.get
    else:
        get = lambda k, d=None: getattr(s, k, d)  # noqa: E731
    return {
        "key": str(get("key") or ""),
        "label": str(get("label") or get("key") or ""),
        "source": str(get("source") or "master"),
        "data_type": str(get("data_type") or "analog"),
        "unit": get("unit"),
        "display_order": int(get("display_order") or 0),
        "scale": float(get("scale") if get("scale") is not None else 1.0),
        "offset": float(get("offset") if get("offset") is not None else 0.0),
        "is_active": bool(get("is_active", True)),
        "modbus_function": get("modbus_function"),
        "modbus_address": get("modbus_address"),
    }


def resolve_stride(target: OutboundTarget | dict, layout: SignalLayout) -> int:
    """Cihaz basina register blogu. Elle verilmisse o, degilse otomatik."""
    manual = _tget(target, "modbus_block_stride")
    if manual:
        return int(manual)
    fmt = str(_tget(target, "modbus_value_format") or "int16")
    auto = AUTO_STRIDE_FLOAT32 if fmt == "float32" else AUTO_STRIDE_INT16
    # Gercek ihtiyac otomatik degeri asiyorsa (ozel katalog / manuel
    # override'lar) bloklarin cakismamasi icin buyut.
    return max(auto, layout.summary.register_words)


def _tget(target, key: str, default=None):
    if isinstance(target, dict):
        return target.get(key, default)
    return getattr(target, key, default)


@dataclass(frozen=True)
class DeviceSlotPlan:
    device_id: int
    device_code: str
    device_name: str
    slot_index: int
    unit_id: int
    block_start: int


@dataclass
class CapacityInfo:
    mode: str
    stride: int
    max_devices: int
    device_count: int
    remaining: int
    # Cihaz basina register blogu tek Modbus okumasina sigiyor mu?
    single_read_per_device: bool
    limit_reason: str
    # Kapasiteye SIGMAYAN cihazlar.
    #
    # NEDEN AYRI ALAN: plan kurulurken tavanı asan cihazlar `continue` ile
    # SESSIZCE atlaniyordu. `remaining` de 0 gorundugu icin arayuz durumu
    # "tam" diye okuyor, "273 cihaz plana ALINMADI" bilgisi hicbir yerde
    # gorunmuyordu. float32 modunda tavan 327 cihaz; 600 cihazlik bir hedefte
    # 273 cihaz SCADA'ya hic yayinlanmiyor ve bunu kimse fark etmiyor.
    #
    # Operator yuku birden fazla hedefe bolerek bu tavani asabilir (her hedef
    # kendi adres alanina sahiptir); ama bunu YAPMASI GEREKTIGINI once
    # gormesi lazim.
    dropped_device_count: int = 0
    dropped_device_codes: tuple[str, ...] = ()


def _capacity(mode: str, stride: int, device_count: int, layout: SignalLayout) -> CapacityInfo:
    if mode == "unit":
        max_devices = MAX_UNIT_ID - MIN_UNIT_ID + 1
        reason = "unit_id_range"
    else:
        max_devices = MODBUS_ADDRESS_SPACE // max(1, stride)
        reason = "address_space"
        # Bit alani da sinirlayabilir (cihaz basina 100 bit blok).
        bit_max = MODBUS_ADDRESS_SPACE // BIT_STRIDE
        if bit_max < max_devices:
            max_devices = bit_max
            reason = "bit_space"
    return CapacityInfo(
        mode=mode,
        stride=stride,
        max_devices=max_devices,
        device_count=device_count,
        remaining=max(0, max_devices - device_count),
        single_read_per_device=layout.summary.register_words <= MAX_REGISTERS_PER_READ,
        limit_reason=reason,
    )


def ensure_slots(
    db: Session,
    target: OutboundTarget,
    devices: list[Device],
    *,
    layout: SignalLayout,
    commit: bool = True,
) -> tuple[list[DeviceSlotPlan], CapacityInfo]:
    """Cihazlara slot (unit id / blok baslangici) ata; mevcutlari KORU.

    Yeni cihaz eklendiginde en kucuk BOS slot verilir — silinen cihazlarin
    yeri boylece geri kazanilir ama mevcut cihazlarin adresi asla degismez.
    Kapasite dolduysa fazla cihazlar plan disinda kalir — sessizce yanlis
    adrese yazmaktansa yayinlamamak dogrudur. Ama BU DURUM GORUNUR OLMALI:
    eskiden yalnizca `continue` vardi, `remaining` de 0 gorundugu icin arayuz
    durumu "tam" diye okuyordu ve "N cihaz plana ALINMADI" bilgisi hicbir
    yerde yoktu. Artik `CapacityInfo.dropped_device_count/_codes` doluyor ve
    tasma ERROR seviyesinde loglaniyor.

    Cozum tavani buyutmek degil: her Modbus hedefi kendi adres alanina
    sahiptir, yani yuk ikinci bir hedefe bolunerek tasinir. Operatorun bunu
    yapmasi gerektigini gormesi icin bu gorunurluk sart.
    """
    mode = str(target.modbus_mode or "block")
    mode = mode if mode in MODES else "block"
    stride = resolve_stride(target, layout)
    base = int(target.modbus_base_address or 0)
    default_unit = int(target.modbus_unit_id or 1)

    existing = {
        s.device_id: s
        for s in db.scalars(
            select(OutboundModbusSlot).where(OutboundModbusSlot.target_id == target.id)
        ).all()
    }

    device_ids = {d.id for d in devices}
    # Silinmis/kapsam disi cihazlarin slotlarini birak (yer acilsin).
    for device_id, slot in list(existing.items()):
        if device_id not in device_ids:
            db.delete(slot)
            existing.pop(device_id, None)

    used_slots = {s.slot_index for s in existing.values()}
    capacity = _capacity(mode, stride, len(devices), layout)

    plans: list[DeviceSlotPlan] = []
    dusen: list[str] = []   # kapasiteye sigmayan cihaz kodlari
    next_free = 0
    # Deterministik sira: cihaz kodu. Yeni cihazlar bu sirayla slot alir.
    for device in sorted(devices, key=lambda d: d.code):
        slot = existing.get(device.id)
        if slot is None:
            while next_free in used_slots:
                next_free += 1
            if next_free >= capacity.max_devices:
                # Kapasite doldu — bu cihaz plana alinmaz. SESSIZ GECME:
                # sayilir, loglanir ve API yanitinda dondurulur (bkz.
                # CapacityInfo.dropped_device_codes).
                dusen.append(device.code)
                continue
            slot_index = next_free
            used_slots.add(slot_index)
            slot = OutboundModbusSlot(
                target_id=target.id,
                device_id=device.id,
                slot_index=slot_index,
            )
            db.add(slot)
            existing[device.id] = slot
        # Mod degisirse (block <-> unit) adresler yeniden turetilir ama
        # slot_index sabit kalir; yani cihazin sirasi korunur.
        if mode == "unit":
            slot.unit_id = MIN_UNIT_ID + slot.slot_index
            slot.block_start = 0
        else:
            slot.unit_id = default_unit
            slot.block_start = base + slot.slot_index * stride
        plans.append(
            DeviceSlotPlan(
                device_id=device.id,
                device_code=device.code,
                device_name=device.name,
                slot_index=slot.slot_index,
                unit_id=slot.unit_id,
                block_start=slot.block_start,
            )
        )

    if commit:
        db.commit()
    else:
        db.flush()

    plans.sort(key=lambda p: p.slot_index)
    capacity.device_count = len(plans)
    capacity.remaining = max(0, capacity.max_devices - len(plans))
    capacity.dropped_device_count = len(dusen)
    # Tamamini tasimak 273 kodluk bir yanit demek olabilir; ilk 20 teshis icin
    # yeter, sayi zaten tam.
    capacity.dropped_device_codes = tuple(sorted(dusen)[:20])
    if dusen:
        logger.error(
            "modbus_plan_kapasite_asildi target=%s mode=%s stride=%d "
            "tavan=%d plana_alinan=%d DUSEN=%d ornekler=%s — bu cihazlar "
            "SCADA'ya hic yayinlanmiyor; yuku ikinci bir hedefe bolun",
            _tget(target, "name", "?"),
            mode,
            stride,
            capacity.max_devices,
            len(plans),
            len(dusen),
            ",".join(sorted(dusen)[:10]),
        )
    return plans, capacity


@dataclass(frozen=True)
class PlanPoint:
    """Adres tablosunun tek satiri — SCADA'ya verilecek nihai adres."""

    device_code: str
    device_name: str
    signal_key: str
    label: str
    source: str
    data_type: str
    unit: str | None
    unit_id: int
    function: int
    address: int
    word_count: int
    scale: float
    offset: float
    manual: bool


def build_plan_points(
    *, slots: list[DeviceSlotPlan], layout: SignalLayout
) -> list[PlanPoint]:
    """Cihaz slotlari x sinyal duzeni -> mutlak adres tablosu."""
    points: list[PlanPoint] = []
    for slot in slots:
        for sig in layout.slots:
            if sig.function in (FC_HOLDING_REGISTER, FC_INPUT_REGISTER):
                address = slot.block_start + sig.offset
            else:
                # Bit alanlarinda blok boyutu register'dan bagimsizdir.
                address = (slot.slot_index * BIT_STRIDE) + sig.offset
            points.append(
                PlanPoint(
                    device_code=slot.device_code,
                    device_name=slot.device_name,
                    signal_key=sig.key,
                    label=sig.label,
                    source=sig.source,
                    data_type=sig.data_type,
                    unit=sig.unit,
                    unit_id=slot.unit_id,
                    function=sig.function,
                    address=address,
                    word_count=sig.word_count,
                    scale=sig.scale,
                    offset=sig.offset_value,
                    manual=sig.manual,
                )
            )
    return points


def load_plan(
    db: Session, target: OutboundTarget, *, commit: bool = True
) -> tuple[SignalLayout, list[DeviceSlotPlan], list[PlanPoint], CapacityInfo]:
    """Bir Modbus hedefi icin tam plan (layout + slotlar + adres tablosu)."""
    signals = list(
        db.scalars(
            select(SignalCatalog).where(SignalCatalog.is_active.is_(True))
        ).all()
    )
    devices = list(db.scalars(select(Device).order_by(Device.code.asc())).all())
    layout = build_signal_layout(
        signals, value_format=str(target.modbus_value_format or "int16")
    )
    slots, capacity = ensure_slots(db, target, devices, layout=layout, commit=commit)
    points = build_plan_points(slots=slots, layout=layout)
    return layout, slots, points, capacity


DEFAULT_MODBUS_PORT = 502


def serialize_plan(db: Session, target: OutboundTarget, *, commit: bool = True) -> dict:
    """Plani API/worker sozlesmesi formatinda dondur (ModbusPlanRead ile ayni).

    Hem `/outbound-targets/{id}/modbus-plan` hem `/internal/modbus-plans` bunu
    kullanir — UI'daki adres tablosu ile worker'in yayinladigi adres birebir
    ayni olsun diye.
    """
    layout, slots, points, capacity = load_plan(db, target, commit=commit)
    peers_raw = (target.modbus_allowed_peers or "").strip()
    return {
        "target_id": target.id,
        "target_name": target.name,
        "mode": str(target.modbus_mode or "block"),
        "value_format": str(target.modbus_value_format or "int16"),
        "word_order": str(target.modbus_word_order or "big"),
        "unit_id": int(target.modbus_unit_id or 1),
        "base_address": int(target.modbus_base_address or 0),
        "stride": capacity.stride,
        "listen_host": str(target.listen_host or "0.0.0.0"),
        "listen_port": int(target.listen_port or DEFAULT_MODBUS_PORT),
        "is_active": bool(target.is_active),
        "allowed_peers": [p.strip() for p in peers_raw.split(",") if p.strip()],
        "summary": {
            "register_words": layout.summary.register_words,
            "discrete_bits": layout.summary.discrete_bits,
            "coil_bits": layout.summary.coil_bits,
            "analog_count": layout.summary.analog_count,
            "counter_count": layout.summary.counter_count,
            "binary_count": layout.summary.binary_count,
            "binary_output_count": layout.summary.binary_output_count,
            "excluded_string_count": layout.summary.excluded_string_count,
        },
        "capacity": {
            "mode": capacity.mode,
            "stride": capacity.stride,
            "max_devices": capacity.max_devices,
            "device_count": capacity.device_count,
            "remaining": capacity.remaining,
            "single_read_per_device": capacity.single_read_per_device,
            "limit_reason": capacity.limit_reason,
            # Kapasiteye sigmayan cihazlar. `remaining: 0` tek basina "tam"
            # gibi okunuyordu; plana ALINMAYAN cihaz oldugunu soyleyen tek
            # alan bu. Arayuz bunu uyari olarak gostermeli.
            "dropped_device_count": capacity.dropped_device_count,
            "dropped_device_codes": list(capacity.dropped_device_codes),
        },
        "devices": [
            {
                "device_id": s.device_id,
                "device_code": s.device_code,
                "device_name": s.device_name,
                "slot_index": s.slot_index,
                "unit_id": s.unit_id,
                "block_start": s.block_start,
            }
            for s in slots
        ],
        "points": [
            {
                "device_code": p.device_code,
                "device_name": p.device_name,
                "signal_key": p.signal_key,
                "label": p.label,
                "source": p.source,
                "data_type": p.data_type,
                "unit": p.unit,
                "unit_id": p.unit_id,
                "function": p.function,
                "address": p.address,
                "word_count": p.word_count,
                "scale": p.scale,
                "offset": p.offset,
                "manual": p.manual,
            }
            for p in points
        ],
    }
