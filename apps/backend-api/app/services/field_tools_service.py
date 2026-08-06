"""Saha araclari servisi — ping ile cihaz erisim testi.

Backend'in kostugu mini PC'den hedef IP/hostname'e sistem `ping` binary'si
ile ICMP echo gonderir. Guvenlik notlari:

  - Hedef, komuta gecmeden ONCE dogrulanir (ipaddress veya kati hostname
    regex'i). Shell KULLANILMAZ (subprocess liste argumani) — injection yok.
  - Konteyner non-root (uid 10001) calisir; iputils ping ICMP datagram
    soketi kullanir ve Docker `net.ipv4.ping_group_range` sysctl'ini
    varsayilan acik verdigi icin root gerekmez (Dockerfile: iputils-ping).

Cikti ayristirma LOCALE'DEN BAGIMSIZ tutulur: ozet satirlari (packet loss /
rtt min/avg/max) dilden dile degistigi icin ozet YERINE her yanit satirindaki
"ttl=" (yanit sayisi) ve "time=/sure=" (RTT) desenleri sayilir; min/avg/max
buradan hesaplanir. Windows (dev) ve Linux (production) ciktilarinin ikisini
de kapsar.
"""

import ipaddress
import platform
import re
import socket
import subprocess
import time
from concurrent.futures import ThreadPoolExecutor

from app.schemas.field_tools import (
    DeviceScanResult,
    DnsResult,
    PingResult,
    PortCheckResult,
    TracerouteResult,
)

# RFC 1123 hostname: nokta ile ayrilmis, her etiket 1-63 karakter,
# alfanumerik baslar/biter, arada tire olabilir.
_HOSTNAME_RE = re.compile(
    r"^(?=.{1,253}$)[a-zA-Z0-9]([a-zA-Z0-9-]{0,61}[a-zA-Z0-9])?"
    r"(\.[a-zA-Z0-9]([a-zA-Z0-9-]{0,61}[a-zA-Z0-9])?)*$"
)

# Basarili echo yaniti satiri: Linux "64 bytes from ...: icmp_seq=1 ttl=64
# time=0.045 ms", Windows EN "Reply from ...: bytes=32 time=1ms TTL=64",
# Windows TR "... yanit: bayt=32 sure=1ms TTL=64".
_REPLY_RE = re.compile(r"\bttl[=:]?\s*\d+", re.IGNORECASE)
_RTT_RE = re.compile(r"(?:time|s[uü]re)\s*[=<]\s*([\d.,]+)\s*ms", re.IGNORECASE)

# Tek ping yanitinin bekleme suresi (sn). Toplam sure limiti bundan turetilir.
_PER_PACKET_TIMEOUT_S = 2
_MAX_OUTPUT_CHARS = 4000


class FieldToolsError(Exception):
    """Kullaniciya donecek, kodlanmis servis hatasi."""

    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


def validate_host(host: str) -> str:
    """IP (v4/v6) veya RFC 1123 hostname kabul et; digerini reddet."""
    candidate = host.strip()
    if not candidate:
        raise FieldToolsError("invalid_host")
    try:
        ipaddress.ip_address(candidate)
        return candidate
    except ValueError:
        pass
    if _HOSTNAME_RE.match(candidate):
        return candidate
    raise FieldToolsError("invalid_host")


def _build_command(host: str, count: int) -> list[str]:
    if platform.system() == "Windows":
        # -n adet, -w yanit bekleme (ms)
        return ["ping", "-n", str(count), "-w", str(_PER_PACKET_TIMEOUT_S * 1000), host]
    # Linux iputils: -c adet, -W yanit bekleme (sn), -i araliksa varsayilan 1 sn
    return ["ping", "-c", str(count), "-W", str(_PER_PACKET_TIMEOUT_S), host]


def _parse_replies(output: str) -> tuple[int, list[float]]:
    """Yanit sayisi + RTT listesi. Ozet satiri yerine yanit satirlarini sayar."""
    received = 0
    rtts: list[float] = []
    for line in output.splitlines():
        if not _REPLY_RE.search(line):
            continue
        received += 1
        rtt_match = _RTT_RE.search(line)
        if rtt_match:
            # Windows TR ondalik virgul kullanabilir.
            rtts.append(float(rtt_match.group(1).replace(",", ".")))
    return received, rtts


def ping_host(host: str, count: int) -> PingResult:
    """Hedefe `count` adet ICMP echo gonder, sonucu ozetle.

    Raises:
        FieldToolsError: invalid_host | ping_unavailable | ping_timeout
    """
    target = validate_host(host)
    command = _build_command(target, count)
    # Ust sinir: her paket icin bekleme + paketler arasi 1 sn + tampon.
    overall_timeout = count * (_PER_PACKET_TIMEOUT_S + 1) + 5
    started = time.monotonic()
    try:
        proc = subprocess.run(
            command,
            capture_output=True,
            text=True,
            errors="replace",
            timeout=overall_timeout,
        )
    except FileNotFoundError as exc:
        raise FieldToolsError("ping_unavailable") from exc
    except subprocess.TimeoutExpired as exc:
        raise FieldToolsError("ping_timeout") from exc
    duration_ms = int((time.monotonic() - started) * 1000)

    output = (proc.stdout or "") + (("\n" + proc.stderr) if proc.stderr else "")
    output = output.strip()[:_MAX_OUTPUT_CHARS]

    received, rtts = _parse_replies(output)
    # Windows'ta "Destination host unreachable" satiri da ttl icermez; ttl'li
    # satir sayisi guvenilir "yanit geldi" olcusudur. count'u asamaz.
    received = min(received, count)
    loss = round((count - received) * 100.0 / count, 1)
    return PingResult(
        host=target,
        success=received > 0,
        packets_sent=count,
        packets_received=received,
        packet_loss_percent=loss,
        rtt_min_ms=min(rtts) if rtts else None,
        rtt_avg_ms=round(sum(rtts) / len(rtts), 2) if rtts else None,
        rtt_max_ms=max(rtts) if rtts else None,
        output=output,
        duration_ms=duration_ms,
    )


def check_port(host: str, port: int, timeout_ms: int = 2000) -> PortCheckResult:
    """Hedefte TCP portu acik mi? Ping gecse bile DNP3 portu (20001)
    modem/firewall'da kapali olabilir — sahadaki "ping var, veri yok"
    vakasinin bir numarali sebebi. Baglanti kurulur kurulmaz kapatilir,
    protokol verisi GONDERILMEZ."""
    target = validate_host(host)
    started = time.monotonic()
    try:
        with socket.create_connection((target, port), timeout=timeout_ms / 1000.0):
            pass
        return PortCheckResult(
            host=target,
            port=port,
            open=True,
            elapsed_ms=int((time.monotonic() - started) * 1000),
        )
    except OSError as exc:
        # gaierror (DNS), timeout, refused... hepsi OSError altinda.
        reason = getattr(exc, "strerror", None) or str(exc) or type(exc).__name__
        return PortCheckResult(
            host=target,
            port=port,
            open=False,
            elapsed_ms=int((time.monotonic() - started) * 1000),
            error=str(reason)[:200],
        )


def traceroute_host(host: str, max_hops: int = 15) -> TracerouteResult:
    """Hedefe giden rotayi izle — kopma sistemde mi, operatorde mi,
    sahadaki modemde mi gorunur. Linux'ta `tracepath` kullanilir (root/
    CAP_NET_RAW GEREKTIRMEZ; klasik traceroute konteynerde calismazdi),
    Windows dev'de `tracert`. Cikti locale bagimli oldugu icin ayristirma
    yapilmaz, ham cikti oldugu gibi doner.

    Raises:
        FieldToolsError: invalid_host | trace_unavailable | trace_timeout
    """
    target = validate_host(host)
    if platform.system() == "Windows":
        # -d: DNS cozme (hizli), -w: yanit bekleme (ms)
        command = ["tracert", "-d", "-h", str(max_hops), "-w", "1000", target]
    else:
        # -n: DNS cozme, -m: azami hop
        command = ["tracepath", "-n", "-m", str(max_hops), target]
    # Yanitsiz hop basina ~3 sn'ye kadar surebilir.
    overall_timeout = max_hops * 4 + 30
    started = time.monotonic()
    try:
        proc = subprocess.run(
            command,
            capture_output=True,
            text=True,
            errors="replace",
            timeout=overall_timeout,
        )
    except FileNotFoundError as exc:
        raise FieldToolsError("trace_unavailable") from exc
    except subprocess.TimeoutExpired as exc:
        raise FieldToolsError("trace_timeout") from exc
    duration_ms = int((time.monotonic() - started) * 1000)
    output = (proc.stdout or "") + (("\n" + proc.stderr) if proc.stderr else "")
    return TracerouteResult(
        host=target,
        success=proc.returncode == 0,
        output=output.strip()[:_MAX_OUTPUT_CHARS],
        duration_ms=duration_ms,
    )


def resolve_dns(name: str) -> DnsResult:
    """Ad -> IP cozumleme testi. "IP'ye ping geciyor ama hostname
    cozulmuyor" durumunu ayiklar. IP girilirse oldugu gibi doner."""
    target = validate_host(name)
    started = time.monotonic()
    addresses: list[str] = []
    resolved = False
    try:
        for info in socket.getaddrinfo(target, None):
            addr = str(info[4][0])
            if addr not in addresses:
                addresses.append(addr)
        resolved = len(addresses) > 0
    except socket.gaierror:
        resolved = False
    return DnsResult(
        name=target,
        resolved=resolved,
        addresses=addresses,
        elapsed_ms=int((time.monotonic() - started) * 1000),
    )


# Toplu tarama: istek basina en fazla 50 hedef (schema siniri); her hedef
# 1 ping + 1 TCP denemesi. 10 paralel iscide en kotu ~15 sn/istek.
_SCAN_PING_COUNT = 1
_SCAN_WORKERS = 10


def scan_targets(targets: list[tuple[int, str | None, int]]) -> list[DeviceScanResult]:
    """(device_id, ip, dnp3_port) listesini paralel tara: ping + port.

    Tek hedefin hatasi digerlerini durdurmaz — hata, o satirin `error`
    alaninda doner (no_ip / invalid_host / ping_unavailable / ping_timeout).
    """

    def one(target: tuple[int, str | None, int]) -> DeviceScanResult:
        device_id, ip, port = target
        if not ip:
            return DeviceScanResult(
                device_id=device_id,
                host=None,
                ping_success=None,
                rtt_avg_ms=None,
                port=port,
                port_open=None,
                error="no_ip",
            )
        try:
            ping = ping_host(ip, _SCAN_PING_COUNT)
        except FieldToolsError as exc:
            return DeviceScanResult(
                device_id=device_id,
                host=ip,
                ping_success=None,
                rtt_avg_ms=None,
                port=port,
                port_open=None,
                error=exc.code,
            )
        port_check = check_port(ip, port)
        return DeviceScanResult(
            device_id=device_id,
            host=ip,
            ping_success=ping.success,
            rtt_avg_ms=ping.rtt_avg_ms,
            port=port,
            port_open=port_check.open,
        )

    with ThreadPoolExecutor(max_workers=_SCAN_WORKERS) as pool:
        return list(pool.map(one, targets))
