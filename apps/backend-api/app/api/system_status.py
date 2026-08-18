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
from datetime import datetime, timezone
from urllib.parse import urlparse

import psutil
from fastapi import APIRouter, Depends, Query, Request
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.api.deps import get_current_user
from app.core import service_role
from app.core.config import settings
from app.db.session import engine, get_db
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
    # Sistemin bu cihazda ILK calistigi an (denetim kaydindaki en eski olay).
    # NULL = henuz hic olay yazilmamis (taze kurulum) veya sorgu basarisiz.
    # DIKKAT: bu "yazilimin kuruldugu tarih" DEGIL, "ilk kez calistigi tarih"
    # olcumudur — ikisi ayni gunde olur ama esdeger degil. Arayuz de bunu
    # "ilk calistirma" olarak etiketler; kesin kurulum damgasi tutan bir alan
    # yok ve uydurmak yaniltici olurdu.
    first_started_at: str | None = Field(
        default=None, description="En eski sistem olayinin zamani (ISO-8601, UTC)"
    )


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


def _first_started_at(db: Session) -> str | None:
    """Denetim kaydindaki EN ESKI olayin zamani — "sistem ne zamandir ayakta".

    Kurulum damgasi tutan bir alan YOK; en eski `system_events` satiri bu
    soruya verilebilecek en dogru cevap (ilk kullanici olusturma, ilk giris
    vb. kurulumun ilk dakikalarinda yazilir). Hata YUTULUR: bu bilgi sayfanin
    ana isi degil, kaynak metrikleri onun yuzunden kaybolmamali.
    """
    try:
        row = db.execute(
            text("SELECT MIN(created_at) FROM system_events")
        ).scalar()
    except Exception:  # noqa: BLE001 — tablo yok / DB kapali: bilgi opsiyonel
        return None
    if row is None:
        return None

    # SAAT DILIMI HER ZAMAN TASINMALI. Surucu/kolon tipine gore buraya
    # datetime da gelebilir duz METIN de (SQLite tarihleri metin saklar).
    # Offset'siz bir metin gonderirsek tarayicidaki `new Date(...)` onu
    # YEREL saat sayar ve tarih sessizce saatlerce kayar — Turkiye'de 3 saat.
    if isinstance(row, datetime):
        stamp = row
    else:
        try:
            stamp = datetime.fromisoformat(str(row))
        except ValueError:
            return None
    if stamp.tzinfo is None:
        # Tum yazma yollari UTC-aware kaydediyor (CLAUDE.md: naive datetime
        # kullanilmaz); tz'yi kaybeden katman surucudur, deger UTC'dir.
        stamp = stamp.replace(tzinfo=timezone.utc)
    return stamp.astimezone(timezone.utc).isoformat(timespec="seconds")


@router.get("/host", response_model=HostStatus)
def get_host_status(
    disk_path: str | None = Query(default=None, description="Override: hangi mount'un kullanim oranini doneyim"),
    db: Session = Depends(get_db),
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
            first_started_at=_first_started_at(db),
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


class HistorianReport(BaseModel):
    """Historian (`telemetry_history`) yapisal sagligi.

    AYRI BIR UC (ServicesReport icine konmadi) cunku:
      * `ServiceStatus` semantigi "ayakta mi + kac ms" ile sinirli; buradaki
        bilgi sayisal (satir/boyut/politika) ve `detail: str` icine
        sikistirilirsa frontend parse edemez.
      * `/services` toplam ~1 sn butcesiyle calisiyor (tum probe'lar 1 sn
        timeout'lu) ve 10 saniyede bir pollenıyor. Historian introspection'i
        o butceyi asarsa TUM servis raporu gecikirdi.
    Bu uc daha seyrek (60 sn) pollenmeli; servis katmani zaten 60 sn cache'li.
    """

    table: str
    timescaledb: str = Field(
        ..., description="installed | available_not_installed | unavailable"
    )
    is_hypertable: bool
    retention_days: int | None = None
    compression_enabled: bool = False
    continuous_aggregates: list[str] = []
    row_estimate: int | None = Field(
        default=None,
        description="pg_class.reltuples TAHMINI — tam sayim degil (COUNT(*) cok pahali)",
    )
    total_bytes: int | None = None
    oldest_sample_at: str | None = None
    newest_sample_at: str | None = None
    retention_last_run_status: str | None = None
    severity: str = Field(..., description="ok | warning | critical")
    problems: list[str] = Field(
        default=[],
        description=(
            "Sabit kodlar (frontend i18n anahtari): timescaledb_missing, "
            "not_hypertable, no_retention, no_compression, retention_failing, "
            "retention_mismatch"
        ),
    )


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


#: Ic proxy isaretcisi. Arka plan surecine giden istek bu basligi tasir;
#: karsi taraf onu gorurse BIR DAHA proxy'lemez. Yanlis yapilandirmada
#: (BACKEND_WORKER_HOST kendini gosterirse) sonsuz zincir olusmasin diye.
_INTERNAL_PROBE_HEADER = "x-e1-internal-probe"


def _is_internal_probe(request: Request | None) -> bool:
    """Istek baska bir backend surecinin ic sorgusu mu?

    Zincir kirici: `BACKEND_WORKER_HOST` yanlislikla surecin KENDISINI
    gosterirse proxy kendi kendini cagirir ve her istek yenilerini dogururdu.
    Bu bayrak ikinci sicramayi engeller — en kotu ihtimalle bir kez proxy'lenip
    yerel (dogru olmayan ama zararsiz) sayaclar doner.
    """
    if request is None:
        return False
    return bool(request.headers.get(_INTERNAL_PROBE_HEADER))


def _forwarded_credentials(request: Request | None) -> dict[str, str]:
    """Cagiranin kimlik basliklarini AYNEN iletilecek sekilde toplar.

    HEM `Cookie` HEM `Authorization`. Yalnizca `Authorization` iletmek
    YETMEZ ve sessizce yanlis sonuc verirdi: bu depoda oturum HttpOnly
    cookie ile tasiniyor (`deps.get_current_user` oncelik sirasi
    cookie > header), yani tarayicidan gelen isteklerin cogunda
    Authorization basligi HIC YOKTUR. O durumda worker 401 doner, uc
    "arka plan surecine ulasilamiyor / critical" gosterir — gercek bir
    arizadan ayirt EDILEMEYEN kalici bir yanlis-pozitif.

    Yeni bir yetki yuzeyi ACILMAZ: worker ayni SECRET_KEY ile ayni JWT'yi
    ayni `get_current_user` ile dogrular.
    """
    if request is None:
        return {}
    iletilecek: dict[str, str] = {}
    for baslik in ("cookie", "authorization"):
        deger = request.headers.get(baslik)
        if deger:
            iletilecek[baslik] = deger
    return iletilecek


def _fetch_worker_pipeline(credentials: dict[str, str]) -> tuple[dict | None, str | None]:
    """Telemetri boru hatti sayaclarini ARKA PLAN surecinden ceker.

    Donus: (rapor, hata). Ikisinden biri her zaman None'dir.

    HATA METNI NEDEN AYRINTILI: "ulasilamiyor" ile "kimlik reddedildi"
    tamamen farkli iki ariza. Ilki worker gercekten kapali demek, ikincisi
    yalnizca yapilandirma hatasi (SECRET_KEY ayrismasi) — ama ikisi de
    ekranda ayni kirmizi olarak gorunurse operator yanlis yeri arar.
    """
    import json as _json
    import urllib.error
    import urllib.request

    hedef = f"{settings.backend_worker_host}:{settings.backend_worker_port}"
    url = f"http://{hedef}{settings.api_prefix}/system-status/telemetry-pipeline"
    headers = {"User-Agent": "e1-system-status", _INTERNAL_PROBE_HEADER: "1"}
    headers.update({k.title(): v for k, v in credentials.items()})
    try:
        req = urllib.request.Request(url, headers=headers)
        with urllib.request.urlopen(req, timeout=_TCP_PROBE_TIMEOUT_SEC) as resp:
            if 200 <= resp.status < 300:
                return _json.loads(resp.read()), None
            return None, f"Arka plan sureci HTTP {resp.status} dondurdu ({hedef})"
    except urllib.error.HTTPError as exc:
        if exc.code in (401, 403):
            return None, (
                f"Arka plan sureci kimligi reddetti (HTTP {exc.code}). "
                "backend-api ve backend-worker AYNI SECRET_KEY'i paylasmali."
            )
        return None, f"Arka plan sureci HTTP {exc.code} dondurdu ({hedef})"
    except Exception as exc:  # noqa: BLE001
        return None, (
            f"Arka plan surecine ulasilamiyor ({hedef}): {type(exc).__name__}. "
            "backend-worker kapali olabilir."
        )


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

    # Arka plan sureci — YALNIZCA ayrik kurulumda (`SERVICE_ROLE=api`) beklenir.
    #
    # Rol `all` ise arka plan zaten bu surecte kosuyor, ayri bir container YOK;
    # listeye kosulsuz eklemek her tek-container kurulumunda kalici kirmizi bir
    # satir gosterir ve gercek arizayi golgeler. Ayrik kurulumda ise tam tersi
    # sart: worker olurse telemetri islenmez ama Sistem Durumu bugun her seyi
    # YESIL gosterir — sessiz ariza.
    if service_role.current_role() == service_role.ROLE_API:
        probe_jobs.append(
            (
                "backend_worker",
                lambda: _check_worker(
                    "Arka Plan Sureci",
                    env_prefix="BACKEND_WORKER",
                    default_hosts=(
                        settings.backend_worker_host,
                        "backend-worker",
                        "e1-grid-backend-worker",
                    ),
                    default_port=settings.backend_worker_port,
                ),
            )
        )

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
        "backend_worker",
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


@router.get("/historian", response_model=HistorianReport)
def get_historian_status(
    refresh: bool = Query(
        default=False,
        description="Cache'i atla (kullanici 'yenile'ye bastiginda)",
    ),
    db: Session = Depends(get_db),
    _: User = Depends(get_current_user),
):
    """Historian'in yapisal sagligi — retention politikasi gercekten kurulu mu?

    NEDEN BU UC VAR: `telemetry_history` 90 gun retention'li bir TimescaleDB
    hypertable olarak tasarlandi, ama migration 0007 timescaledb'siz bir
    ortamda kostuysa tablo DUZ kalir, revision yine damgalanir ve retention
    HIC kurulmaz. 600 cihazda gunde ~26M satir; belirti disk dolana kadar
    ortaya cikmaz. Bu uc durumu disk dolmadan gorunur kilar.

    `severity`:
      ok       — hypertable + retention yerinde
      warning  — calisir ama eksik (orn. compression yok)
      critical — retention YOK veya kosmuyor: tablo sinirsiz buyuyor

    Maliyet: servis katmani 60 sn cache'liyor ve `COUNT(*)` kullanmiyor
    (satir sayisi planlayici tahmini). Duz-tablo durumunda zaman araligi
    sorgusu ATLANIR — tam tarama olurdu.
    """
    from app.services.historian_service import get_historian_status as _status

    return HistorianReport(**_status(db, refresh=refresh).to_dict())


class TelemetryPipelineReport(BaseModel):
    """Telemetri boru hattinin anlik durumu — "tuketici yetisiyor mu?".

    NEDEN BU UC VAR: telemetri NATS stream'inde tamponlanir ve stream
    `discard=old` ile calisir. Tuketici gelis hizinin gerisine duserse tampon
    dolar ve EN ESKI mesajlar SESSIZCE dusurulur — ekranda hata yok, alarm
    yok, sadece bazi okumalar hic gelmemis olur. Bu sessizlik "sistem
    durmasin" tercihinin bedeli; onu tehlikeli olmaktan cikaran tek sey
    tasmaya yaklasildigini gosteren bu olcumdur.

    Ayrica olcek karari bu veriye dayanir: tek uvicorn worker'da tuketici
    API ile ayni surecte kosuyor. `throughput` ve `backlog` birlikte,
    "cihaz sayisi arttiginda tavana ne kadar yakiniz" sorusunu cevaplar.

    MALIYET SIFIRA YAKIN: `backlog` degeri JetStream mesaj metadata'sindan
    (`num_pending`) bedava gelir; ek bir consumer_info cagrisi YOK. Sayaclar
    surec-ici, DB'ye dokunmaz.
    """

    running: bool = Field(..., description="Tuketici thread'i ayakta mi")
    connected: bool = Field(..., description="NATS baglantisi kurulu mu")
    # BILINMEYEN CIHAZ KARANTINASI — yalnizca SUREC-ICI sayaclar.
    #
    # `pending` ve `oldest_pending_age` bilincli olarak BURADA YOK: ikisi de
    # DB sorgusu ister ve bu uc "maliyeti sifira yakin" olmak uzere
    # tasarlandi. Onlar DB'ye zaten dokunan
    # `GET /admin/telemetry-quarantine` ozetinden gelir.
    unknown_device_quarantine_total: int = Field(
        default=0, description="Bu surecte karantinaya alinan olcum sayisi"
    )
    # UC SILME KATEGORISI AYRI RAPORLANIR — anlamlari farkli:
    unknown_device_quarantine_replayed_cleanup_total: int = Field(
        default=0,
        description="Isi bitmis (replayed) ve retention suresi dolmus kayit silindi. Kayip yok.",
    )
    unknown_device_quarantine_expired_total: int = Field(
        default=0,
        description=(
            "Cozulmemis ama retention suresini ASMIS kayit silindi. "
            "Acik saklama politikasinin sonucu."
        ),
    )
    unknown_device_quarantine_data_shed_total: int = Field(
        default=0,
        description=(
            "ACIL VERI DUSURME: retention suresi DOLMADAN, yalnizca kapasite "
            "tavanini korumak icin silinen kayit. >0 ise gercek veri kaybi "
            "olmustur; kapasite ya da replay ihmali incelenmelidir."
        ),
    )
    unknown_device_quarantine_capacity_rejected_total: int = Field(
        default=0,
        description=(
            "Yer ACILAMADIGI icin kabul edilmeyen olcum sayisi. Kontrollu yer "
            "acma devrede oldugu icin normal kapasite baskisinda ARTMAZ; >0 "
            "yapilandirma hatasidir (parti tavandan buyuk ya da silinebilecek "
            "pending kayit yok). Bu durumda mesajlar ack edilmez."
        ),
    )
    unknown_device_replay_success_total: int = Field(default=0)
    unknown_device_replay_failed_total: int = Field(default=0)
    source: str | None = Field(
        default=None,
        description=(
            "Kalicilastirmanin BESLENDIGI akis: 'normalized' hedef mimaridir "
            "(tag-engine cikisi), 'raw' ise gecis oncesi drenaj fazidir. "
            "Bu alan olmadan gecisin tamamlanip tamamlanmadigi ancak NATS "
            "CLI ile anlasilabilirdi."
        ),
    )
    backlog: int | None = Field(
        default=None,
        description=(
            "Tuketicinin ONUNDE bekleyen mesaj sayisi (JetStream num_pending). "
            "Surekli 0 civari beklenir; kalici buyume tuketicinin geride "
            "oldugunu ve tampon tasarsa VERI KAYBI baslayacagini gosterir."
        ),
    )
    throughput_msgs_per_sec: float = Field(
        ..., description="Son 60 saniyelik kayan ortalama islenmis mesaj/sn"
    )
    last_batch_size: int = 0
    last_batch_duration_sec: float = 0.0
    last_fetch_at: str | None = None
    processed_total: int = 0
    bad_total: int = Field(
        default=0, description="Parse/dogrulama hatasi alip DLQ'ya giden mesajlar"
    )
    reconnects: int = 0
    last_error: str | None = None
    backlog_warn_threshold: int = Field(
        ..., description="Bu degerin uzerinde uyari/olay kaydi uretilir"
    )
    severity: str = Field(..., description="ok | warning | critical")
    stages: PipelineStageQueues | None = Field(
        default=None,
        description=(
            "Asama bazli kuyruklar (NATS monitor'den). None = monitor'e "
            "ulasilamadi; rapor yine gecerlidir (fail-soft)."
        ),
    )


class PipelineStageQueues(BaseModel):
    """Boru hattinin HER asamasinin kendi kuyrugu.

    Tek bir 'bekleyen' sayisi iki farkli durumu ayirt edemiyordu: tag-engine
    birikimi eriyip persist kuyruguna dokulurken operator 'kuyruk kendi
    kendine artiyor' goruyordu — oysa toplam su azaliyordu. Asamalar ayri
    gosterilince ust kovanin alt kovaya bosaldigi GORUNUR olur.

    Degerler NATS monitor endpoint'inden (jsz) gelir ve ~5 sn onbelleklidir;
    hizlar sunucuda HESAPLANMAZ — istemci ardisik iki orneklemden turetir
    (last_seq farklari), boylece sunucu durumsuz kalir.
    """

    raw_pending: int | None = Field(
        default=None, description="tag-engine onunde bekleyen (RAW kuyrugu)"
    )
    normalized_prio_pending: int | None = Field(
        default=None, description="Oncelikli persist hatti bekleyeni (dijital/durum)"
    )
    normalized_bulk_pending: int | None = Field(
        default=None, description="Toplu persist hatti bekleyeni (analog)"
    )
    normalized_legacy_pending: int | None = Field(
        default=None,
        description="Gecis donemi tekil durable bekleyeni (hat ayrimi oncesi)",
    )
    alarm_prio_pending: int | None = None
    alarm_bulk_pending: int | None = None
    raw_last_seq: int | None = Field(
        default=None, description="RAW stream son sira no — istemci giris hizini turetir"
    )
    normalized_last_seq: int | None = Field(
        default=None,
        description="NORMALIZED stream son sira no — istemci normalize hizini turetir",
    )
    sampled_at: str | None = Field(default=None, description="Orneklem zamani (ISO)")


# `stages` alani PipelineStageQueues'u SINIFTAN ONCE referansliyor (rapor
# sinifi yukarida). `from __future__ import annotations` ile ad ancak burada
# cozulur; rebuild olmadan ilk instantiate "not fully defined" ile patlar.
TelemetryPipelineReport.model_rebuild()

#: NATS monitor orneklemi kisa sureli onbellekte tutulur: Sistem Durumu
#: sayfasi 10 sn'de bir sorgular, her sorguda monitor'e gitmek gereksiz.
_STAGE_CACHE_TTL_SEC = 5.0
_stage_cache: dict = {"ts": 0.0, "veri": None}


def parse_stage_queues(jsz: dict) -> PipelineStageQueues:
    """jsz cevabindan asama kuyruklarini ayikla (saf fonksiyon — test edilir)."""
    from datetime import datetime, timezone

    consumers: dict[str, int] = {}
    seqs: dict[str, int] = {}
    for hesap in jsz.get("account_details") or []:
        for stream in hesap.get("stream_detail") or []:
            durum = stream.get("state") or {}
            if stream.get("name"):
                seqs[stream["name"]] = int(durum.get("last_seq") or 0)
            for c in stream.get("consumer_detail") or []:
                if c.get("name"):
                    consumers[c["name"]] = int(c.get("num_pending") or 0)
    # tag-engine 2.45.x'te queue-group'lu durable'a gecti (…-q1); gecis
    # aninda ikisi de bulunabilir — toplanir. Yalnizca eski ada bakmak
    # panelde ham kuyrugu "—" gosteriyordu (sahada yasandi).
    tag_eski = consumers.get("tag-engine-normalize")
    tag_yeni = consumers.get("tag-engine-normalize-q1")
    raw_bekleyen = (
        None if tag_eski is None and tag_yeni is None
        else (tag_eski or 0) + (tag_yeni or 0)
    )
    return PipelineStageQueues(
        raw_pending=raw_bekleyen,
        normalized_prio_pending=consumers.get("telemetry-persist-prio-v1"),
        normalized_bulk_pending=consumers.get("telemetry-persist-bulk-v1"),
        normalized_legacy_pending=consumers.get("telemetry-persist-normalized-v3"),
        alarm_prio_pending=consumers.get("alarm-service-evaluator-prio"),
        alarm_bulk_pending=consumers.get("alarm-service-evaluator-bulk"),
        raw_last_seq=seqs.get(settings.nats_stream_telemetry_raw),
        normalized_last_seq=seqs.get(settings.nats_stream_telemetry_normalized),
        sampled_at=datetime.now(timezone.utc).isoformat(),
    )


def _fetch_stage_queues() -> PipelineStageQueues | None:
    """NATS monitor'den asama kuyruklari; ulasilamazsa None (fail-soft).

    Monitor'un yoklugu raporu DUSURMEZ: eski kurulumda port kapali olabilir,
    panel asamasiz ama calisir kalir.
    """
    simdi = time.monotonic()
    if simdi - _stage_cache["ts"] < _STAGE_CACHE_TTL_SEC:
        return _stage_cache["veri"]
    veri: PipelineStageQueues | None = None
    try:
        import requests

        resp = requests.get(
            f"{settings.nats_monitor_url.rstrip('/')}/jsz?consumers=true",
            timeout=2,
        )
        resp.raise_for_status()
        veri = parse_stage_queues(resp.json())
    except Exception:  # noqa: BLE001 — monitor yoklugu normal bir durum
        veri = None
    _stage_cache["ts"] = simdi
    _stage_cache["veri"] = veri
    return veri


@router.get("/telemetry-pipeline", response_model=TelemetryPipelineReport)
def get_telemetry_pipeline_status(
    request: Request = None,
    _: User = Depends(get_current_user),
):
    """Telemetri tuketicisi gelis hizina yetisiyor mu?

    `severity`:
      ok       — baglanti var, backlog esigin altinda
      warning  — backlog esigi asti: tuketici geride, tampon tasarsa veri kaybi
      critical — tuketici calismiyor veya NATS baglantisi yok: telemetri
                 DB'ye HIC yazilmiyor

    AYRIK KURULUM (`SERVICE_ROLE=api`): sayaclar SUREC ICIDIR ve tuketici bu
    surecte HIC acilmaz. Yerel `get_stats()` her zaman running=False doner,
    yani uc kalici ve SAHTE bir "critical" gosterirdi; backlog ise tamamen
    kaybolurdu. Bu yuzden gercegi tutan surece soruluyor.
    """
    from app.services import telemetry_consumer

    # Asama kuyruklari NATS monitor'den gelir ve surec rolunden BAGIMSIZDIR:
    # worker'a ulasilamasa bile kuyruklarin gercek durumu gosterilebilir.
    asamalar = _fetch_stage_queues()

    if not service_role.wants_background() and not _is_internal_probe(request):
        rapor, hata = _fetch_worker_pipeline(_forwarded_credentials(request))
        if rapor is not None:
            sonuc = TelemetryPipelineReport(**rapor)
            sonuc.stages = asamalar
            return sonuc
        # Worker'a ULASILAMIYORSA "critical" DOGRUDUR: arka plan sureci
        # gercekten kapali olabilir ve o durumda telemetri islenmiyordur.
        # Sahte yesil gostermek bu urunde en agir hata sinifi.
        return TelemetryPipelineReport(
            running=False,
            connected=False,
            backlog=None,
            throughput_msgs_per_sec=0.0,
            last_error=hata,
            backlog_warn_threshold=settings.telemetry_backlog_warn_threshold,
            severity="critical",
            stages=asamalar,
        )

    stats = telemetry_consumer.get_stats()
    backlog = stats.get("backlog")
    threshold = settings.telemetry_backlog_warn_threshold

    if not stats.get("running") or not stats.get("connected"):
        severity = "critical"
    elif backlog is not None and backlog >= threshold:
        severity = "warning"
    else:
        severity = "ok"

    return TelemetryPipelineReport(
        running=bool(stats.get("running")),
        connected=bool(stats.get("connected")),
        source=stats.get("source"),
        backlog=backlog,
        throughput_msgs_per_sec=float(stats.get("throughput_msgs_per_sec") or 0.0),
        last_batch_size=int(stats.get("last_batch_size") or 0),
        last_batch_duration_sec=float(stats.get("last_batch_duration_sec") or 0.0),
        last_fetch_at=stats.get("last_fetch_at"),
        processed_total=int(stats.get("processed_total") or 0),
        bad_total=int(stats.get("bad_total") or 0),
        reconnects=int(stats.get("reconnects") or 0),
        last_error=stats.get("last_error"),
        backlog_warn_threshold=threshold,
        severity=severity,
        stages=asamalar,
        **_karantina_sayaclari(),
    )


def _karantina_sayaclari() -> dict[str, int]:
    """Surec-ici karantina sayaclari. DB'ye DOKUNMAZ."""
    from app.services import unknown_device_quarantine as quarantine

    s = quarantine.get_stats()
    alanlar = (
        "unknown_device_quarantine_total",
        "unknown_device_quarantine_replayed_cleanup_total",
        "unknown_device_quarantine_expired_total",
        "unknown_device_quarantine_data_shed_total",
        "unknown_device_quarantine_capacity_rejected_total",
        "unknown_device_replay_success_total",
        "unknown_device_replay_failed_total",
    )
    return {ad: int(s.get(ad) or 0) for ad in alanlar}
