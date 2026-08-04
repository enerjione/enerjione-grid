"""ILK KURULUMDA yalnizca ACIK LISTEDEKI olcumler arsivlenir.

NEDEN ACIK LISTE
----------------
Katalogda 193 sinyal var; cogu zaman serisi olarak hicbir soruya cevap
vermiyor. Kara liste ("sunlari haric tut") yaklasimi, katalog buyudukce
her yeni sinyalin sessizce arsive girmesi demekti. Acik liste, bir
sinyalin arsive girmesi icin BILINCLI karar gerektirir.

OLCULDU (test sunucusu): 193 sinyalin 60'i acik (54 analog + 6 sayac).
Onceki hal 136/193 idi.

TESTIN ASIL DEGERI
------------------
Uc yonu birden kilitlemek gerekiyor ve ucu de farkli bicimde bozulabilir:
  1. Listedeki olcum ACIK gelmeli — degilse analiz/raporlama verisi hic
     birikmez ve kayip GERI ALINAMAZ (gecmis sonradan doldurulamaz).
  2. Liste disi KAPALI gelmeli — degilse disk gereksiz yere dolar.
  3. Acikca verilen deger EZILMEMELI — degilse operatorun karari sessizce
     geri alinir.
"""

from __future__ import annotations

from app.services.signal_catalog_seed import (
    _ARSIVLENEN_OLCUMLER,
    _ARSIVLENEN_TIPLER,
    _arsiv_varsayilani,
    _olcum_adi,
)


def _v(key: str, tip: str = "analog") -> dict:
    return {"key": key, "data_type": tip}


# --- 1. Listedeki olcumler ACIK -------------------------------------------
def test_yuk_akimi_arsivlenir() -> None:
    for ad in ("actual_current", "minimum_current", "maximum_current",
               "average_current", "last_good_known_current"):
        assert _arsiv_varsayilani(_v(f"master.{ad}"))["historize"] is True, ad


def test_ariza_olcumleri_arsivlenir() -> None:
    """Ariza akimi ve suresi ariza yeri tespitinin dogrudan girdisi."""
    for ad in ("fault_current", "fault_duration"):
        assert _arsiv_varsayilani(_v(f"sat01.{ad}"))["historize"] is True, ad


def test_gerilim_sicaklik_batarya_arsivlenir() -> None:
    for ad in ("actual_voltage", "minimum_voltage", "maximum_voltage",
               "conductor_temperature", "device_temperature",
               "battery_voltage_satellite"):
        assert _arsiv_varsayilani(_v(f"sat02.{ad}"))["historize"] is True, ad


def test_konum_aci_sinyal_gucu_arsivlenir() -> None:
    for ad in ("latitude_degrees", "longitude_seconds", "phase_angle",
               "pitch_angle", "modem_rssi", "rssi_satellite"):
        assert _arsiv_varsayilani(_v(f"master.{ad}"))["historize"] is True, ad


def test_KAYNAKTAN_bagimsiz_karar() -> None:
    """Ayni olcum uc kaynakta tekrarlanir; karar kaynaga gore degismemeli."""
    for kaynak in ("master", "sat01", "sat02"):
        assert _arsiv_varsayilani(_v(f"{kaynak}.actual_current"))["historize"] is True


# --- 2. Liste disi KAPALI --------------------------------------------------
def test_AYAR_parametreleri_arsivlenmez() -> None:
    """Bunlar OLCUM degil AYAR. Degistiklerinde onemli olan 'kim ne zaman
    degistirdi'; onu olay kaydi tutar, zaman serisi degil."""
    for ad in ("nominal_voltage", "trip_level",
               "conductor_temperature_alarm_threshold"):
        assert _arsiv_varsayilani(_v(f"master.{ad}"))["historize"] is False, ad


def test_STATIK_metadata_arsivlenmez() -> None:
    """Cihaz omru boyunca sabit; zaman serisi ayni degeri tekrarlar."""
    for ad in ("serial_number", "firmware_version", "hardware_revision"):
        assert _arsiv_varsayilani(_v(f"master.{ad}"))["historize"] is False, ad


def test_anlami_belirsiz_sinyal_KAPALI_baslar() -> None:
    """Bilmedigimiz bir sinyali arsivlemek yerine kapali basliyoruz."""
    assert _arsiv_varsayilani(_v("master.test_point_level"))["historize"] is False


def test_dijital_ve_metin_arsivlenmez() -> None:
    for tip in ("binary", "binary_output", "string"):
        assert _arsiv_varsayilani(_v("master.her_neyse", tip))["historize"] is False


# --- 3. Sayaclar: tip ile acik --------------------------------------------
def test_SAYACLAR_arsivlenir() -> None:
    """Kumulatif olduklari icin yalnizca arttiklarinda satir yazarlar
    (maliyet ~sifir) ve ariza sikligi raporlamasinin kaynagidir."""
    assert _arsiv_varsayilani(_v("master.permanent_fault_counter", "counter"))["historize"] is True


# --- 4. Acikca verilen deger EZILMEZ --------------------------------------
def test_ACIKCA_verilen_deger_EZILMEZ() -> None:
    acik = {"key": "master.serial_number", "data_type": "analog", "historize": True}
    assert _arsiv_varsayilani(acik)["historize"] is True

    kapali = {"key": "master.actual_current", "data_type": "analog", "historize": False}
    assert _arsiv_varsayilani(kapali)["historize"] is False


def test_girdi_MUTASYONA_ugramaz() -> None:
    veri = _v("master.actual_current")
    _arsiv_varsayilani(veri)
    assert "historize" not in veri


# --- 5. Yardimci ve liste butunlugu ---------------------------------------
def test_olcum_adi_kaynak_onekini_atar() -> None:
    assert _olcum_adi("master.actual_current") == "actual_current"
    assert _olcum_adi("noktasiz") == "noktasiz"


def test_liste_AYAR_parametresi_ICERMEZ() -> None:
    """Regresyon kilidi: biri listeye ayar parametresi eklerse yakalansin."""
    yasak = {"nominal_voltage", "trip_level",
             "conductor_temperature_alarm_threshold", "serial_number",
             "firmware_version", "hardware_revision"}
    assert not (_ARSIVLENEN_OLCUMLER & yasak)
    assert _ARSIVLENEN_TIPLER == frozenset({"counter"})


def test_MEVCUT_satira_dokunulmuyor() -> None:
    """Tohumlama her acilista kosuyor; guncelleme kullanicinin ayarini
    ezmemeli. Varsayilan YALNIZCA yeni satir dalinda uygulanmali."""
    import inspect

    from app.services import signal_catalog_seed as m

    kaynak = inspect.getsource(m.seed_default_signals)
    assert kaynak.index("if current is None:") < kaynak.index("_arsiv_varsayilani")
    assert kaynak.count("_arsiv_varsayilani") == 1
