"""Ag ayarlari servisi — ajan state'ini okuma ve istek yazma.

Testler nmcli CALISTIRMAZ; host ajaninin (e1-netd) yazdigi state.json sahte
olarak uretilir. Kilitlenen davranislar:

  * GERI UYUMLULUK: schema 2 yazan ESKI ajan sahada calisirken backend
    guncellenirse yeni bloklar bos gelir. Bu durumda `agent_schema` dolu
    gelmeli ki UI "ag ajani eski" desin — varsayilanlari olcum gibi gosterip
    "WiFi karti yok" demek, duzeltmeye calistigimiz YALANIN aynisi olurdu.
  * WiFi KAPATMA sadece IP almis bagli bir ethernet varken kabul edilir
    ("cihaz erisilemez kalmasin" degismezi).
  * DONANIM kilidi varken "ac" istegi kuyruga bile alinmaz.
  * "client" gorevine gecis KAYITLI profil ister; yeni ag secmek ayri akis.
  * Radyo kapaliyken tarama/baglanma istekleri erken reddedilir (sahadaki
    "gorunur ag bulunamadi" sikayetinin gercek sebebi buydu).
"""

from __future__ import annotations

import json

import pytest

from app.services import network_service as ns


def _state(**overrides) -> dict:
    state = {
        "schema": 3,
        "updated_at": "2026-07-30T10:00:00+00:00",
        "hostname": "e1-grid",
        "mdns_name": "e1-grid.local",
        "ap": {"connection": "e1-grid-ap", "exists": True, "active": True,
               "ssid": "E1GRID-TEST", "ifname": "wlan0", "address": "10.42.0.1"},
        "wifi": {"supported": True, "ifname": "wlan0", "connection": "e1-grid-wifi",
                 "connected": False, "saved": False},
        "radio": {"supported": True, "enabled": True, "hardware_enabled": True,
                  "blocked_by": None, "desired": None, "changed_at": None,
                  "auto_restored_at": None},
        "wifi_role": {"mode": "ap", "effective": "ap", "since": None, "set_by": None,
                      "fallback_active": False, "fallback_since": None,
                      "next_retry_at": None, "foreign_client": None},
        "internet": {"state": "none", "source": "route", "ifname": None,
                     "via": None, "gateway": None, "checked_at": None},
        "interfaces": [
            {"ifname": "wlan0", "type": "wifi", "state": "connected",
             "connection": "e1-grid-ap", "managed_by_e1": True,
             "addresses": ["10.42.0.1/24"]},
        ],
    }
    for key, value in overrides.items():
        if isinstance(value, dict) and isinstance(state.get(key), dict):
            state[key] = {**state[key], **value}
        else:
            state[key] = value
    return state


def _wired_iface(address: str = "192.168.1.50/24", state: str = "connected") -> dict:
    return {
        "ifname": "enp1s0", "type": "ethernet", "state": state,
        "connection": "e1-grid-eth", "managed_by_e1": True,
        "addresses": [address],
    }


@pytest.fixture
def agent(tmp_path, monkeypatch):
    monkeypatch.setattr(ns.settings, "network_state_dir", str(tmp_path))

    class Agent:
        dir = tmp_path

        @staticmethod
        def write(state: dict | None = None) -> None:
            (tmp_path / ns.STATE_FILE).write_text(
                json.dumps(state if state is not None else _state()), encoding="utf-8"
            )

        @staticmethod
        def request() -> dict:
            return json.loads((tmp_path / ns.REQUEST_FILE).read_text(encoding="utf-8"))

    return Agent


# ------------------------------------------------------------- okuma ----
def test_schema3_blocks_are_parsed(agent):
    agent.write(
        _state(
            radio={"enabled": False, "blocked_by": "software", "desired": "off"},
            wifi_role={"mode": "client", "effective": "off", "foreign_client": "OFIS"},
            internet={"state": "full", "source": "nm", "ifname": "enp1s0",
                      "via": "ethernet", "gateway": "192.168.1.1"},
        )
    )
    status = ns.read_status()
    assert status.agent_schema == 3
    assert status.radio.enabled is False
    assert status.radio.blocked_by == "software"
    assert status.radio.desired == "off"
    # TERCIH ve OLCUM ayri alanlarda kalmali — celiskinin carasi budur.
    assert (status.role.mode, status.role.effective) == ("client", "off")
    assert status.role.foreign_client == "OFIS"
    assert (status.internet.state, status.internet.via) == ("full", "ethernet")


def test_old_agent_state_is_tolerated(agent):
    """schema 2 ajan yeni bloklari yazmaz; 500 vermemeli ve `agent_schema`
    UI'nin "ajan eski" diyebilmesi icin dolu gelmeli."""
    old = _state()
    old["schema"] = 2
    for key in ("radio", "wifi_role", "internet"):
        old.pop(key)
    agent.write(old)
    status = ns.read_status()
    assert status.agent_schema == 2
    # OLCUM YOK -> None. Bos bir nesne (supported=False) DONMEMELI: arayuz
    # onu "Cihazda WiFi karti bulunamadi" diye olculmus bir donanim iddiasina
    # ceviriyordu. None = "bilinmiyor", UI oyle gosterir.
    assert status.radio is None
    assert status.role is None
    assert status.internet is None


def test_corrupt_block_is_reported_as_unknown(agent):
    """Bozuk blok da "olcum yok"tur: uydurma varsayilan URETME.

    Eskiden bos nesneye dusuyordu (enabled=False) ve UI bunu "WiFi kapali"
    diye KESIN bir durum gibi gosteriyordu; oysa tek bildigimiz blogun
    okunamadigi."""
    agent.write(_state(radio={"enabled": "evet"}, internet={"state": "harika"}))
    status = ns.read_status()
    assert status.radio is None
    assert status.internet is None


# ------------------------------------------------- WiFi ac/kapa ---------
def test_radio_off_rejected_without_wired_path(agent):
    """DEGISMEZ: cihaz erisilemez kalmasin. Tek radyo kapaninca AP de duser."""
    agent.write()
    with pytest.raises(ns.NetworkRequestError) as exc:
        ns.request_wifi_radio(False, "installer1")
    assert str(exc.value) == "radio_off_would_strand"
    assert not (agent.dir / ns.REQUEST_FILE).exists()


def test_radio_off_rejected_when_wired_has_only_link_local(agent):
    agent.write(_state(interfaces=[_wired_iface("169.254.1.2/16")]))
    with pytest.raises(ns.NetworkRequestError) as exc:
        ns.request_wifi_radio(False, "installer1")
    assert str(exc.value) == "radio_off_would_strand"


def test_radio_off_rejected_when_wired_is_disconnected(agent):
    agent.write(_state(interfaces=[_wired_iface(state="unavailable")]))
    with pytest.raises(ns.NetworkRequestError):
        ns.request_wifi_radio(False, "installer1")


def test_radio_off_accepted_with_wired_path(agent):
    agent.write(_state(interfaces=[_wired_iface()]))
    request_id = ns.request_wifi_radio(False, "installer1")
    body = agent.request()
    assert body["action"] == "wifi_radio"
    assert body["enabled"] is False
    assert body["id"] == request_id
    assert body["requested_by"] == "installer1"


def test_radio_on_rejected_when_hardware_switch_off(agent):
    """`nmcli radio wifi on` donanim kilidinde de basari doner; kullaniciya
    "actim" demek yerine gercegi soyle."""
    agent.write(_state(radio={"enabled": False, "hardware_enabled": False,
                              "blocked_by": "hardware"}))
    with pytest.raises(ns.NetworkRequestError) as exc:
        ns.request_wifi_radio(True, "installer1")
    assert str(exc.value) == "radio_hardware_blocked"


def test_radio_on_accepted_when_software_blocked(agent):
    agent.write(_state(radio={"enabled": False, "blocked_by": "software"}))
    ns.request_wifi_radio(True, "installer1")
    assert agent.request()["enabled"] is True


# ---------------------------------------------------- gorev secimi ------
def test_mode_client_requires_saved_network(agent):
    agent.write()
    with pytest.raises(ns.NetworkRequestError) as exc:
        ns.request_wifi_mode("client", "installer1")
    assert str(exc.value) == "no_saved_network"


def test_mode_client_accepted_with_saved_network(agent):
    agent.write(_state(wifi={"saved": True}))
    ns.request_wifi_mode("client", "installer1")
    body = agent.request()
    assert (body["action"], body["mode"]) == ("wifi_mode", "client")


def test_mode_ap_needs_no_saved_network(agent):
    agent.write()
    ns.request_wifi_mode("ap", "installer1")
    assert agent.request()["mode"] == "ap"


def test_mode_rejected_when_radio_off(agent):
    agent.write(_state(wifi={"saved": True},
                       radio={"enabled": False, "blocked_by": "software"}))
    with pytest.raises(ns.NetworkRequestError) as exc:
        ns.request_wifi_mode("client", "installer1")
    assert str(exc.value) == "radio_off"


def test_invalid_mode_rejected(agent):
    agent.write()
    with pytest.raises(ns.NetworkRequestError) as exc:
        ns.request_wifi_mode("bridge", "installer1")
    assert str(exc.value) == "invalid_mode"


# --------------------------------------------------------- tarama -------
def test_scan_rejected_when_radio_off(agent):
    """Sahadaki "gorunur ag bulunamadi" sikayetinin gercek sebebi: radyo
    kapaliyken tarama BOS doner ve kullanici sebebini goremez."""
    agent.write(_state(radio={"enabled": False, "blocked_by": "software"}))
    with pytest.raises(ns.NetworkRequestError) as exc:
        ns.request_wifi_scan(False, "installer1")
    assert str(exc.value) == "radio_off"


def test_deep_scan_flag_is_forwarded(agent):
    agent.write()
    ns.request_wifi_scan(True, "installer1")
    assert agent.request()["deep"] is True


def test_old_agent_does_not_block_wifi_actions(agent):
    """schema 2 ajan radyo durumunu OLCMUYOR; olcmedigimiz bir seye dayanip
    istegi reddetmek yeni bir yalan olurdu. Karar ajana birakilir."""
    old = _state()
    old["schema"] = 2
    old.pop("radio")
    agent.write(old)
    ns.request_wifi_scan(False, "installer1")
    assert agent.request()["action"] == "wifi_scan"


def test_internet_check_is_queued(agent):
    agent.write()
    ns.request_internet_check("installer1")
    assert agent.request()["action"] == "net_check"


def test_pending_request_blocks_new_one(agent):
    agent.write(_state(interfaces=[_wired_iface()]))
    ns.request_internet_check("installer1")
    with pytest.raises(ns.NetworkRequestError) as exc:
        ns.request_wifi_radio(False, "installer1")
    assert str(exc.value) == "request_pending"
