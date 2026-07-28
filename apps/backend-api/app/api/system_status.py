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
from concurrent.futures import ThreadPoolExecutor, as_completed
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


class VersionInfo(BaseModel):
    """Calisan surum + (bilgi amacli) guncelleme durumu."""

    current: str = Field(..., description="Calisan EnerjiOne Grid surumu")
    check_enabled: bool = Field(..., description="UPDATE_CHECK_URL tanimli mi")
    latest: str | None = Field(default=None, description="Uzakta duyurulan surum")
    update_available: bool = Field(default=False, description="latest > current mi")
    error: str | None = Field(default=None, description="Kontrol hatasi (varsa)")
    checked_at: float | None = Field(default=None, description="Son kontrol (epoch)")


@router.get("/version", response_model=VersionInfo)
def get_version_info(_: User = Depends(get_current_user)):
    """Surum bilgisi ve yeni surum uyarisi — SALT OKUNUR.

    Bu uc bilerek yalnizca BILGI dondurur; guncelleme baslatan bir uc YOKTUR.
    Guncelleme `update.sh` ile operator kontrolunde yapilir (calisan bir SCADA
    sisteminin arayuzden tetiklenen bir islemle yeniden baslamasi istenmez).
    """
    from app.services.version_service import get_version_info as _info

    return VersionInfo(**_info())


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
    role: str = Field(..., description="db | broker | worker | gateway | ftp | web | self")
    healthy: bool
    latency_ms: float | None = None
    detail: str | None = Field(default=None, description="Hata veya bilgi mesaji")
    endpoint: str | None = None


class ServicesReport(BaseModel):
    services: list[ServiceStatus]
    sampled_at: float


_TCP_PROBE_TIMEOUT_SEC = 1.0


def _tcp_probe(host: str, port: int) -> tuple[bool, float, str | None]:
    """TCP socket ile basit erisilebilirlik testi.

    Tum exception'lari yakalar — yoksa `_check_worker` icindeki for-loop'unun
    sonraki host adayini deneme sansi olmaz. Returns: (healthy, latency_ms, error_msg)
    """
    started = time.perf_counter()
    try:
        with socket.create_connection((host, port), timeout=_TCP_PROBE_TIMEOUT_SEC):
            elapsed = (time.perf_counter() - started) * 1000.0
            return True, round(elapsed, 1), None
    except Exception as exc:  # noqa: BLE001 — defensive; her hata sonraki host'a gecsin
        elapsed = (time.perf_counter() - started) * 1000.0
        return False, round(elapsed, 1), f"{type(exc).__name__}: {exc}"


def _http_probe(url: str) -> tuple[bool, float, str | None]:
    """HTTP GET ile basit erisilebilirlik testi (gateway /health endpoint'leri icin)."""
    import urllib.request

    started = time.perf_counter()
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "e1-system-status"})
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


def _check_nats() -> ServiceStatus:
    """NATS JetStream client portuna TCP probe.

    NATS client portu (4222) telemetri akisinin tek yolu — backend, tag-engine,
    alarm-service, iec104-outbound hepsi buraya baglanir. TCP probe yeterli
    cunku NATS server protokolu "INFO" string'ini hemen gonderir; port acik =
    server canli. HTTP monitoring (8222) ek bir test ama localhost-only bind
    oldugu icin uzak deploy'larda erisilemez; client portu evrensel.
    """
    nats_url = getattr(settings, "nats_url", "nats://localhost:4222")
    parsed = urlparse(nats_url)
    host = parsed.hostname or "localhost"
    port = parsed.port or 4222
    ok, ms, err = _tcp_probe(host, port)
    return ServiceStatus(
        name="NATS JetStream",
        role="broker",
        healthy=ok,
        latency_ms=ms,
        endpoint=f"{host}:{port}",
        detail=err,
    )


def _check_worker(
    name: str,
    *,
    env_prefix: str,
    default_hosts: tuple[str, ...],
    default_port: int,
    role: str = "worker",
) -> ServiceStatus:
    """Bir worker servisinin /health portuna TCP probe.

    `role` alani UI'da servisleri gruplayabilmek icin serbest birakildi:
    worker (tag-engine, alarm-service, ...), ftp (ftp-server), gateway
    (whatsapp-web-gateway), web (frontend nginx).

    Host secimi (oncelik sirasi):
      1. Env override: `<ENV_PREFIX>_HEALTH_HOST` (orn. `TAG_ENGINE_HEALTH_HOST`)
      2. `default_hosts` icindeki hostname'leri sirayla dener — ilk erisilebilir
         olan kullanilir. Boylece Docker compose'ta `tag-engine` servis ismi
         otomatik bulunur, native kurulumda `127.0.0.1` fallback olur.

    Port secimi:
      1. `<ENV_PREFIX>_HEALTH_PORT` env varsa o
      2. `WORKER_HEALTH_PORT` env varsa o (worker'larin `.env`'lerinde
         kullandigi standart ad — dikkat: tum worker'larin ortak env'i
         oldugundan farkli portlar icin yalnizca <PREFIX>_HEALTH_PORT kullan!)
      3. `default_port`
    """
    # ----- Port -----
    port: int | None = None
    # ONEMLI: WORKER_HEALTH_PORT ortak env oldugu icin hatali sekilde tum
    # worker'lara ayni port'u uygulayabilir. Sadece <PREFIX>_HEALTH_PORT
    # spesifik override icin kullanilir; diger durumlar default'a duser.
    raw = os.environ.get(f"{env_prefix}_HEALTH_PORT", "").strip()
    if raw and raw.isdigit():
        port = int(raw)
    if port is None:
        port = default_port

    # ----- Host -----
    override = os.environ.get(f"{env_prefix}_HEALTH_HOST", "").strip()
    candidate_hosts: tuple[str, ...] = (override,) if override else default_hosts

    # Aday hostlari PARALEL probe et — eski ardisik versiyonda DNS cozunmeyen
    # her aday icin 1.0sn timeout yiyordu (4 host = 4sn / worker; 4 worker =
    # 16sn). Paralel'de toplam sure tek timeout (1sn) ile sinirli kaliyor;
    # frontend "Checking services…" durumunda takilmiyor.
    tried: list[str] = [h for h in candidate_hosts if h]
    if not tried:
        return ServiceStatus(
            name=name,
            role=role,
            healthy=False,
            latency_ms=0.0,
            endpoint=f":{port}",
            detail="Aday host yok.",
        )

    last_err: str | None = None
    last_ms: float = 0.0
    chosen_host: str = tried[0]
    with ThreadPoolExecutor(max_workers=max(1, len(tried))) as ex:
        future_to_host = {ex.submit(_tcp_probe, h, int(port)): h for h in tried}
        for fut in as_completed(future_to_host):
            host = future_to_host[fut]
            try:
                ok, ms, err = fut.result()
            except Exception as exc:  # noqa: BLE001
                ok, ms, err = False, 0.0, str(exc)
            if ok:
                return ServiceStatus(
                    name=name,
                    role=role,
                    healthy=True,
                    latency_ms=ms,
                    endpoint=f"{host}:{port}",
                )
            last_err = err
            last_ms = ms
            chosen_host = host

    detail = last_err or "TCP probe basarisiz."
    if len(tried) > 1:
        detail = f"{detail} | Denenen: {', '.join(tried)}"
    return ServiceStatus(
        name=name,
        role=role,
        healthy=False,
        latency_ms=last_ms,
        endpoint=f"{chosen_host}:{port}",
        detail=detail,
    )


def _skipped_service_keys() -> set[str]:
    """`SYSTEM_STATUS_SKIP_SERVICES` env'inde listelenen probe anahtarlari.

    Virgulle ayrilir; tire/alt-tire ve buyuk/kucuk harf farki onemsenmez
    (`frontend-web` == `frontend_web`).
    """
    raw = os.environ.get("SYSTEM_STATUS_SKIP_SERVICES", "")
    return {part.strip().lower().replace("-", "_") for part in raw.split(",") if part.strip()}


@router.get("/services", response_model=ServicesReport)
def get_services_status(
    _: User = Depends(get_current_user),
):
    """Backend'in bagli oldugu servislerin (DB, broker, worker'lar) saglik durumu.

    Hizli probe'lar (timeout 1.5s) ile her cagriya tum servisler kontrol edilir.
    Worker servisleri (tag-engine, alarm-service, notification-worker,
    iec104-outbound, ftp-server, whatsapp-web-gateway) ayri proseslerdir; her
    birinin kendi /health portu vardir. Frontend nginx icin de 8080 probe edilir.

    Kurulumda bulunmayan servisler `SYSTEM_STATUS_SKIP_SERVICES` ile listeden
    tamamen cikarilabilir (orn. native dev'de nginx yerine vite kullanilirken:
    `SYSTEM_STATUS_SKIP_SERVICES=frontend_web,ftp_server`). Aksi halde bu
    servisler surekli "kirmizi" gorunur ve gercek arizayi golgeler.
    """
    skip = _skipped_service_keys()

    # Backend (kendisi) - bu cagri zaten basariliysa backend ayakta.
    self_status = ServiceStatus(
        name="Backend API",
        role="self",
        healthy=True,
        latency_ms=0.0,
        endpoint=f"{os.environ.get('BACKEND_HOST', '127.0.0.1')}:{os.environ.get('BACKEND_PORT', '8000')}",
    )

    # Tum probe'lari PARALEL calistir. Eskiden ardisikta 4 worker × 4 host ×
    # 1sn timeout = 16sn'ye kadar bekleyebiliyordu; frontend bu surede sadece
    # "Checking services…" gosterip duruyordu. Paralel'de toplam sure ~1sn'ye
    # iniyor (en yavas tek probe kadar).
    probe_jobs: list[tuple[str, callable]] = [  # type: ignore[name-defined]
        ("database", _check_database),
        ("rabbitmq", _check_rabbitmq),
        ("nats", _check_nats),
        (
            "tag_engine",
            lambda: _check_worker(
                "Tag Engine",
                env_prefix="TAG_ENGINE",
                default_hosts=("tag-engine", "tag_engine", "e1-grid-tag-engine", "e1-tag-engine", "127.0.0.1"),
                default_port=8011,
            ),
        ),
        (
            "alarm_service",
            lambda: _check_worker(
                "Alarm Service",
                env_prefix="ALARM_SERVICE",
                default_hosts=("alarm-service", "alarm_service", "e1-grid-alarm-service", "e1-alarm-service", "127.0.0.1"),
                default_port=8012,
            ),
        ),
        (
            "notification_worker",
            lambda: _check_worker(
                "Notification Worker",
                env_prefix="NOTIFICATION_WORKER",
                default_hosts=(
                    "notification-worker",
                    "notification_worker",
                    "e1-grid-notification-worker",
                    "e1-notification-worker",
                    "127.0.0.1",
                ),
                default_port=8013,
            ),
        ),
        (
            "iec104_outbound",
            lambda: _check_worker(
                "IEC104 Outbound",
                env_prefix="IEC104_OUTBOUND",
                default_hosts=("iec104-outbound", "iec104_outbound", "e1-grid-iec104-outbound", "e1-iec104-outbound", "127.0.0.1"),
                default_port=8014,
            ),
        ),
        (
            "modbus_outbound",
            lambda: _check_worker(
                "Modbus Outbound",
                env_prefix="MODBUS_OUTBOUND",
                default_hosts=("modbus-outbound", "modbus_outbound", "e1-grid-modbus-outbound", "e1-modbus-outbound", "127.0.0.1"),
                default_port=8017,
            ),
        ),
        # ---- Sonradan eklenen servisler (compose'ta var, raporda yoktu) ----
        (
            "ftp_server",
            lambda: _check_worker(
                "FTP Sunucusu",
                env_prefix="FTP_SERVER",
                default_hosts=("ftp-server", "ftp_server", "e1-grid-ftp-server", "e1-ftp-server", "127.0.0.1"),
                default_port=8015,
                role="ftp",
            ),
        ),
        (
            "whatsapp_web_gateway",
            lambda: _check_worker(
                "WhatsApp Web Gateway",
                env_prefix="WHATSAPP_WEB_GATEWAY",
                default_hosts=(
                    "whatsapp-web-gateway",
                    "whatsapp_web_gateway",
                    "e1-grid-whatsapp-web-gateway",
                    "e1-whatsapp-web-gateway",
                    "127.0.0.1",
                ),
                default_port=8016,
                role="gateway",
            ),
        ),
        (
            "frontend_web",
            lambda: _check_worker(
                "Frontend (nginx)",
                env_prefix="FRONTEND_WEB",
                # Container icinde nginx-unprivileged 8080'de dinler; host'a 80
                # ile map edilir. Compose network'unde servis adi kullanilir.
                default_hosts=("frontend-web", "e1-grid-frontend-web", "e1-frontend-web", "127.0.0.1"),
                default_port=8080,
                role="web",
            ),
        ),
    ]

    probe_jobs = [(key, fn) for key, fn in probe_jobs if key not in skip]

    results: dict[str, ServiceStatus] = {}
    with ThreadPoolExecutor(max_workers=max(1, len(probe_jobs))) as ex:
        future_map = {ex.submit(fn): key for key, fn in probe_jobs}
        for fut in as_completed(future_map):
            key = future_map[fut]
            try:
                results[key] = fut.result()
            except Exception as exc:  # noqa: BLE001
                results[key] = ServiceStatus(
                    name=key,
                    role="worker",
                    healthy=False,
                    latency_ms=0.0,
                    endpoint="",
                    detail=str(exc),
                )

    # UI'da gosterim sirasi: cekirdek altyapi -> isleyiciler -> entegrasyonlar.
    display_order = (
        "database",
        "rabbitmq",
        "nats",
        "frontend_web",
        "tag_engine",
        "alarm_service",
        "notification_worker",
        "iec104_outbound",
        "modbus_outbound",
        "ftp_server",
        "whatsapp_web_gateway",
    )
    services: list[ServiceStatus] = [self_status]
    services.extend(results[key] for key in display_order if key in results)

    return ServicesReport(services=services, sampled_at=time.time())
