"""e1-netd WiFi karar mantigi — radyo, gorev (AP/client), internet.

Bu testler nmcli/donanim CALISTIRMAZ. Ajanin saf ayristirma ve karar
fonksiyonlari komut ciktisini STRING olarak alacak sekilde ayrildi; sahadaki
kritik yollar donanima bagli oldugu icin dogrulanabilir tek katman burasi.

Kilitlenen davranislar:
  * DONANIM kilidi (WIFI-HW=disabled) yazilim bayragini EZER — `nmcli radio
    wifi on` basari dondurse bile radyo acilmaz; UI'nin "actim" demesi
    duzeltmeye calistigimiz YALANIN aynisi olurdu.
  * `effective` bir OLCUMDUR, kural degil. Eski panel "client yoksa AP acik
    olmali" KURALINI olcum gibi gosterdigi icin AP kapaliyken "erisim
    noktasi acik" yaziyordu.
  * "unknown" internet durumu ASLA "internet var"a terfi ettirilmez.
  * OTOMATIK dongu (muhafiz) ASLA yikici degildir: calisan bir client
    baglantisini dusurmez. Yikici olabilecek tek sey kullanicinin ACIK
    istegidir.
  * Radyo kapaliyken AP acmaya calisilmaz (30 sn'de bir anlamsiz hata).
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

_AGENT_PATH = Path(__file__).resolve().parents[3] / "infra" / "appliance" / "e1-netd.py"


@pytest.fixture(scope="module")
def netd():
    if not _AGENT_PATH.is_file():
        pytest.skip(f"e1-netd.py bulunamadi: {_AGENT_PATH}")
    spec = importlib.util.spec_from_file_location("e1_netd", _AGENT_PATH)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


# --------------------------------------------------------------- radyo ---
def test_general_status_parse(netd):
    parsed = netd.parse_general_status("connected:limited:enabled:disabled\n")
    assert parsed == {
        "state": "connected",
        "connectivity": "limited",
        "wifi_hw": "enabled",
        "wifi": "disabled",
    }


def test_general_status_empty(netd):
    assert netd.parse_general_status("") == {
        "state": None,
        "connectivity": None,
        "wifi_hw": None,
        "wifi": None,
    }


@pytest.mark.parametrize(
    "wifi_hw,wifi,expected",
    [
        # (donanim, yazilim) -> (enabled, blocked_by)
        ("enabled", "enabled", (True, None)),
        ("enabled", "disabled", (False, "software")),
        ("disabled", "disabled", (False, "hardware")),
        # NM "WirelessEnabled=true" derken fiziksel anahtar kapali olabilir.
        # Donanim kazanir; aksi halde UI "acik" der ama tarama bos doner.
        ("disabled", "enabled", (False, "hardware")),
    ],
)
def test_radio_soft_vs_hard_block(netd, wifi_hw, wifi, expected):
    radio = netd.radio_from_general(
        {"wifi_hw": wifi_hw, "wifi": wifi}, wifi_device_present=True
    )
    assert radio["supported"] is True
    assert (radio["enabled"], radio["blocked_by"]) == expected


def test_radio_hardware_block_still_counts_as_supported(netd):
    """Donanim kilidinde cihaz satiri kaybolabilir; "disabled" cevabi radyonun
    VAR oldugunu kanitlar. Aksi halde UI "WiFi karti yok" diye yalan soyler."""
    radio = netd.radio_from_general(
        {"wifi_hw": "disabled", "wifi": "disabled"}, wifi_device_present=False
    )
    assert radio["supported"] is True
    assert radio["blocked_by"] == "hardware"


def test_radio_absent_when_no_card(netd):
    radio = netd.radio_from_general(
        {"wifi_hw": "enabled", "wifi": "enabled"}, wifi_device_present=False
    )
    assert radio == {
        "supported": False,
        "enabled": False,
        "hardware_enabled": True,
        "blocked_by": None,
    }


# ------------------------------------------------------------ internet ---
def test_route_parse_with_gateway(netd):
    route = netd.parse_ip_route_get(
        "1.1.1.1 via 192.168.1.1 dev enp1s0 src 192.168.1.50 uid 0 \n    cache"
    )
    assert route == {"ifname": "enp1s0", "gateway": "192.168.1.1", "src": "192.168.1.50"}


def test_route_parse_direct_link(netd):
    route = netd.parse_ip_route_get("1.1.1.1 dev wlan0 src 10.42.0.1 uid 0")
    assert route["ifname"] == "wlan0"
    assert route["gateway"] is None


def test_route_parse_no_default_route(netd):
    assert netd.parse_ip_route_get("")["ifname"] is None
    assert netd.parse_ip_route_get("RTNETLINK answers: Network is unreachable")["ifname"] is None


def test_internet_none_without_route(netd):
    """Varsayilan rota yoksa hicbir yere paket cikamaz — probe HIC kosmamali."""
    result = netd.resolve_internet("full", {"ifname": None}, None)
    assert result["state"] == "none"
    assert result["source"] == "route"
    assert result["via"] is None


def test_internet_uses_nm_value(netd):
    result = netd.resolve_internet(
        "full", {"ifname": "enp1s0", "gateway": "192.168.1.1"}, "ethernet"
    )
    assert (result["state"], result["source"], result["via"]) == ("full", "nm", "ethernet")


def test_internet_limited_is_not_full(netd):
    result = netd.resolve_internet("limited", {"ifname": "wlan0"}, "wifi")
    assert result["state"] == "limited"


def test_internet_unknown_stays_unknown_without_probe(netd):
    result = netd.resolve_internet("unknown", {"ifname": "enp1s0"}, "ethernet")
    assert result["state"] == "unknown"
    assert result["source"] is None


def test_internet_probe_resolves_unknown(netd):
    ok = netd.resolve_internet("unknown", {"ifname": "enp1s0"}, "ethernet", probe_ok=True)
    assert (ok["state"], ok["source"]) == ("full", "probe")
    fail = netd.resolve_internet("unknown", {"ifname": "enp1s0"}, "ethernet", probe_ok=False)
    assert (fail["state"], fail["source"]) == ("limited", "probe")


# -------------------------------------------------------------- gorev ---
@pytest.mark.parametrize(
    "radio_on,ap_active,client,expected",
    [
        (False, False, False, "off"),
        (False, True, False, "off"),   # radyo kapaliyken AP "acik" olamaz
        (True, True, False, "ap"),
        (True, False, True, "client"),
        (True, False, False, "idle"),
    ],
)
def test_effective_is_measurement(netd, radio_on, ap_active, client, expected):
    assert netd.derive_effective(radio_on, ap_active, client) == expected


# ------------------------------------------- WiFi kapatma onkosulu -------
def test_wired_fallback_requires_real_address(netd):
    rows = [
        {"ifname": "enp1s0", "type": "ethernet", "state": "connected",
         "addresses": ["169.254.3.4/16"]},  # link-local = ulasim yolu DEGIL
    ]
    assert netd.pick_wired_fallback(rows) is None


def test_wired_fallback_requires_connected(netd):
    rows = [
        {"ifname": "enp1s0", "type": "ethernet", "state": "unavailable",
         "addresses": ["192.168.1.50/24"]},
    ]
    assert netd.pick_wired_fallback(rows) is None


def test_wired_fallback_ignores_wifi(netd):
    rows = [
        {"ifname": "wlan0", "type": "wifi", "state": "connected",
         "addresses": ["10.42.0.1/24"]},
    ]
    assert netd.pick_wired_fallback(rows) is None


def test_wired_fallback_found(netd):
    rows = [
        {"ifname": "wlan0", "type": "wifi", "state": "connected",
         "addresses": ["10.42.0.1/24"]},
        {"ifname": "enp1s0", "type": "ethernet", "state": "connected",
         "addresses": ["192.168.1.50/24"]},
    ]
    found = netd.pick_wired_fallback(rows)
    assert found is not None and found["ifname"] == "enp1s0"


# ------------------------------------- gorev zorlama (yan etkili) --------
@pytest.fixture
def agent(netd, tmp_path, monkeypatch):
    """Ajani izole bir state dizinine baglar ve nmcli cagrilarini kaydeder."""
    monkeypatch.setattr(netd, "STATE_DIR", str(tmp_path))
    monkeypatch.setattr(netd, "MODE_PATH", str(tmp_path / "mode.json"))
    monkeypatch.setattr(netd, "GUARD_PATH", str(tmp_path / "wifi-guard.json"))
    calls: list[tuple[str, ...]] = []

    def fake_nmcli(*args, **kwargs):
        calls.append(tuple(args))
        return ""

    monkeypatch.setattr(netd, "_nmcli", fake_nmcli)
    monkeypatch.setattr(netd, "_sta_profile_exists", lambda: True)
    monkeypatch.setattr(netd, "_sta_profile_ssid", lambda: "MUSTERI-AG")
    monkeypatch.setattr(netd, "_ap_has_clients", lambda ifname: False)
    netd.calls = calls
    return netd


def _devices():
    return [{"ifname": "wlan0", "type": "wifi", "state": "connected", "connection": None}]


def _radio(enabled=True):
    return {
        "supported": True,
        "enabled": enabled,
        "hardware_enabled": True,
        "blocked_by": None if enabled else "software",
    }


def _ap(active: bool):
    return {"connection": "e1-grid-ap", "exists": True, "active": active}


def test_enforce_skips_when_radio_off(agent, monkeypatch):
    """Radyo kapaliyken AP acmaya calismak ANLAMSIZ: her 30 sn'de bir hata
    loglanir ve kullanici gercek sebebi (kart kapali) hic gormez."""
    monkeypatch.setattr(agent, "_ap_info", lambda d: _ap(False))
    monkeypatch.setattr(agent, "_wifi_client_online", lambda i, d: False)
    agent.calls.clear()
    agent._enforce_wifi_mode(_devices(), _radio(enabled=False))
    assert agent.calls == []


def test_enforce_opens_ap_when_idle(agent, monkeypatch):
    """DEGISMEZ: client yoksa AP acilir — cihaz erisilemez kalmaz."""
    monkeypatch.setattr(agent, "_ap_info", lambda d: _ap(False))
    monkeypatch.setattr(agent, "_wifi_client_online", lambda i, d: False)
    agent.calls.clear()
    agent._enforce_wifi_mode(_devices(), _radio())
    assert ("connection", "up", "e1-grid-ap") in agent.calls


def test_enforce_never_drops_foreign_client(agent, monkeypatch):
    """Tercih "ap" olsa bile OTOMATIK dongu calisan bir client baglantisini
    DUSURMEZ. Sahada tam bu yuzden kurulumcunun SSH oturumu kopmustu."""
    monkeypatch.setattr(agent, "_ap_info", lambda d: _ap(False))
    monkeypatch.setattr(agent, "_wifi_client_online", lambda i, d: True)
    agent.calls.clear()
    agent._enforce_wifi_mode(_devices(), _radio())
    assert agent.calls == []


def test_client_mode_waits_grace_before_falling_back(agent, monkeypatch):
    """Client modunda baglanti kopunca AP'yi HEMEN acmayiz: NM'in kendi
    yeniden baglanmasina sans veririz (aksi halde her kisa kesintide AP
    acilip client'i bogar)."""
    monkeypatch.setattr(agent, "_ap_info", lambda d: _ap(False))
    monkeypatch.setattr(agent, "_wifi_client_online", lambda i, d: False)
    agent._set_mode("client", "installer1")
    agent.calls.clear()

    agent._enforce_wifi_mode(_devices(), _radio())  # ilk tur: sayaci baslat
    assert agent.calls == []
    agent._enforce_wifi_mode(_devices(), _radio())  # grace icinde
    assert agent.calls == []

    state = agent._read_mode()
    state["fallback"]["since"] = state["fallback"]["since"] - agent.CLIENT_GRACE_SEC - 1
    agent._write_mode(state)
    agent._enforce_wifi_mode(_devices(), _radio())
    assert ("connection", "up", "e1-grid-ap") in agent.calls
    assert agent._read_mode()["fallback"]["active"] is True


def test_client_mode_retries_after_fallback(agent, monkeypatch):
    """Tek seferlik fallback, musterinin agi bir saat kesilip geri gelirse
    cihazi sonsuza dek internetsiz birakirdi."""
    monkeypatch.setattr(agent, "_ap_info", lambda d: _ap(True))
    monkeypatch.setattr(agent, "_wifi_client_online", lambda i, d: False)
    monkeypatch.setattr(agent, "_sta_is_online", lambda i: True)
    agent._set_mode("client", "installer1")
    state = agent._read_mode()
    state["fallback"] = {"active": True, "since": 1.0, "last_attempt": 1.0}
    agent._write_mode(state)
    agent.calls.clear()

    agent._enforce_wifi_mode(_devices(), _radio())
    assert ("connection", "up", "e1-grid-wifi") in agent.calls


def test_client_retry_respects_ap_users(agent, monkeypatch):
    """AP uzerinden birinin oturumu varken yeniden denemeyi ERTELE."""
    monkeypatch.setattr(agent, "_ap_info", lambda d: _ap(True))
    monkeypatch.setattr(agent, "_wifi_client_online", lambda i, d: False)
    monkeypatch.setattr(agent, "_ap_has_clients", lambda ifname: True)
    agent._set_mode("client", "installer1")
    state = agent._read_mode()
    state["fallback"] = {"active": True, "since": 1.0, "last_attempt": 1.0}
    agent._write_mode(state)
    agent.calls.clear()

    agent._enforce_wifi_mode(_devices(), _radio())
    assert agent.calls == []


def test_mode_preference_sets_autoconnect(agent):
    """Tercih NM'in kendi bayragina yazilmazsa cihaz her reboot'ta kayitli
    aga kacar ve kullanicinin acik secimi SESSIZCE ezilir."""
    agent.calls.clear()
    agent._set_mode("ap", "installer1")
    assert ("connection", "modify", "e1-grid-wifi", "connection.autoconnect", "no") in agent.calls

    agent.calls.clear()
    agent._set_mode("client", "installer1")
    modify = [c for c in agent.calls if c[:2] == ("connection", "modify")]
    assert modify and "yes" in modify[0]
    # AP profili autoconnect-priority 100 ile kurulu; client tercihi boot'ta
    # onun onune gecemezse "internete baglan" secimi hic gerceklesmez.
    assert str(agent.STA_AUTOCONNECT_PRIORITY) in modify[0]
    assert int(agent.STA_AUTOCONNECT_PRIORITY) > 100


def test_mode_defaults_to_ap(agent):
    """mode.json yoksa davranis bugunkuyle ayni kalmali."""
    assert agent._read_mode()["mode"] == "ap"
