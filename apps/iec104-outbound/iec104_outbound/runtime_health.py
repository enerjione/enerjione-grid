"""CALISMA-ZAMANI SAGLIGI -> IEC 104 SISTEM NOKTALARI.

NE COZUYOR
----------
Horstmann Smart modda modemini BILEREK kapatir. Backend ve arayuz bunu
dogru biliyor (`smart_idle` = SAGLIKLI), ama IEC 104 cikisi yalnizca
`telemetry.normalized.*` tuketiyordu: SCADA icin "veri gelmiyor" ile
"cihaz uyuyor" ayni seye benziyordu.

Sonuc: saglikli, uyuyan bir filo dis SCADA'da HABERLESME KAYBI gibi
gorunuyordu. Grid ekraninda dogru, SCADA'da yanlis — kabul edilemez.

IKI NOKTA, IKI AYRI SORU
------------------------
    DEVICE_RUNTIME_STATE  (analog, 0..5)  "cihaz su an hangi durumda"
    DEVICE_REPORT_LATE    (binary, 0/1)   "planli raporu gecikti mi"

`report_late` KANONIK DURUMU EZMEZ ve bu ayrim isin ozudur: bir cihaz
ayni anda `smart_idle` (saglikli, uyuyor) VE `report_late` (bekledigimiz
rapor gelmedi) olabilir. Ikisini tek bir enum'a sikistirmak, ya uyuyan
cihazi arizali gostermek ya da gecikmeyi tamamen kaybetmek olurdu.

NEDEN OLCUM NOKTASI DEGIL SISTEM NOKTASI
----------------------------------------
Bu bilgi bir SAHA OLCUMU degil; gateway'in cihazla olan OTURUMU hakkinda.
Normal telemetri yukune sahte bir sinyal olarak eklemek "olcum" ile
"baglanti durumu" ayrimini bozardi ve katalog/CSV disa aktarimlarda
gercek bir DNP3 noktasiymis gibi gorunurdu.
"""

from __future__ import annotations

from typing import Final

# ---------------------------------------------------------------------------
# DURUM KODLARI
# ---------------------------------------------------------------------------
#
# Sayilar SOZLESMEDIR: SCADA tarafinda nokta listesine yazilir ve
# degistirilemez. Yeni bir durum eklenirse SONA eklenir; mevcut sayilar
# ASLA kaydirilmaz.
STATE_UNKNOWN: Final = 0
STATE_ONLINE: Final = 1
STATE_SMART_IDLE: Final = 2
STATE_RECOVERING: Final = 3
STATE_COMM_LOST: Final = 4
STATE_LISTENER_ERROR: Final = 5

#: Backend `connection_state` -> SCADA kodu.
#:
#: `lost` -> COMM_LOST ve `smart_idle` -> SMART_IDLE AYRI kodlardir. Bu
#: dosyanin var olma sebebi tam olarak bu iki satirin ayri kalmasi.
STATE_CODES: Final[dict[str, int]] = {
    "unknown": STATE_UNKNOWN,
    "online": STATE_ONLINE,
    "smart_idle": STATE_SMART_IDLE,
    "recovering": STATE_RECOVERING,
    "lost": STATE_COMM_LOST,
    "listener_error": STATE_LISTENER_ERROR,
}

#: Saglik kovalari — SCADA'ya YAYINLANMAZ, yalnizca bu modulun kendi
#: dogrulugunu test edebilmesi icin. `smart_idle` SAGLIKLIDIR.
HEALTHY: Final = frozenset({"online", "smart_idle"})
DEGRADED: Final = frozenset({"recovering"})
UNHEALTHY: Final = frozenset({"lost", "listener_error"})


def state_code(connection_state: str | None) -> int:
    """Kanonik durum metnini SCADA koduna cevirir.

    TANIMADIGIMIZ DURUM `UNKNOWN` OLUR, `COMM_LOST` DEGIL. Gateway ileride
    yeni bir durum eklerse, onu "haberlesme kaybi" diye yayinlamak saglikli
    bir cihazi arizali gosterirdi; "bilmiyorum" demek durustur.
    """
    return STATE_CODES.get((connection_state or "").strip().lower(), STATE_UNKNOWN)


# ---------------------------------------------------------------------------
# NOKTA ADRESLERI
# ---------------------------------------------------------------------------
#
# SISTEM BANDI — saha sinyallerinden UZAK.
#
# Katalogdaki gercek sinyal IOA'lari 1..2091 araliginda (uc model dosyasi
# tarandi). 9.000.000 tabani hem o araligin cok uzerinde hem de IEC 104'un
# 3 baytlik IOA tavaninin (16.777.215) altinda.
SYSTEM_IOA_BASE: Final = 9_000_000

#: Bandin ust siniri — IEC 104 IOA 3 BAYTTIR.
SYSTEM_IOA_MAX: Final = 0xFFFFFF

#: Cihaz basina ayrilan yuva sayisi (durum + gecikme).
SLOTS_PER_DEVICE: Final = 2

#: Yuva icindeki konumlar.
SLOT_RUNTIME_STATE: Final = 0
SLOT_REPORT_LATE: Final = 1


def system_ioa(device_id: int, slot: int) -> int:
    """Cihaz basina DETERMINISTIK ve CAKISMASIZ sistem IOA'si.

    NEDEN CIHAZ BASINA AYRI IOA — ILK TASARIM YANLISTI
    --------------------------------------------------
    Ilk surumde her cihaz AYNI IOA'yi kullaniyordu ve ayirt edici olarak
    cihazin kendi Common Address'ine guveniliyordu. Bu VARSAYIM YANLIS:

      * `devices.iec104_common_address` NULLABLE ve UNIQUE KISITI YOK
        (kolon tanimi: `Mapped[int | None]`, migration'da index/unique yok,
        `DeviceCreate`/`DeviceUpdate` semalarinda benzersizlik dogrulamasi
        yok).
      * CA'si olmayan cihazlarin HEPSI hedefin varsayilan CA'sina duser.

    Yani ayni CA'yi paylasan iki cihaz ayni (CA, IOA) ciftine duserdi.
    Normal sinyallerde bu yalnizca ayni sinyal anahtari iki cihazda varsa
    olur; SISTEM NOKTALARI ise HER cihazda kosulsuz bulundugu icin cakisma
    GARANTIYDI: iki cihazin durumu tek bir adrese yazilir, SCADA hangisini
    gordugunu bilemezdi.

    NEDEN `device_id` — SIRA NUMARASI DEGIL
    ---------------------------------------
    Sirali indeks (katalogdaki kacinci cihaz) deterministik GORUNUR ama
    DEGILDIR: araya bir cihaz eklendiginde sonrakilerin hepsi kayar ve
    SCADA'nin nokta listesi sessizce gecersizlesir. `device_id` birincil
    anahtardir; cihaz silinip eklenmedigi surece DEGISMEZ.

    Bant kapasitesi: (16.777.215 - 9.000.000) / 2 = 3.888.607 cihaz.
    """
    if device_id is None or device_id < 0:
        raise ValueError(f"gecersiz device_id: {device_id!r}")
    ioa = SYSTEM_IOA_BASE + int(device_id) * SLOTS_PER_DEVICE + int(slot)
    if ioa > SYSTEM_IOA_MAX:
        raise ValueError(
            f"sistem IOA bandi asildi (device_id={device_id}); IEC 104 IOA "
            f"tavani {SYSTEM_IOA_MAX}"
        )
    return ioa


#: Sinyal anahtarlari — `update_point(device_code, signal_key, ...)` icin.
#: `system.` oneki bilincli: katalogdaki DNP3 anahtarlariyla (or.
#: `master.average_current`) karisamaz.
KEY_RUNTIME_STATE: Final = "system.runtime_state"
KEY_REPORT_LATE: Final = "system.report_late"

#: IEC 104 tipleri — mevcut kapsam icinde (bkz. registry
#: `SUPPORTED_MONITORING_TYPES`): 13 = M_ME_NC_1, 1 = M_SP_NA_1.
#:
#: DURUM icin ANALOG secildi cunku 0..5 arasi bir ENUM tasiyor; tek nokta
#: (binary) yalnizca 0/1 tasir ve alti durumu anlatamazdi.
TYPE_RUNTIME_STATE: Final = 13
TYPE_REPORT_LATE: Final = 1


def system_signals_for_device(device_id: int, model: str) -> list[dict]:
    """Bir CIHAZ icin sistem sinyal tanimlari.

    IOA cihaz basina hesaplanir (`system_ioa`): ayni CA'yi paylasan iki
    cihaz artik cakismaz. `build_point_registry` bunlari normal katalog
    sinyalleriyle AYNI yoldan gecirir — ayri bir nokta uretim yolu acmak
    iki farkli adresleme mantigi demekti.

    `model` alani ZORUNLU: registry sinyalleri modele gore esliyor
    (kartezyen carpim tuzagi, bkz. `build_point_registry`).
    """
    ortak = {
        "is_active": True,
        "iec104_enabled": True,
        "outbound_eligible": True,
        # Zaman damgasi TASINIR: gozlemin NE ZAMAN alindigi, durumun
        # kendisi kadar onemli.
        "iec104_with_timestamp": True,
        "model": model,
    }
    return [
        dict(
            ortak,
            key=KEY_RUNTIME_STATE,
            iec104_type_id=TYPE_RUNTIME_STATE,
            iec104_ioa=system_ioa(device_id, SLOT_RUNTIME_STATE),
        ),
        dict(
            ortak,
            key=KEY_REPORT_LATE,
            iec104_type_id=TYPE_REPORT_LATE,
            iec104_ioa=system_ioa(device_id, SLOT_REPORT_LATE),
        ),
    ]


__all__ = [
    "DEGRADED",
    "HEALTHY",
    "KEY_REPORT_LATE",
    "KEY_RUNTIME_STATE",
    "SLOTS_PER_DEVICE",
    "SLOT_REPORT_LATE",
    "SLOT_RUNTIME_STATE",
    "STATE_CODES",
    "STATE_COMM_LOST",
    "STATE_LISTENER_ERROR",
    "STATE_ONLINE",
    "STATE_RECOVERING",
    "STATE_SMART_IDLE",
    "STATE_UNKNOWN",
    "SYSTEM_IOA_BASE",
    "SYSTEM_IOA_MAX",
    "TYPE_REPORT_LATE",
    "TYPE_RUNTIME_STATE",
    "UNHEALTHY",
    "state_code",
    "system_ioa",
    "system_signals_for_device",
]
