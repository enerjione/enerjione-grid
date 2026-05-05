"""Backend host'unun anlik kaynak kullanimi (CPU, RAM, disk, uptime) ve
bagimli servis (PostgreSQL, RabbitMQ, worker servisleri) saglik kontrolu.

Sistem Durumu sayfasi ust kisminda gosterilir; cati yazilim ayni Windows/Linux
host'unda calisirken sunucu yukunu hizlica goren bir bakista takip edebilmek
icin kullanilir.

Notlar:
  * `psutil` cross-platform; Windows + Linux + macOS uzerinde ayni alanlari verir.
  * `cpu_percent(interval=None)` blocking degildir: son cagrida tutulan
    snapshot'a gore yuzde doner. Ilk cagri 0.0 doner; bu yuzden modul yuklendigi
    anda bir kez "primer" cagri yaparak warm-up yaptiriyoruz.
  * Servis saglik kontrollerinde TCP-level probe yeterli kabul edilir; AMQP
    handshake yapip bagimliligi (pika) ayri thread'de calistirmak yerine,
    socket connect ile cevap suresini olcuyoruz. PostgreSQL icin SQLAlchemy
    engine.execute('SELECT 1') ile gercek query roundtrip'i olculur.
  * Endpoint cache yok cunku polling 10sn'den siktir; tum probe'lar timeout'lu
    (1.5s) ve hizli; backend yuk altinda olsa bile bu cagrı blocklamamali.
"""
from __future__ import annotations

import os
import platform
import socket
import time
from urllib.parse import urlparse

import psutil
from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field
from sqlalchemy import text

from app.api.deps import get_current_user
from app.core.config import settings
from app.db.session import engine
from app.models.user import User


router = APIRouter(prefix="/system-status", tags=["system-status"])


# psutil.cpu_percent ilk cagri 0.0 verir; modul yuklemesinde primer cagri.
psutil.cpu_percent(interval=None)


class HostCpuMetrics(BaseModel):
    percent: float = Field(..., description="Tum CPU'larin agirlikli ortalamasi (%)")
    per_cpu_percent: list[float] = Field(default_factory=list, description="CPU basina yuzdeler")
    load_avg_1m: float | None = None
    load_avg_5m: float | None = None
    load_avg_15m: float | None = None
    physical_cores: int | None = None
    logical_cores: int | None = None


class HostMemoryMetrics(BaseModel):
    total_bytes: int
    used_bytes: int
    available_bytes: int
    percent: float
    swap_total_bytes: int | None = None
    swap_used_bytes: int | None = None
    swap_percent: float | None = None


class HostDiskMetrics(BaseModel):
    path: str
    total_bytes: int
    used_bytes: int
    free_bytes: int
    percent: float


class HostNetworkMetrics(BaseModel):
    bytes_sent: int
    bytes_recv: int
    packets_sent: int
    packets_recv: int


class HostInfo(BaseModel):
    hostname: str
    os_name: str
    os_release: str
    machine: str
    python_version: str
    boot_time: float = Field(..., description="Unix timestamp (saniye)")
    uptime_seconds: float
    process_pid: int
    process_uptime_seconds: float


class HostStatus(BaseModel):
    info: HostInfo
    cpu: HostCpuMetrics
    memory: HostMemoryMetrics
    disk: HostDiskMetrics
    network: HostNetworkMetrics
    sampled_at: float = Field(..., description="Olcum anin Unix timestamp'i (saniye)")


def _disk_path(override: str | None) -> str:
    """Default disk path:
    * `DISK_USAGE_PATH` env override varsa onu kullan
    * yoksa Windows'ta `C:\\`, Linux'ta `/`
    """
    if override:
        return override
    env = os.environ.get("DISK_USAGE_PATH", "").strip()
    if env:
        return env
    return "C:\\" if os.name == "nt" else "/"


def _safe_load_avg() -> tuple[float | None, float | None, float | None]:
    """Linux'ta os.getloadavg(); Windows'ta yok -> None."""
    try:
        l1, l5, l15 = os.getloadavg()
        return l1, l5, l15
    except (AttributeError, OSError):
        return None, None, None


@router.get("/host", response_model=HostStatus)
def get_host_status(
    disk_path: str | None = Query(default=None, description="Override: hangi mount'un kullanim oranini doneyim"),
    _: User = Depends(get_current_user),
):
    """Backend'in calistigi host'un guncel kaynak metriklerini doner.

    Tum kullanicilar (operator dahil) gorebilir; degerler salt-okunur ve
    hassas degil. Sadece login zorunlu — anonim trafige acmiyoruz cunku
    icerik (hostname, uptime) basit bir reconnaissance imkani saglar.
    """
    boot_t = psutil.boot_time()
    now = time.time()
    proc = psutil.Process(os.getpid())
    proc_create = proc.create_time()

    cpu_percent = psutil.cpu_percent(interval=None)
    per_cpu = list(psutil.cpu_percent(interval=None, percpu=True))
    l1, l5, l15 = _safe_load_avg()

    vm = psutil.virtual_memory()
    sw = psutil.swap_memory()

    path = _disk_path(disk_path)
    try:
        du = psutil.disk_usage(path)
    except (FileNotFoundError, PermissionError, OSError):
        # Path mevcut degilse fallback path'e dus.
        path = "/" if os.name != "nt" else "C:\\"
        du = psutil.disk_usage(path)

    net = psutil.net_io_counters()

    return HostStatus(
        info=HostInfo(
            hostname=socket.gethostname(),
            os_name=platform.system(),
            os_release=platform.release(),
            machine=platform.machine(),
            python_version=platform.python_version(),
            boot_time=boot_t,
            uptime_seconds=max(0.0, now - boot_t),
            process_pid=os.getpid(),
            process_uptime_seconds=max(0.0, now - proc_create),
        ),
        cpu=HostCpuMetrics(
            percent=cpu_percent,
            per_cpu_percent=per_cpu,
            load_avg_1m=l1,
            load_avg_5m=l5,
            load_avg_15m=l15,
            physical_cores=psutil.cpu_count(logical=False),
            logical_cores=psutil.cpu_count(logical=True),
        ),
        memory=HostMemoryMetrics(
            total_bytes=int(vm.total),
            used_bytes=int(vm.used),
            available_bytes=int(vm.available),
            percent=float(vm.percent),
            swap_total_bytes=int(sw.total) if sw else None,
            swap_used_bytes=int(sw.used) if sw else None,
            swap_percent=float(sw.percent) if sw else None,
        ),
        disk=HostDiskMetrics(
            path=path,
            total_bytes=int(du.total),
            used_bytes=int(du.used),
            free_bytes=int(du.free),
            percent=float(du.percent),
        ),
        network=HostNetworkMetrics(
            bytes_sent=int(net.bytes_sent),
            bytes_recv=int(net.bytes_recv),
            packets_sent=int(net.packets_sent),
            packets_recv=int(net.packets_recv),
        ),
        sampled_at=now,
    )


# ---------------------------------------------------------------------------
# Servis saglik kontrolu (RabbitMQ, PostgreSQL, worker'lar)
# ---------------------------------------------------------------------------


class ServiceStatus(BaseModel):
    name: str
    role: str = Field(..., description="db | broker | worker | gateway | self")
    healthy: bool
    latency_ms: float | None = None
    detail: str | None = Field(default=None, description="Hata veya bilgi mesaji")
    endpoint: str | None = None


class ServicesReport(BaseModel):
    services: list[ServiceStatus]
    sampled_at: float


_TCP_PROBE_TIMEOUT_SEC = 1.5


def _tcp_probe(host: str, port: int) -> tuple[bool, float, str | None]:
    """TCP socket ile basit erisilebilirlik testi.

    Returns: (healthy, latency_ms, error_msg)
    """
    started = time.perf_counter()
    try:
        with socket.create_connection((host, port), timeout=_TCP_PROBE_TIMEOUT_SEC):
            elapsed = (time.perf_counter() - started) * 1000.0
            return True, round(elapsed, 1), None
    except (socket.gaierror, OSError, TimeoutError) as exc:
        elapsed = (time.perf_counter() - started) * 1000.0
        return False, round(elapsed, 1), f"{type(exc).__name__}: {exc}"


def _http_probe(url: str) -> tuple[bool, float, str | None]:
    """HTTP GET ile basit erisilebilirlik testi (gateway /health endpoint'leri icin)."""
    import urllib.request

    started = time.perf_counter()
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "hsl-system-status"})
        with urllib.request.urlopen(req, timeout=_TCP_PROBE_TIMEOUT_SEC) as resp:
            elapsed = (time.perf_counter() - started) * 1000.0
            ok = 200 <= resp.status < 300
            return ok, round(elapsed, 1), None if ok else f"HTTP {resp.status}"
    except Exception as exc:  # noqa: BLE001
        elapsed = (time.perf_counter() - started) * 1000.0
        return False, round(elapsed, 1), f"{type(exc).__name__}: {str(exc)[:120]}"


def _check_database() -> ServiceStatus:
    started = time.perf_counter()
    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        elapsed = (time.perf_counter() - started) * 1000.0
        # Hostname/port'u url'den ayikla (sifre maskelenir).
        parsed = urlparse(settings.database_url)
        endpoint = f"{parsed.hostname or '?'}:{parsed.port or '?'}"
        return ServiceStatus(
            name="PostgreSQL",
            role="db",
            healthy=True,
            latency_ms=round(elapsed, 1),
            endpoint=endpoint,
        )
    except Exception as exc:  # noqa: BLE001
        elapsed = (time.perf_counter() - started) * 1000.0
        return ServiceStatus(
            name="PostgreSQL",
            role="db",
            healthy=False,
            latency_ms=round(elapsed, 1),
            detail=f"{type(exc).__name__}: {str(exc)[:200]}",
        )


def _check_rabbitmq() -> ServiceStatus:
    parsed = urlparse(settings.rabbitmq_url)
    host = parsed.hostname or "localhost"
    port = parsed.port or 5672
    ok, ms, err = _tcp_probe(host, port)
    return ServiceStatus(
        name="RabbitMQ",
        role="broker",
        healthy=ok,
        latency_ms=ms,
        endpoint=f"{host}:{port}",
        detail=err,
    )


def _check_worker(name: str, host_port_env_keys: tuple[str, ...], default_port: int) -> ServiceStatus:
    """Native bir worker'in /health portuna TCP probe.

    `host_port_env_keys`: env'den okunacak port keylerinin sirasi (ilk dolu olan
    kullanilir). Bulunamazsa default_port kullanilir.
    """
    host = os.environ.get(f"{name.upper()}_HEALTH_HOST", "127.0.0.1")
    port: int | None = None
    for key in host_port_env_keys:
        raw = os.environ.get(key, "").strip()
        if raw and raw.isdigit():
            port = int(raw)
            break
    if port is None:
        port = default_port
    ok, ms, err = _tcp_probe(host, int(port))
    return ServiceStatus(
        name=name,
        role="worker",
        healthy=ok,
        latency_ms=ms,
        endpoint=f"{host}:{port}",
        detail=err,
    )


@router.get("/services", response_model=ServicesReport)
def get_services_status(
    _: User = Depends(get_current_user),
):
    """Backend'in bagli oldugu servislerin (DB, broker, worker'lar) saglik durumu.

    Hizli probe'lar (timeout 1.5s) ile her cagriya tum servisler kontrol edilir.
    Worker servisleri (tag-engine, alarm-service, notification-worker, iec104-outbound)
    ayri proseslerdir; her birinin kendi /health portu vardir.
    """
    services: list[ServiceStatus] = []

    # Backend (kendisi) - bu cagri zaten basariliysa backend ayakta.
    services.append(
        ServiceStatus(
            name="Backend API",
            role="self",
            healthy=True,
            latency_ms=0.0,
            endpoint=f"{os.environ.get('BACKEND_HOST', '127.0.0.1')}:{os.environ.get('BACKEND_PORT', '8000')}",
        )
    )

    services.append(_check_database())
    services.append(_check_rabbitmq())

    # Worker'lar — gercek varsayilan portlar (worker main.py'lardan).
    # tag-engine: 8011, alarm-service: 8012, notification-worker: 8013,
    # iec104-outbound: 8013 (notification-worker ile cakisir; env override ile
    # uretimde farkli portlara alinir). Her worker icin tercihen kendine
    # ozel env key'i ile override edilebilir.
    services.append(_check_worker("Tag Engine", ("TAG_ENGINE_HEALTH_PORT",), 8011))
    services.append(_check_worker("Alarm Service", ("ALARM_SERVICE_HEALTH_PORT",), 8012))
    services.append(
        _check_worker(
            "Notification Worker", ("NOTIFICATION_WORKER_HEALTH_PORT",), 8013
        )
    )
    services.append(
        _check_worker("IEC104 Outbound", ("IEC104_OUTBOUND_HEALTH_PORT",), 8014)
    )

    return ServicesReport(services=services, sampled_at=time.time())
