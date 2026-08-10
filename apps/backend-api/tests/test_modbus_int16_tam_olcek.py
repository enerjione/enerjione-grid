"""int16 register olcegi sahadaki degeri TASIYABILMELI.

YASANAN ARIZA
-------------
Register olcegi olarak katalogdaki `scale` kullaniliyordu. O olcek DNP3
COZME katsayisidir (ham * scale = muhendislik degeri) ve cihazin ham
birimini anlatir: akimlar icin mA. Modbus kodlayicisi tersini uyguluyor
(raw = deger / scale), yani register'a mA yaziyordu:

    master.actual_current, scale=0.001 -> int16 tavani 32767*0.001 = 32.767 A

Bir dagitim fideri rahatca 100-600 A tasir, ariza akimi kA mertebesindedir.
32.767 A ustundeki her deger `clamp_int16` ile 32767'ye kilitleniyordu ve
SCADA sonsuza dek 32.767 A okuyordu. Belirti sinsi: deger "makul" gorunur,
sadece HIC DEGISMEZ.

Bu testler iki seyi kilitler:
  1. Akim/aci sinyallerinin olcegi saha degerini tasiyacak kadar genis.
  2. Genisletme SESSIZ degil — `rescaled` bayragi ve `rescaled_count`
     sayaci ile arayuze bildiriliyor (SCADA'daki eski katsayi guncellenmeli).

Ayrica `unit` modunda bit adreslemesinin cihaz basina KAYMAMASI da burada
korunuyor: o modda her cihaz kendi slave id'sindedir ve register'lar gibi
bitler de 0'dan baslamalidir.
"""

from __future__ import annotations

from app.services.modbus_plan_service import (
    INT16_MAX,
    DeviceSlotPlan,
    build_plan_points,
    build_signal_layout,
    resolve_int16_scale,
)


def _sig(key: str, unit: str | None, scale: float, data_type: str = "analog") -> dict:
    return {
        "key": key,
        "label": key,
        "source": "master",
        "data_type": data_type,
        "unit": unit,
        "display_order": 1,
        "scale": scale,
        "offset": 0.0,
        "is_active": True,
        "modbus_function": None,
        "modbus_address": None,
    }


# ---- Olcek secimi ---------------------------------------------------------

def test_akim_olcegi_fider_akimini_TASIR():
    """0.001 (mA) tavani 32.767 A idi — 100 A'lik normal bir fider bile tasar."""
    scale = resolve_int16_scale(0.001, "A")
    tavan = INT16_MAX * scale
    assert tavan >= 3000, (
        f"akim tavani {tavan} A — dagitim fideri ve ariza akimi bu tavani asar, "
        "SCADA sonsuza dek kilitli deger okur"
    )


def test_aci_olcegi_TAM_TURU_tasir():
    """0.01'in tavani 327.67 idi; 360 derecelik tam tur bile sigmiyordu."""
    assert INT16_MAX * resolve_int16_scale(0.01, "°") >= 360


def test_tasmayan_birimin_cozunurlugu_KORUNUR():
    """Genisletme her sinyale degil, GERCEKTEN tasana uygulanir.

    Batarya gerilimi 0.01 V ile 327 V'a kadar cikar; 3.x V'luk bir hucre
    icin fazlasiyla yeterli. Olcegi buyutmek burada yalnizca cozunurluk
    kaybi olurdu (batarya esikleri 3.40/3.71 V — 0.01 V hassasiyet sart).
    """
    assert resolve_int16_scale(0.01, "V") == 0.01
    assert resolve_int16_scale(0.01, "°C") == 0.01
    assert resolve_int16_scale(1.0, None) == 1.0


def test_olcek_ONDALIK_kalir():
    """Kayan nokta birikimi (0.0999...) SCADA'ya elle girilemez."""
    assert resolve_int16_scale(0.001, "A") == 0.1


def test_sifir_olcek_bolme_hatasi_URETMEZ():
    assert resolve_int16_scale(0.0, "A") == 1.0


# ---- Yerlesim + gorunurluk ------------------------------------------------

def test_int16_yerlesiminde_genisletme_ISARETLENIR():
    layout = build_signal_layout(
        [_sig("master.actual_current", "A", 0.001), _sig("master.device_temperature", "°C", 0.01)],
        value_format="int16",
    )
    by_key = {s.key: s for s in layout.slots}
    akim = by_key["master.actual_current"]
    sicaklik = by_key["master.device_temperature"]

    assert akim.scale == 0.1 and akim.rescaled is True
    assert sicaklik.scale == 0.01 and sicaklik.rescaled is False
    assert layout.summary.rescaled_count == 1, (
        "genisletme sessiz kaldi — SCADA'daki eski katsayinin guncellenmesi "
        "gerektigini operator hicbir yerde goremez"
    )


def test_float32_olcege_DOKUNMAZ():
    """float32'de olcek yoktur; muhendislik birimi dogrudan yazilir."""
    layout = build_signal_layout(
        [_sig("master.actual_current", "A", 0.001)], value_format="float32"
    )
    slot = layout.slots[0]
    assert slot.scale == 0.001 and slot.rescaled is False
    assert layout.summary.rescaled_count == 0


def test_sayac_olcege_DOKUNMAZ():
    """Counter'lar ham 32-bit tamsayidir, olceklenmez."""
    layout = build_signal_layout(
        [_sig("master.fault_counter", "A", 0.001, data_type="counter")],
        value_format="int16",
    )
    assert layout.slots[0].scale == 0.001
    assert layout.slots[0].rescaled is False


# ---- unit modunda bit adresleme -------------------------------------------

def _bit_layout() -> object:
    return build_signal_layout(
        [_sig("master.fault_flag", None, 1.0, data_type="binary")], value_format="int16"
    )


def _slots() -> list[DeviceSlotPlan]:
    return [
        DeviceSlotPlan(1, "DEV-001", "F1", slot_index=0, unit_id=1, block_start=0),
        DeviceSlotPlan(2, "DEV-002", "F2", slot_index=1, unit_id=2, block_start=0),
    ]


def test_unit_modunda_bitler_HER_CIHAZDA_sifirdan_baslar():
    """Her cihaz kendi slave id'sinde; adres duzeni AYNI olmali.

    Eskiden mod ne olursa olsun `slot_index * 100` uygulaniyordu: unit
    modunda 2. cihazin bitleri kendi unit'inde 100'den basliyordu ve SCADA
    eslemesi tutmuyordu.
    """
    points = build_plan_points(slots=_slots(), layout=_bit_layout(), mode="unit")
    bits = {p.device_code: p.address for p in points if p.function == 2}
    assert bits == {"DEV-001": 0, "DEV-002": 0}, bits


def test_block_modunda_bitler_cihaz_basina_AYRISIR():
    """Tek unit id paylasildigi icin bloklarin cakismamasi SART."""
    points = build_plan_points(slots=_slots(), layout=_bit_layout(), mode="block")
    bits = {p.device_code: p.address for p in points if p.function == 2}
    assert bits == {"DEV-001": 0, "DEV-002": 100}, bits
