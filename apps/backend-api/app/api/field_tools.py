"""Saha araclari (field tools) API endpoint'leri.

Yetki: installer + engineer. Diagnostik amaclidir, kalici state degistirmez
(bu yuzden audit event kaydi yok); yine de ag kesfine acik olmamasi icin
operator/ops_manager'a KAPALI tutulur.

Endpoint'ler:
  POST /field-tools/ping  -> hedef IP/hostname'e mini PC'den ICMP ping testi
"""

from fastapi import APIRouter, Depends, HTTPException, status

from app.api.deps import require_roles
from app.models.enums import UserRole
from app.schemas.field_tools import PingRequest, PingResult
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
}

_ERROR_STATUS = {
    "invalid_host": status.HTTP_422_UNPROCESSABLE_ENTITY,
    "ping_unavailable": status.HTTP_503_SERVICE_UNAVAILABLE,
    "ping_timeout": status.HTTP_504_GATEWAY_TIMEOUT,
}


@router.post("/ping", response_model=PingResult)
def ping(payload: PingRequest) -> PingResult:
    """Hedefe ICMP ping at, ozet + ham cikti dondur.

    Senkron def: FastAPI threadpool'da kosar, subprocess beklerken event
    loop'u BLOKLAMAZ.
    """
    try:
        return field_tools_service.ping_host(payload.host, payload.count)
    except FieldToolsError as exc:
        raise HTTPException(
            status_code=_ERROR_STATUS.get(exc.code, status.HTTP_400_BAD_REQUEST),
            detail={"code": exc.code, "message": _ERROR_MESSAGES.get(exc.code, exc.code)},
        ) from exc
