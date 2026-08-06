"""Guvenlik duvari servisi + ajan dogrulamasi.

Testler iptables'i HIC calistirmaz; host ajaninin (e1-fwd) yazdigi dosyalar
sahte olarak uretilir. Kilitlenen davranislar:

  * Kullanilamazlik sebepleri (dizin yok / ajan hic yazmamis / bayat durum)
  * Duvari ACARKEN host'ta iptables yoksa istek HIC yazilmaz — "dugmeye
    bastim, hicbir sey olmadi" sessiz basarisizligi yerine acik hata.
  * Bekleyen istek varken yeni istek reddedilir (her istek yapilandirmanin
    TAMAMINI tasir; ust uste iki istek hangisinin gecerli oldugunu
    belirsizlestirir).
  * Sema, port yonlendirmenin sistem portlarini (web/SSH/SCADA/FTP)
    dinlemesini reddeder — mevcut servisi sessizce golgelemek yasak.
  * Ajanin bagimsiz dogrulamasi backend semasiyla AYNI kararlari verir
    (ozellikle rezerve port listesi ve kilitlenme korumasi sabitleri).
"""

from __future__ import annotations

import importlib.util
import json
import os
from pathlib import Path

import pytest

from app.schemas.firewall import (
    FirewallConfig,
    FirewallForward,
    FirewallRule,
    RESERVED_LISTEN_PORTS,
)
from app.services import firewall_service as fws


# Ajan modulu (dosya adinda tire var, normal import calismaz).
_AGENT_PATH = (
    Path(__file__).resolve().parents[3] / "infra" / "appliance" / "e1-fwd.py"
)


@pytest.fixture(scope="module")
def agent_module():
    spec = importlib.util.spec_from_file_location("e1_fwd", _AGENT_PATH)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _state(**overrides) -> dict:
    state = {
        "schema": 1,
        "updated_at": "2026-08-06T10:00:00+00:00",
        "iptables": True,
        "ipv6": True,
        "enabled": False,
        "active": False,
        "mismatch": None,
        "config": {"enabled": False, "rules": [], "forwards": []},
        "changed_by": None,
        "changed_at": None,
        "guard_tcp_ports": [22, 80, 443],
        "reserved_listen_ports": sorted(RESERVED_LISTEN_PORTS),
        "max_rules": 50,
        "max_forwards": 20,
        "reason": None,
    }
    for key, value in overrides.items():
        state[key] = value
    return state


@pytest.fixture
def agent_dir(tmp_path, monkeypatch):
    """Izole ajan dizini. `write()` ile state.json tazelenir."""
    monkeypatch.setattr(fws.settings, "firewall_state_dir", str(tmp_path))

    class Agent:
        dir = tmp_path

        @staticmethod
        def write(state: dict | None = None) -> None:
            (tmp_path / fws.STATE_FILE).write_text(
                json.dumps(state if state is not None else _state()),
                encoding="utf-8",
            )

    return Agent


# ------------------------------------------------------------ durum ------
def test_state_dir_missing(tmp_path, monkeypatch):
    monkeypatch.setattr(fws.settings, "firewall_state_dir", str(tmp_path / "yok"))
    result = fws.read_status()
    assert result.available is False
    assert result.reason == "state_dir_missing"


def test_agent_never_reported(agent_dir):
    result = fws.read_status()
    assert result.available is False
    assert result.reason == "agent_never_reported"


def test_valid_state_is_parsed(agent_dir):
    agent_dir.write(
        _state(
            enabled=True,
            active=True,
            config={
                "enabled": True,
                "rules": [
                    {
                        "action": "allow",
                        "proto": "tcp",
                        "ports": "2404-2406",
                        "source": None,
                        "comment": "IEC 104",
                    }
                ],
                "forwards": [],
            },
            changed_by="muhendis",
        )
    )
    result = fws.read_status()
    assert result.available is True
    assert result.reason is None
    assert result.enabled is True
    assert result.active is True
    assert result.config is not None
    assert result.config.rules[0].ports == "2404-2406"
    assert result.changed_by == "muhendis"
    assert 22 in result.guard_tcp_ports


def test_stale_state_is_flagged(agent_dir):
    agent_dir.write()
    old = fws.time.time() - (fws.STALE_STATE_SECONDS + 60)
    os.utime(agent_dir.dir / fws.STATE_FILE, (old, old))
    result = fws.read_status()
    # Bayatlik BLOKLAMAZ, sadece uyarir: reboot sonrasi duvar geri
    # kurulmayabilir, kullanici bunu gormeli.
    assert result.available is True
    assert result.reason == "state_stale"


# ------------------------------------------------------------ istek ------
def _config(enabled: bool = True) -> FirewallConfig:
    return FirewallConfig(
        enabled=enabled,
        rules=[FirewallRule(action="allow", proto="tcp", ports="4222")],
        forwards=[],
    )


def test_request_written_with_full_config(agent_dir):
    agent_dir.write()
    request_id = fws.request_set_config(
        _config(),
        actor_username="muhendis",
        actor_role="engineer",
        actor_ip="10.0.0.5",
    )
    raw = json.loads((agent_dir.dir / fws.REQUEST_FILE).read_text(encoding="utf-8"))
    assert raw["id"] == request_id
    assert raw["action"] == "set_config"
    assert raw["requested_by"] == "muhendis"
    assert raw["config"]["enabled"] is True
    assert raw["config"]["rules"][0]["ports"] == "4222"


def test_pending_request_rejected(agent_dir):
    agent_dir.write()
    fws.request_set_config(_config(), actor_username="a", actor_role="engineer")
    with pytest.raises(fws.FirewallError, match="request_pending"):
        fws.request_set_config(_config(), actor_username="b", actor_role="engineer")


def test_enable_without_iptables_rejected(agent_dir):
    # Ajan "host'ta iptables yok" diyor: ACMA istegi hic yazilmamali.
    agent_dir.write(_state(iptables=False))
    with pytest.raises(fws.FirewallError, match="iptables_missing"):
        fws.request_set_config(_config(True), actor_username="a", actor_role="engineer")
    assert not (agent_dir.dir / fws.REQUEST_FILE).exists()
    # KAPATMA istegi ise gecmeli — guvenli yone gitmek her zaman mumkun.
    fws.request_set_config(_config(False), actor_username="a", actor_role="engineer")
    assert (agent_dir.dir / fws.REQUEST_FILE).exists()


# ------------------------------------------------------------ sema -------
def test_schema_rejects_reserved_listen_port():
    # Yayinlanan her port reddedilmeli (web, SSH, SCADA, FTP pasif araligi).
    for port in (22, 80, 2404, 4222, 30005):
        with pytest.raises(ValueError):
            FirewallForward(proto="tcp", listen_port=port, dest_ip="192.168.1.50", dest_port=502)
    # Rezerve olmayan port gecer.
    fwd = FirewallForward(proto="tcp", listen_port=8502, dest_ip="192.168.1.50", dest_port=502)
    assert fwd.listen_port == 8502


def test_schema_rejects_bad_rule_values():
    with pytest.raises(ValueError):
        FirewallRule(action="allow", proto="tcp", ports="70000")
    with pytest.raises(ValueError):
        FirewallRule(action="allow", proto="tcp", ports="2406-2404")  # ters aralik
    with pytest.raises(ValueError):
        FirewallRule(action="allow", proto="tcp", ports="2404", source="fe80::/64")  # v6
    rule = FirewallRule(action="deny", proto="udp", ports="5000-5010", source="10.0.0.1")
    assert rule.source == "10.0.0.1/32"  # tek adres /32'ye normalize edilir


# ------------------------------------------------------- ajan paritesi ---
def test_agent_reserved_ports_match_schema(agent_module):
    """Backend erken uyari listesi ile ajanin nihai listesi AYNI olmali;
    ayrisirlarsa kullanici backend'in kabul ettigi istegin ajanda sessizce
    reddedildigini gorur (ya da tersi: koruma delinir)."""
    assert frozenset(agent_module.RESERVED_LISTEN) == RESERVED_LISTEN_PORTS
    assert tuple(agent_module.GUARD_TCP) == (22, 80, 443)


def test_agent_validates_like_schema(agent_module):
    ok = agent_module._validate_config(
        {
            "enabled": True,
            "rules": [
                {"action": "allow", "proto": "tcp", "ports": "2404-2406", "source": "10.0.0.0/24"}
            ],
            "forwards": [
                {"proto": "tcp", "listen_port": 8502, "dest_ip": "192.168.1.50", "dest_port": 502}
            ],
        }
    )
    assert ok["rules"][0]["ports"] == "2404-2406"
    assert ok["forwards"][0]["listen_port"] == 8502

    # Rezerve dinleme portu ajanda da reddedilir.
    with pytest.raises(agent_module.RequestError):
        agent_module._validate_config(
            {
                "enabled": True,
                "rules": [],
                "forwards": [
                    {"proto": "tcp", "listen_port": 2404, "dest_ip": "192.168.1.50", "dest_port": 502}
                ],
            }
        )

    # Bilinmeyen alan = protokol uyusmazligi; sessizce yutulmaz.
    with pytest.raises(agent_module.RequestError):
        agent_module._validate_config({"enabled": True, "rules": [], "surpriz": 1})

    # Ayni portu dinleyen iki yonlendirme reddedilir.
    with pytest.raises(agent_module.RequestError):
        agent_module._validate_config(
            {
                "enabled": True,
                "rules": [],
                "forwards": [
                    {"proto": "tcp", "listen_port": 8502, "dest_ip": "192.168.1.50", "dest_port": 502},
                    {"proto": "tcp", "listen_port": 8502, "dest_ip": "192.168.1.60", "dest_port": 502},
                ],
            }
        )
