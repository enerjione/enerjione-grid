"""Horstmann Pole Master Kit — DNP3 sinyal katalogu ureteci.

CALISTIRMA:  python infra/scripts/gen_pole_master_kit_signals.py

Iki dosya uretir (mevcut olanlari EZER):
  apps/backend-api/app/data/horstmann_pole_master_kit_signals.json  (493 nokta)
  apps/backend-api/app/data/horstmann_pmk_set_signals.json          (147 nokta)

NEDEN URETEC, NEDEN ELLE DUZENLENMIS JSON DEGIL: tablo 493 satir ve on
kaynagin ayni olcum kumesini tekrarlamasindan olusuyor. Elle bakim, tek bir
DNP3 indeksinin sessizce kaymasi demekti — ve o kayma sahada "komut gitti
ama hicbir sey olmadi" olarak ortaya cikardi. Uretec sonunda adres, IOA ve
display_order cakismalarini ayrica DOGRULUYOR.

Bir sinyal degisecekse ONCE bu dosya duzenlenir, sonra uretec kosulur.

Kaynak: "Sinyal Listesi - POLE MASTER KIT.pdf" (RTU DNP3 pointlist ekran goruntusu).

Konvansiyonlar horstmann_sn2_signals.json ile BIREBIR ayni tutuldu:
  key            : "<source>.<olcum_adi>"  (source = master | sat01..sat09)
  scale          : cihaz ham birimi -> SI birimi (mA->A 0.001, 1/100C->C 0.01,
                   1/10deg->deg 0.1, 1/100deg->deg 0.01)
  display_order  : kaynak_sirasi*10000 + tip_tabani + tip_ici_sira
                   (SN2'de kaynak*1000 idi; PMK'da 10 kaynak ve 226'ya kadar
                   index oldugu icin bir basamak buyutuldu)
  iec104_ioa     : binary  = 1    + (kaynak_sirasi-1)*100 + sira
                   analog  = 1000 + (kaynak_sirasi-1)*100 + sira
                   counter = 2000 + (kaynak_sirasi-1)*10  + sira
                   string / binary_output = NULL (SN2 ile ayni)
"""

from __future__ import annotations

import json
from pathlib import Path

SOURCES = ["master"] + [f"sat{n:02d}" for n in range(1, 10)]
SRC_ORDINAL = {s: i + 1 for i, s in enumerate(SOURCES)}

TYPE_BASE_DO = {
    "binary": 0,
    "counter": 2000,
    "analog": 3000,
    "string": 5000,
    "binary_output": 7000,
}
GROUP = {"binary": 1, "binary_output": 10, "counter": 20, "analog": 30, "string": 110}
IEC_TYPE = {"binary": 1, "analog": 13, "counter": 15, "string": None, "binary_output": None}
TIP_ADI = {
    "binary": "Binary input",
    "binary_output": "DNP3 komut (CROB)",
    "counter": "Counter",
    "analog": "Analog input",
    "string": "String",
}

rows: list[dict] = []


def add(source: str, data_type: str, index: int, name: str, label: str,
        unit: str | None = None, scale: float = 1.0, dnp3_class: str = "Class 1",
        note: str | None = None) -> None:
    rows.append({
        "_src": source, "_dt": data_type, "_idx": index, "_name": name,
        "_label": label, "_unit": unit, "_scale": scale, "_cls": dnp3_class,
        "_note": note,
    })


# ---------------------------------------------------------------- MASTER: binary in
for idx, name, label in [
    (0, "test", "Test"),
    (16, "operation_mode", "Operation Mode"),
    (20, "battery_status", "Battery Status"),
    (30, "voltage_loss_all_units", "Voltage Loss All Units"),
    (52, "tamper_detection", "Tamper Detection"),
    (56, "local_comm_enabled", "Local Comm Enabled"),
    (60, "password_enabled", "Password Enabled"),
    (73, "solar_power", "Solar Power"),
    (74, "ac_power", "AC Power"),
    (75, "boost_mode_enabled", "Boost Mode Enabled"),
    (76, "fast_curve_enabled", "Fast Curve Enabled"),
]:
    add("master", "binary", idx, name, label)

# ------------------------------------------------- SATELLITE 01-09: binary in
# Sinyal listesi iki blok halinde: sat01-03 dusuk indekslerde, sat04-09 ayri
# bir blokta. Her satir icin (sat01 baslangici, sat04 baslangici).
SAT_BINARY = [
    ("test", "Test", 1, 80),
    ("overcurrent_tripped", "Overcurrent Tripped", 4, 86),
    ("delta_i_delta_t_tripped", "ΔI/ΔT Tripped", 7, 92),
    ("voltage_loss", "Voltage Loss", 10, 98),
    ("current_loss", "Current Loss", 13, 104),
    ("operation_mode", "Operation Mode", 17, 110),
    ("battery_status", "Battery Status", 21, 116),
    ("communication_failure", "Communication Failure", 24, 122),
    ("delta_i_delta_t_pick_up", "ΔI/ΔT Pick-up", 27, 128),
    ("permanent_fault", "Permanent Fault", 31, 134),
    ("momentary_fault", "Momentary Fault", 34, 140),
    ("load_flow_direction_green_a", "Load Flow Direction Green (A)", 37, 146),
    ("load_flow_direction_red_b", "Load Flow Direction Red (B)", 40, 152),
    ("overcurrent_fault_direction_green_a", "Overcurrent Fault Direction Green (A)", 43, 158),
    ("overcurrent_fault_direction_red_b", "Overcurrent Fault Direction Red (B)", 46, 164),
    ("tamper_detection", "Tamper Detection", 53, 176),
    ("local_comm_enabled", "Local Comm Enabled", 57, 182),
    ("password_enabled", "Password Enabled", 61, 188),
    # NOT: sinyal listesinde "Battery Status" IKINCI bir blokta daha geciyor
    # (sat01-03 -> 64-66, sat04-09 -> 221-226). Ikisi de ayni etiketi tasiyor
    # ve ilkinden farki belgelenmemis; MUKERRER kabul edilip TEK KAYIT
    # birakildi (yukaridaki 21/116 blogu). Ayri bir key uretmek, kullaniciya
    # anlami bilinmeyen ikinci bir "Pil durumu" satiri gostermek olurdu.
    ("delta_i_delta_t_fault_direction_green_a", "ΔI/ΔT Fault Direction Green (A)", 67, 200),
    ("delta_i_delta_t_fault_direction_red_b", "ΔI/ΔT Fault Direction Red (B)", 70, 206),
    ("conductor_temperature_alarm", "Conductor Temperature Alarm", 212, 215),
]
for name, label, b13, b49 in SAT_BINARY:
    for n in range(1, 10):
        if name == "conductor_temperature_alarm":
            # 212-220 arasi 9 uydu icin KESINTISIZ.
            idx = 212 + (n - 1)
        elif n <= 3:
            idx = b13 + (n - 1)
        else:
            idx = b49 + (n - 4)
        add(f"sat{n:02d}", "binary", idx, name, label)

# ------------------------------------------------------------------- COUNTERS
for n in range(1, 10):
    add(f"sat{n:02d}", "counter", 2 * (n - 1), "momentary_fault_counter",
        "Momentary Fault Counter", unit="adet")
    add(f"sat{n:02d}", "counter", 2 * (n - 1) + 1, "permanent_fault_counter",
        "Permanent Fault Counter", unit="adet")

# ------------------------------------------------------------ MASTER: analog in
for idx, name, label, unit, scale, cls in [
    (0, "modem_rssi", "Modem RSSI", "dBm", 1.0, "Class 2"),
    (1, "test_point_level", "Test Point Level", None, 1.0, "Class 2"),
    (14, "battery_voltage_satellite", "Battery Voltage Satellite", "V", 0.01, "Class 2"),
    (21, "device_temperature", "Device Temperature", "°C", 0.01, "Class 1"),
    (157, "serial_number", "Serial Number", None, 1.0, "Class 2"),
    (167, "firmware_version", "Firmware Version", None, 1.0, "Class 2"),
    (177, "hardware_revision", "Hardware Revision", None, 1.0, "Class 2"),
]:
    add("master", "analog", idx, name, label, unit, scale, cls)

# ------------------------------------------------- SATELLITE 01-09: analog in
# (isim, etiket, birim, olcek, sinif, sat01 basi, sat04 basi)
# sat04 basi None ise sinyal 9 uydu boyunca KESINTISIZ (sat01 basi + n-1).
SAT_ANALOG = [
    ("test_point_level", "Test Point Level", None, 1.0, "Class 2", 2, 52),
    ("actual_current", "Actual Current", "A", 0.001, "Class 2", 5, 58),
    ("minimum_voltage", "Minimum Voltage", "V", 1.0, "Class 2", 8, 64),
    ("maximum_voltage", "Maximum Voltage", "V", 1.0, "Class 2", 11, 70),
    ("battery_voltage_satellite", "Battery Voltage Satellite", "V", 0.01, "Class 2", 15, 76),
    ("trip_level", "Trip Level", "A", 0.001, "Class 2", 18, 82),
    ("minimum_current", "Minimum Current", "A", 0.001, "Class 2", 22, 88),
    ("maximum_current", "Maximum Current", "A", 0.001, "Class 2", 25, 94),
    ("average_current", "Average Current", "A", 0.001, "Class 2", 28, 100),
    ("fault_current", "Fault Current", "A", 0.001, "Class 1", 31, 106),
    ("fault_duration", "Fault Duration", "ms", 1.0, "Class 1", 34, 112),
    ("last_good_known_current", "Last Good Known Current", "A", 0.001, "Class 1", 37, 118),
    ("actual_voltage", "Actual Voltage", "V", 1.0, "Class 2", 40, 124),
    ("conductor_temperature", "Conductor Temperature", "°C", 0.01, "Class 2", 43, 130),
    ("device_temperature", "Device Temperature", "°C", 0.01, "Class 2", 46, 136),
    ("rssi_satellite", "RSSI Satellite", "dBm", 1.0, "Class 2", 49, 142),
    ("phase_angle", "Phase Angle", "°", 0.1, "Class 2", 148, None),
    ("serial_number", "Serial Number", None, 1.0, "Class 2", 158, None),
    ("firmware_version", "Firmware Version", None, 1.0, "Class 2", 168, None),
    ("hardware_revision", "Hardware Revision", None, 1.0, "Class 2", 178, None),
    ("pitch_angle", "Pitch Angle", "°", 0.01, "Class 2", 187, None),
]
for name, label, unit, scale, cls, b13, b49 in SAT_ANALOG:
    for n in range(1, 10):
        if b49 is None:
            idx = b13 + (n - 1)
        elif n <= 3:
            idx = b13 + (n - 1)
        else:
            idx = b49 + (n - 4)
        add(f"sat{n:02d}", "analog", idx, name, label, unit, scale, cls)

# ------------------------------------------------------------ MASTER: strings
for idx, name, label, cls in [
    (0, "info_sim_serial_number_ccid", "SIM Serial Number (CCID)", "Class 2"),
    (1, "info_network_operator", "Network Operator", "Class 2"),
    (2, "info_network_type", "Network Type", "Class 2"),
    (3, "info_network_registration", "Network Registration", "Class 2"),
    (8, "info_fw_version_satellite", "FW Version Satellite", "Class 2"),
    (12, "info_last_configuration_update", "Last Configuration Update", "Class 1"),
    (16, "info_rtu_status_text", "RTU Status Text", "Class 1"),
    (20, "info_modem_ip_address", "Modem IP Address", "Class 1"),
    (21, "info_comm_library_version", "Comm Library Version", "Class 1"),
    (22, "info_modem_fw_version", "Modem FW Version", "Class 1"),
    (23, "info_modem_imei", "Modem IMEI", "Class 1"),
    (24, "info_modem_dial_in_settings", "Modem Dial-In Settings", "Class 1"),
    (25, "info_gps_latitude_longitude", "GPS Latitude / Longitude", "Class 1"),
    (26, "info_network_rf_status_information", "Network RF Status Information", "Class 2"),
    (65000, "info_serial_number", "Serial Number", "Class 2"),
    (65001, "info_fw_version", "FW Version", "Class 2"),
    (65002, "info_hardware_revision", "Hardware Revision", "Class 2"),
    (65003, "info_part_no", "Part No.", "Class 2"),
]:
    add("master", "string", idx, name, label, dnp3_class=cls)

# ------------------------------------------------- SATELLITE 01-09: strings
SAT_STRING = [
    ("info_serial_number", "Serial Number", "Class 2", 5, 27),
    ("info_fw_version_satellite", "FW Version Satellite", "Class 2", 9, 33),
    ("info_last_configuration_update", "Last Configuration Update", "Class 1", 13, 39),
    ("info_rtu_status_text", "RTU Status Text", "Class 1", 17, 45),
]
for name, label, cls, b13, b49 in SAT_STRING:
    for n in range(1, 10):
        idx = b13 + (n - 1) if n <= 3 else b49 + (n - 4)
        add(f"sat{n:02d}", "string", idx, name, label, dnp3_class=cls)

# ------------------------------------------------- MASTER: binary out (komut)
for idx, name, label in [
    (0, "config_update", "Configuration Update"),
    (1, "dnp3_config_update", "DNP3 Configuration Update"),
    (2, "firmware_update", "Firmware Update"),
    (3, "start_csv_upload", "Start CSV File Upload"),
    (4, "upload_debug_file", "Upload Debug File"),
    (7, "reset_all_fcis", "Reset all FCIs"),
    (10, "trigger_config_download", "Trigger Configuration Download (Bulk)"),
    (11, "trigger_dnp3_config_download", "Trigger DNP3 Configuration Download (Bulk)"),
    (12, "trigger_firmware_download", "Trigger Firmware Download (Bulk)"),
    (19, "reset_tamper_alarm", "Reset Tamper Alarm"),
    (20, "enable_password", "Enable Password"),
    (21, "enable_local_communication", "Enable Local Communication"),
    (22, "modem_firmware_ota", "Modem Firmware Update Over The Air"),
    (23, "software_reset", "Software Reset"),
    (30, "boost_mode", "Boost Mode"),
    (43, "enable_fast_curve", "Enable Fast Curve"),
]:
    add("master", "binary_output", idx, name, label)


# ------------------------------------------------------------------ CIKTI
def kaynak_etiketi(src: str) -> str:
    return "Master" if src == "master" else f"Satellite {int(src[3:]):02d}"


sirali = sorted(rows, key=lambda r: (SRC_ORDINAL[r["_src"]],
                                     list(TYPE_BASE_DO).index(r["_dt"]),
                                     r["_idx"]))

out: list[dict] = []
tip_sira: dict[tuple[str, str], int] = {}
for r in sirali:
    src, dt = r["_src"], r["_dt"]
    ordinal = SRC_ORDINAL[src]
    i = tip_sira.get((src, dt), 0)
    tip_sira[(src, dt)] = i + 1

    if dt == "binary":
        ioa = 1 + (ordinal - 1) * 100 + i
    elif dt == "analog":
        ioa = 1000 + (ordinal - 1) * 100 + i
    elif dt == "counter":
        ioa = 2000 + (ordinal - 1) * 10 + i
    else:
        ioa = None

    aciklama = f"{TIP_ADI[dt]} — {kaynak_etiketi(src)} — index {r['_idx']}"
    if r["_note"]:
        aciklama = f"{aciklama}. {r['_note']}"

    out.append({
        "key": f"{src}.{r['_name']}",
        "label": r["_label"],
        "unit": r["_unit"],
        "description": aciklama,
        "source": src,
        "dnp3_class": r["_cls"],
        "data_type": dt,
        "dnp3_object_group": GROUP[dt],
        "dnp3_index": r["_idx"],
        "scale": r["_scale"],
        "offset": 0.0,
        "supports_alarm": False,
        "display_order": ordinal * 10000 + TYPE_BASE_DO[dt] + i,
        "iec104_type_id": IEC_TYPE[dt],
        "iec104_ioa_offset": ioa,
        "iec104_ioa": ioa,
    })

# ------------------------------------------------------------- DOGRULAMALAR
keys = [o["key"] for o in out]
assert len(keys) == len(set(keys)), "key tekrari var"

çakisma: dict[tuple, str] = {}
for o in out:
    k = (o["source"], o["dnp3_object_group"], o["dnp3_index"])
    assert k not in çakisma, f"DNP3 adres cakismasi {k}: {çakisma[k]} / {o['key']}"
    çakisma[k] = o["key"]

ioa_map: dict[int, str] = {}
for o in out:
    if o["iec104_ioa"] is None:
        continue
    assert o["iec104_ioa"] not in ioa_map, \
        f"IOA cakismasi {o['iec104_ioa']}: {ioa_map[o['iec104_ioa']]} / {o['key']}"
    ioa_map[o["iec104_ioa"]] = o["key"]

do = [o["display_order"] for o in out]
assert len(do) == len(set(do)), "display_order tekrari var"

import collections

# Repo koku: infra/scripts/ -> ../../ 
DATA_DIR = Path(__file__).resolve().parents[2] / "apps" / "backend-api" / "app" / "data"


def ozet(ad: str, satirlar: list[dict]) -> None:
    ioalar = [o["iec104_ioa"] for o in satirlar if o["iec104_ioa"] is not None]
    print(f"--- {ad}: {len(satirlar)} sinyal")
    print("    kaynak:", dict(collections.Counter(o["source"] for o in satirlar)))
    print("    tip   :", dict(collections.Counter(o["data_type"] for o in satirlar)))
    print(f"    IOA   : {min(ioalar)}-{max(ioalar)} ({len(ioalar)} adet)")


ozet("FIZIKSEL KIT (horstmann_pole_master_kit)", out)
(DATA_DIR / "horstmann_pole_master_kit_signals.json").write_text(
    json.dumps(out, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
)

# =====================================================================
# SANAL SET KATALOGU (horstmann_pmk_set)
# =====================================================================
# Setin uc uydusu, SN2 ile AYNI kaynak adlarini kullanir (sat01/sat02/sat03).
# Boylece `sat01.overcurrent_tripped` icin yazilmis bir alarm kurali hem SN2'de
# hem sette calisir, faz eslemesi ve arayuz kanal listesi degismez.
#
# DNP3 adresleri KANONIK SET-1 adresleridir. Sanal kayit DNP3 ile OKUNMAZ
# (fiziksel kit okunur, tag-engine kaynagi yeniden adlandirir); indeksler
# yalnizca belgeleme ve saha dogrulamasi icin tasinir.
SET_SOURCES = ("sat01", "sat02", "sat03")
SET_ORDINAL = {s: i + 1 for i, s in enumerate(SET_SOURCES)}

set_rows = [r for r in sirali if r["_src"] in SET_SOURCES]
set_out: list[dict] = []
set_tip_sira: dict[tuple[str, str], int] = {}
for r in set_rows:
    src, dt = r["_src"], r["_dt"]
    ordinal = SET_ORDINAL[src]
    i = set_tip_sira.get((src, dt), 0)
    set_tip_sira[(src, dt)] = i + 1

    # SN2 ile ayni IOA deseni: kaynak basina 100'luk binary bloklari,
    # 1000+ analog bloklari, 2000+ sayac bloklari.
    if dt == "binary":
        ioa = ordinal * 100 + i
    elif dt == "analog":
        ioa = 1000 + ordinal * 100 + i
    elif dt == "counter":
        ioa = 2000 + ordinal * 10 + i
    else:
        ioa = None

    aciklama = (
        f"{TIP_ADI[dt]} — {kaynak_etiketi(src)} — kanonik index {r['_idx']} "
        f"(sanal set kaydi; DNP3 ile okunmaz, fiziksel kitten turetilir)"
    )
    if r["_note"]:
        aciklama = f"{aciklama}. {r['_note']}"

    set_out.append({
        "key": f"{src}.{r['_name']}",
        "label": r["_label"],
        "unit": r["_unit"],
        "description": aciklama,
        "source": src,
        "dnp3_class": r["_cls"],
        "data_type": dt,
        "dnp3_object_group": GROUP[dt],
        "dnp3_index": r["_idx"],
        "scale": r["_scale"],
        "offset": 0.0,
        "supports_alarm": False,
        "display_order": ordinal * 1000 + TYPE_BASE_DO[dt] // 10 + i,
        "iec104_type_id": IEC_TYPE[dt],
        "iec104_ioa_offset": ioa,
        "iec104_ioa": ioa,
    })

set_keys = [o["key"] for o in set_out]
assert len(set_keys) == len(set(set_keys)), "set katalogunda key tekrari"
set_ioa: dict[int, str] = {}
for o in set_out:
    if o["iec104_ioa"] is None:
        continue
    assert o["iec104_ioa"] not in set_ioa, (
        f"set IOA cakismasi {o['iec104_ioa']}: {set_ioa[o['iec104_ioa']]} / {o['key']}"
    )
    set_ioa[o["iec104_ioa"]] = o["key"]
set_do = [o["display_order"] for o in set_out]
assert len(set_do) == len(set(set_do)), "set display_order tekrari"

# Setin sinyal ADLARI, fiziksel kitteki her uydunun sinyal adlariyla BIREBIR
# ayni olmali — bolme yalnizca kaynak adini degistiriyor, olcum adini degil.
for n in range(1, 10):
    fiziksel = {r["_name"] for r in rows if r["_src"] == f"sat{n:02d}"}
    sanal = {o["key"].split(".", 1)[1] for o in set_out if o["source"] == "sat01"}
    assert fiziksel == sanal, f"sat{n:02d} olcum kumesi set katalogundan farkli"

ozet("SANAL SET (horstmann_pmk_set)", set_out)
(DATA_DIR / "horstmann_pmk_set_signals.json").write_text(
    json.dumps(set_out, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
)
print("yazildi ->", DATA_DIR)
