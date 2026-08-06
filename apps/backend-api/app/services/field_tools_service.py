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
import subprocess
import time

from app.schemas.field_tools import PingResult

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
