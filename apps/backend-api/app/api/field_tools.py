"""Saha araclari (field tools) API endpoint'leri.

Yetki: installer + engineer. Diagnostik amaclidir, kalici state degistirmez
(bu yuzden audit event kaydi yok); yine de ag kesfine acik olmamasi icin
operator/ops_manager'a KAPALI tutulur.

Endpoint'ler:
  POST /field-tools/ping        -> hedefe sistem uzerinden ICMP ping testi
  POST /field-tools/port-check  -> hedefte TCP portu acik mi (DNP3 20001 vb.)
  POST /field-tools/traceroute  -> hedefe giden rota (tracepath/tracert)
  POST /field-tools/dns         -> ad -> IP cozumleme testi
  POST /field-tools/scan        -> kayitli cihazlarda toplu ping + port testi
"""

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import require_roles
from app.db.session import get_db
from app.models.device import Device
from app.models.enums import UserRole
from app.schemas.field_tools import (
    DeviceScanResult,
    DnsRequest,
    DnsResult,
    PingRequest,
    PingResult,
    PortCheckRequest,
    PortCheckResult,
    ScanRequest,
    TracerouteRequest,
    TracerouteResult,
)
from app.services import field_tools_service
from app.services.field_tools_service import FieldToolsError

router = APIRouter(
    prefix="/field-tools",
    tags=["field-tools"],
    dependencies=[Depends(require_roles([UserRole.INSTALLER, UserRole.ENGINEER]))],
)

# Servis hata kodu -> kullanici mesaji. Kod da detail'de gider (frontend
# i18n dil bagimsiz eslesebilsin) — network.py'deki desenle ayni.
_ERROR_MESSAGES = {
    "invalid_host": "Gecersiz hedef: IP adresi veya hostname girin.",
    "ping_unavailable": "Ping araci bu sunucuda kurulu degil.",
    "ping_timeout": "Ping komutu zaman asimina ugradi.",
    "trace_unavailable": "Traceroute araci bu sunucuda kurulu degil.",
    "trace_timeout": "Traceroute zaman asimina ugradi.",
}

_ERROR_STATUS = {
    "invalid_host": status.HTTP_422_UNPROCESSABLE_ENTITY,
    "ping_unavailable": status.HTTP_503_SERVICE_UNAVAILABLE,
    "ping_timeout": status.HTTP_504_GATEWAY_TIMEOUT,
    "trace_unavailable": status.HTTP_503_SERVICE_UNAVAILABLE,
    "trace_timeout": status.HTTP_504_GATEWAY_TIMEOUT,
}


def _http_error(exc: FieldToolsError) -> HTTPException:
    return HTTPException(
        status_code=_ERROR_STATUS.get(exc.code, status.HTTP_400_BAD_REQUEST),
        detail={"code": exc.code, "message": _ERROR_MESSAGES.get(exc.code, exc.code)},
    )


@router.post("/ping", response_model=PingResult)
def ping(payload: PingRequest) -> PingResult:
    """Hedefe ICMP ping at, ozet + ham cikti dondur.

    Senkron def: FastAPI threadpool'da kosar, subprocess beklerken event
    loop'u BLOKLAMAZ (asagidaki endpoint'ler icin de gecerli).
    """
    try:
        return field_tools_service.ping_host(payload.host, payload.count)
    except FieldToolsError as exc:
        raise _http_error(exc) from exc


@router.post("/port-check", response_model=PortCheckResult)
def port_check(payload: PortCheckRequest) -> PortCheckResult:
    """Hedefte TCP portu acik mi (baglan-kapat; protokol verisi gonderilmez)."""
    try:
        return field_tools_service.check_port(payload.host, payload.port, payload.timeout_ms)
    except FieldToolsError as exc:
        raise _http_error(exc) from exc


@router.post("/traceroute", response_model=TracerouteResult)
def traceroute(payload: TracerouteRequest) -> TracerouteResult:
    """Hedefe giden rotayi izle; ham cikti doner (dakikaya yakin surebilir)."""
    try:
        return field_tools_service.traceroute_host(payload.host, payload.max_hops)
    except FieldToolsError as exc:
        raise _http_error(exc) from exc


@router.post("/dns", response_model=DnsResult)
def dns(payload: DnsRequest) -> DnsResult:
    """Ad -> IP cozumleme testi (sistem resolver'i ile)."""
    try:
        return field_tools_service.resolve_dns(payload.name)
    except FieldToolsError as exc:
        raise _http_error(exc) from exc


@router.post("/scan", response_model=list[DeviceScanResult])
def scan(payload: ScanRequest, db: Session = Depends(get_db)) -> list[DeviceScanResult]:
    """Verilen cihazlarda ping + DNP3 port testi (paralel).

    Frontend cihaz listesini <=50'lik parcalar halinde gonderir ve
    ilerlemeyi parca bazinda gosterir. Istekte olmayan/bilinmeyen id'ler
    sessizce atlanir (cihaz silinmis olabilir).
    """
    rows = (
        db.execute(select(Device).where(Device.id.in_(payload.device_ids)))
        .scalars()
        .all()
    )
    targets = [
        (device.id, device.ip_address or None, device.dnp3_outstation_port or 20001)
        for device in rows
    ]
    return field_tools_service.scan_targets(targets)
