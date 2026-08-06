"""field_tools_service testleri — host dogrulama, ping cikti ayristirma,
port kontrolu, DNS cozumleme, traceroute ve toplu tarama.

Gercek ping/traceroute KOSULMAZ (subprocess.run monkeypatch; CI'da ag/ICMP
izni garantisi yok). Port testi 127.0.0.1'de GERCEK bir soketle yapilir.
"""

import socket
import subprocess
import threading

import pytest

from app.services import field_tools_service
from app.services.field_tools_service import (
    FieldToolsError,
    _parse_replies,
    check_port,
    ping_host,
    resolve_dns,
    scan_targets,
    traceroute_host,
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


# --- Port kontrolu (gercek soketle, 127.0.0.1) ------------------------------


def test_check_port_acik():
    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    listener.bind(("127.0.0.1", 0))
    listener.listen(1)
    port = listener.getsockname()[1]
    # accept etmesek de SYN kuyruga girer ve connect basarili olur; yine de
    # temiz olsun diye tek baglantiyi kabul eden bir thread calistir.
    thread = threading.Thread(target=lambda: listener.accept(), daemon=True)
    thread.start()
    try:
        result = check_port("127.0.0.1", port, timeout_ms=2000)
    finally:
        listener.close()
    assert result.open is True
    assert result.port == port
    assert result.error is None


def test_check_port_kapali():
    # Bagli olmayan bir port bul: bind edip hemen kapat.
    probe = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    probe.bind(("127.0.0.1", 0))
    port = probe.getsockname()[1]
    probe.close()
    result = check_port("127.0.0.1", port, timeout_ms=1000)
    assert result.open is False
    assert result.error


def test_check_port_gecersiz_host():
    with pytest.raises(FieldToolsError):
        check_port("127.0.0.1; ls", 80)


# --- DNS --------------------------------------------------------------------


def test_resolve_dns_localhost():
    result = resolve_dns("localhost")
    assert result.resolved is True
    assert any(a.startswith("127.") or a == "::1" for a in result.addresses)


def test_resolve_dns_cozulemeyen():
    result = resolve_dns("cozulemeyen-ad.invalid")
    assert result.resolved is False
    assert result.addresses == []


# --- Traceroute (subprocess monkeypatch) ------------------------------------


def test_traceroute_cikti_doner(monkeypatch):
    def fake_run(*args, **kwargs):
        return subprocess.CompletedProcess(
            args=args, returncode=0, stdout=" 1:  10.0.0.1  0.5ms\n 2:  10.0.0.5  1.2ms\n", stderr=""
        )

    monkeypatch.setattr(field_tools_service.subprocess, "run", fake_run)
    result = traceroute_host("10.0.0.5", max_hops=5)
    assert result.success is True
    assert "10.0.0.5" in result.output


def test_traceroute_binary_yoksa(monkeypatch):
    def fake_run(*args, **kwargs):
        raise FileNotFoundError("tracepath")

    monkeypatch.setattr(field_tools_service.subprocess, "run", fake_run)
    with pytest.raises(FieldToolsError) as exc:
        traceroute_host("10.0.0.5")
    assert exc.value.code == "trace_unavailable"


# --- Toplu tarama -----------------------------------------------------------


def test_scan_targets(monkeypatch):
    def fake_ping(host, count):
        ok = host == "10.0.0.5"
        return field_tools_service.PingResult(
            host=host,
            success=ok,
            packets_sent=count,
            packets_received=1 if ok else 0,
            packet_loss_percent=0.0 if ok else 100.0,
            rtt_avg_ms=2.0 if ok else None,
            output="",
            duration_ms=10,
        )

    def fake_port(host, port, timeout_ms=2000):
        return field_tools_service.PortCheckResult(
            host=host, port=port, open=(host == "10.0.0.5"), elapsed_ms=5
        )

    monkeypatch.setattr(field_tools_service, "ping_host", fake_ping)
    monkeypatch.setattr(field_tools_service, "check_port", fake_port)

    results = scan_targets(
        [(1, "10.0.0.5", 20001), (2, "10.0.0.9", 20001), (3, None, 20001)]
    )
    by_id = {r.device_id: r for r in results}
    assert by_id[1].ping_success is True and by_id[1].port_open is True
    assert by_id[2].ping_success is False and by_id[2].port_open is False
    assert by_id[3].error == "no_ip" and by_id[3].ping_success is None
