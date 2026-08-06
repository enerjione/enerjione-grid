"""field_tools_service testleri — host dogrulama + ping cikti ayristirma.

Gercek ping KOSULMAZ; _parse_replies dogrudan, ping_host ise subprocess.run
monkeypatch'lenerek test edilir (CI'da ag/ICMP izni garantisi yok).
"""

import subprocess

import pytest

from app.services import field_tools_service
from app.services.field_tools_service import (
    FieldToolsError,
    _parse_replies,
    ping_host,
    validate_host,
)

LINUX_OUTPUT = """PING 10.0.0.5 (10.0.0.5) 56(84) bytes of data.
64 bytes from 10.0.0.5: icmp_seq=1 ttl=64 time=0.45 ms
64 bytes from 10.0.0.5: icmp_seq=2 ttl=64 time=1.10 ms

--- 10.0.0.5 ping statistics ---
3 packets transmitted, 2 received, 33% packet loss, time 2003ms
rtt min/avg/max/mdev = 0.450/0.775/1.100/0.325 ms
"""

WINDOWS_TR_OUTPUT = """10.0.0.5 adresine ping gonderiliyor. 32 bayt veri:
10.0.0.5 yanit: bayt=32 sure=4ms TTL=64
10.0.0.5 yanit: bayt=32 sure<1ms TTL=64
Istek zaman asimina ugradi.

10.0.0.5 icin Ping istatistikleri:
    Paket: Giden = 3, Gelen = 2, Kaybolan = 1 (%33 kayip),
"""


def test_validate_host_kabul_eder():
    assert validate_host("10.0.0.5") == "10.0.0.5"
    assert validate_host(" 2001:db8::1 ") == "2001:db8::1"
    assert validate_host("fid-01.saha.local") == "fid-01.saha.local"


@pytest.mark.parametrize(
    "kotu",
    ["", "  ", "10.0.0.5; rm -rf /", "host name", "a..b", "-onek.com", "10.0.0.5 && ls"],
)
def test_validate_host_reddeder(kotu):
    with pytest.raises(FieldToolsError) as exc:
        validate_host(kotu)
    assert exc.value.code == "invalid_host"


def test_parse_replies_linux():
    received, rtts = _parse_replies(LINUX_OUTPUT)
    assert received == 2
    assert rtts == [0.45, 1.10]


def test_parse_replies_windows_turkce():
    # "sure<1ms" satirinda RTT yakalanir; "zaman asimi" satiri sayilmaz.
    received, rtts = _parse_replies(WINDOWS_TR_OUTPUT)
    assert received == 2
    assert rtts == [4.0, 1.0]


def test_ping_host_ozet(monkeypatch):
    def fake_run(*args, **kwargs):
        return subprocess.CompletedProcess(args=args, returncode=0, stdout=LINUX_OUTPUT, stderr="")

    monkeypatch.setattr(field_tools_service.subprocess, "run", fake_run)
    result = ping_host("10.0.0.5", count=3)
    assert result.success is True
    assert result.packets_sent == 3
    assert result.packets_received == 2
    assert result.packet_loss_percent == pytest.approx(33.3)
    assert result.rtt_min_ms == 0.45
    assert result.rtt_max_ms == 1.10
    assert result.rtt_avg_ms == pytest.approx(0.78, abs=0.01)


def test_ping_host_hic_yanit_yok(monkeypatch):
    def fake_run(*args, **kwargs):
        return subprocess.CompletedProcess(args=args, returncode=1, stdout="no reply", stderr="")

    monkeypatch.setattr(field_tools_service.subprocess, "run", fake_run)
    result = ping_host("10.0.0.9", count=2)
    assert result.success is False
    assert result.packets_received == 0
    assert result.packet_loss_percent == 100.0
    assert result.rtt_avg_ms is None


def test_ping_host_binary_yoksa(monkeypatch):
    def fake_run(*args, **kwargs):
        raise FileNotFoundError("ping")

    monkeypatch.setattr(field_tools_service.subprocess, "run", fake_run)
    with pytest.raises(FieldToolsError) as exc:
        ping_host("10.0.0.5", count=1)
    assert exc.value.code == "ping_unavailable"
