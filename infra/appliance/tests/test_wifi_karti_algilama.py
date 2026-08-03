"""WiFi karti algilama — sistemde NE VARSA ona gore.

YASANAN ARIZA
-------------
Kart tespiti YALNIZCA `nmcli device status` ciktisina bakiyordu. O komut
NetworkManager'in YONETTIGI cihazlari listeler; kart takili ve calisir
durumda olsa bile NM onu yonetmiyorsa listede HIC gorunmez.

Sonuc sahada su celiskiyle goruluyordu:

    ap.ifname = "wlx502b73ac016f"   <- profil arayuz adini BILIYOR
    radio.supported = false          <- ama "kart yok" deniyor

Arayuz de haklı olarak gorev (AP / client) secimini KILITLIYOR: radyo
desteklenmiyorsa mod degistirmenin anlami yok. Yani kullanici AP moduna
gecemiyordu ve ekranda sebep "kart yok" yaziyordu — duzeltilemez bir ariza
gibi.

"Kart yok" ile "karti NetworkManager yonetmiyor" AYNI SEY DEGIL; ikincisi
duzeltilebilir bir durumdur ve oyle raporlanmali.

AD KALIBINA BAKILMIYOR
----------------------
USB adaptorlerde arayuz adi MAC'ten turer (`wlx502b73ac016f`), yerlesik
kartlarda `wlan0` / `wlp2s0` olur. Ad kalibiyla arama yapmak USB
adaptorleri kacirirdi. Cekirdegin isareti `wireless/` (ya da `phy80211/`)
alt dizinidir; tespit ona bakiyor.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

_AJAN = Path(__file__).resolve().parents[1] / "e1-netd.py"


@pytest.fixture(scope="module")
def netd():
    spec = importlib.util.spec_from_file_location("e1netd_test", _AJAN)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _sahte_sysfs(tmp_path: Path, arayuzler: dict[str, bool]) -> str:
    """`{ad: kablosuz_mu}` -> sahte /sys/class/net agaci."""
    kok = tmp_path / "net"
    kok.mkdir()
    for ad, kablosuz in arayuzler.items():
        (kok / ad).mkdir()
        if kablosuz:
            (kok / ad / "wireless").mkdir()
    return str(kok)


def test_usb_adaptor_bulunur(netd, tmp_path) -> None:
    """`wlx<MAC>` adli USB adaptor — ad kalibi tutmaz, isaret tutar."""
    kok = _sahte_sysfs(tmp_path, {"lo": False, "eth0": False, "wlx502b73ac016f": True})
    assert netd.wifi_ifaces_from_sysfs(kok) == ["wlx502b73ac016f"]


def test_yerlesik_kart_da_bulunur(netd, tmp_path) -> None:
    kok = _sahte_sysfs(tmp_path, {"wlp2s0": True, "enp3s0": False})
    assert netd.wifi_ifaces_from_sysfs(kok) == ["wlp2s0"]


def test_phy80211_isareti_de_kabul_edilir(netd, tmp_path) -> None:
    """Bazi surucler `wireless/` yerine yalnizca `phy80211/` acar."""
    kok = tmp_path / "net"
    (kok / "wlan0" / "phy80211").mkdir(parents=True)
    assert netd.wifi_ifaces_from_sysfs(str(kok)) == ["wlan0"]


def test_kablosuz_olmayan_arayuzler_sayilmaz(netd, tmp_path) -> None:
    """Ethernet/loopback kablosuz sayilirsa sistem olmayan bir karti VAR
    sanardi — ters yondeki yalan."""
    kok = _sahte_sysfs(tmp_path, {"lo": False, "eth0": False, "docker0": False})
    assert netd.wifi_ifaces_from_sysfs(kok) == []


def test_sysfs_okunamazsa_patlamaz(netd) -> None:
    """Kok dizin yoksa (kapsayici, farkli OS) tespit sessizce bos donmeli;
    durum raporlamasinin tamami bu yuzden dusmemeli."""
    assert netd.wifi_ifaces_from_sysfs("/boyle-bir-dizin-yok-12345") == []


# --------------------------------------------------------------------------
# Radyo durumu — iki kaynagin BIRLESIMI
# --------------------------------------------------------------------------


def test_nm_gormese_de_kart_var_sayilir(netd, tmp_path, monkeypatch) -> None:
    """ASIL VAKA: kart takili ama NM yonetmiyor.

    Eskiden `supported=false` idi ve arayuz gorev secimini kilitliyordu.
    """
    kok = _sahte_sysfs(tmp_path, {"wlx502b73ac016f": True})
    # Sonucu ONCE hesapla: lambda icinde cagirmak yamalanmis fonksiyonu
    # kendine cagirir (sonsuz ozyineleme).
    bulunan = netd.wifi_ifaces_from_sysfs(kok)
    monkeypatch.setattr(netd, "wifi_ifaces_from_sysfs", lambda *_a: bulunan)

    radio = netd._radio_state(
        devices=[{"type": "ethernet", "ifname": "eth0", "state": "connected", "connection": None}],
        general={"wifi_hw": "enabled", "wifi": "enabled"},
    )
    assert radio["supported"] is True, "NM gormedi diye kart yok sayildi"
    assert radio.get("unmanaged") is True, "sebep bildirilmedi"


def test_nm_goruyorsa_unmanaged_isaretlenmez(netd, monkeypatch) -> None:
    monkeypatch.setattr(netd, "wifi_ifaces_from_sysfs", lambda *_a: ["wlan0"])
    radio = netd._radio_state(
        devices=[{"type": "wifi", "ifname": "wlan0", "state": "connected", "connection": None}],
        general={"wifi_hw": "enabled", "wifi": "enabled"},
    )
    assert radio["supported"] is True
    assert radio.get("unmanaged") is not True


def test_hicbir_kaynak_gormezse_kart_yok(netd, monkeypatch) -> None:
    """Gercekten kart yoksa VAR DEMEYELIM — ters yondeki yalan da kotu."""
    monkeypatch.setattr(netd, "wifi_ifaces_from_sysfs", lambda *_a: [])
    radio = netd._radio_state(
        devices=[{"type": "ethernet", "ifname": "eth0", "state": "connected", "connection": None}],
        general={"wifi_hw": "enabled", "wifi": "enabled"},
    )
    assert radio["supported"] is False
