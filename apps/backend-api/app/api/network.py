"""Appliance ag ayarlari (IP / DNS) API endpoint'leri.

Yetki: SADECE installer. Ag ayari yanlis girilirse cihaz kablolu agdan
erisilemez hale gelir; bu yuzden en dar rol.

Endpoint'ler:
  GET  /network/status   -> host ag durumu (arayuzler, AP, son uygulama)
  PUT  /network/config   -> ethernet IP/DNS ayarini uygula (+ yeniden baslat)

Backend host agina DOKUNMAZ: istegi /var/lib/e1-grid/net/request.json'a
yazar, host'ta root ile calisan `e1-netd` ajani dogrulayip nmcli ile uygular
(bkz. infra/appliance/). Bu yuzden 202 Accepted doner — islem asenkrondur ve
reboot sirasinda bu API zaten yanit veremez.
"""

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.api.deps import require_roles
from app.db.session import get_db
from app.models.enums import UserRole
from app.models.user import User
from app.schemas.network import (
    NetworkConfigAccepted,
    NetworkConfigUpdate,
    NetworkStatus,
)
from app.services import network_service
from app.services.event_service import record_event

router = APIRouter(
    prefix="/network",
    tags=["network"],
    dependencies=[Depends(require_roles([UserRole.INSTALLER]))],
)

# Servis hata kodu -> kullaniciya donen mesaj. Frontend i18n icin kodu da
# gonderiyoruz (detail alaninda), boylece dil bagimsiz eslesme yapilabilir.
_ERROR_MESSAGES = {
    "state_dir_missing": "Appliance modu kurulu degil (host ag ajani dizini yok).",
    "state_dir_not_writable": "Ag ajani dizinine yazilamiyor (izin hatasi).",
    "agent_never_reported": "Host ag ajani (e1-netd) henuz calismamis.",
    "request_pending": "Onceki ag ayari istegi hala isleniyor.",
    "interface_not_found": "Secilen ag arayuzu bulunamadi.",
    "interface_not_ethernet": "Sadece kablolu (ethernet) arayuz ayarlanabilir.",
}


@router.get("/status", response_model=NetworkStatus)
def get_network_status(
    _: User = Depends(require_roles([UserRole.INSTALLER])),
):
    """Host'un guncel ag durumu.

    Kaynak host ajaninin yazdigi state.json'dir; appliance modu kapaliysa
    `available=false` + sebep doner (hata degil — VPS kurulumunda normal).
    """
    return network_service.read_status()


@router.put(
    "/config",
    response_model=NetworkConfigAccepted,
    status_code=status.HTTP_202_ACCEPTED,
)
def update_network_config(
    payload: NetworkConfigUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_roles([UserRole.INSTALLER])),
):
    """Kablolu arayuzun IP/DNS ayarini degistir.

    202 Accepted: istek host ajanina kuyruklandi. `reboot=true` ise cihaz
    birkac saniye icinde yeniden baslar; bu yanit son cevaptir.
    """
    current = network_service.read_status()

    try:
        request_id = network_service.submit_request(payload, current_user.username)
    except network_service.NetworkRequestError as exc:
        code = str(exc)
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=_ERROR_MESSAGES.get(code, f"Ag ayari uygulanamadi: {code}"),
        ) from exc

    summary = (
        f"{payload.ifname} -> DHCP"
        if payload.method == "dhcp"
        else f"{payload.ifname} -> {payload.address}/{payload.prefix}"
        + (f" gw {payload.gateway}" if payload.gateway else "")
    )
    record_event(
        db,
        category="system",
        event_type="network_config_changed",
        severity="warning",
        actor_username=current_user.username,
        message=f"Ag ayari degistirildi: {summary}"
        + (" (cihaz yeniden baslatiliyor)" if payload.reboot else ""),
        metadata={
            "request_id": request_id,
            "ifname": payload.ifname,
            "method": payload.method,
            "address": payload.address,
            "prefix": payload.prefix,
            "gateway": payload.gateway,
            "dns": payload.dns,
            "reboot": payload.reboot,
        },
    )

    return NetworkConfigAccepted(
        request_id=request_id,
        reboot=payload.reboot,
        next_url=network_service.next_url_for(payload, current.mdns_name),
    )
