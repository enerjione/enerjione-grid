"""Uzaktan bakim izni (Tailscale kapisi) API endpoint'leri.

Endpoint'ler:
  GET  /remote-access/status   -> ajan + tailnet durumu, acik izin, son oturum
  POST /remote-access/grant    -> SURELI izin ver     (202, asenkron)
  POST /remote-access/revoke   -> izni hemen geri al  (202, asenkron)

YETKI
------
  okuma (status)  : engineer, installer, ops_manager
  izin ver/geri al: engineer, installer

`installer` DAHIL — bu bilincli bir urun karari (proje sahibi: "installer
tum yetkilere sahip birisi"). Sonucu acikca bilinsin: bu ozellik URETICIYE
KARSI bir "musteri onayi" garantisi DEGILDIR; installer kendi kendine izin
acabilir. Ayrica installer kullanici yonetimine sahip oldugu icin kendine
engineer hesabi da acabilirdi — yani engineer'a kisitlamak zaten kagit
uzerinde kalan bir kontroldu.

Ozelligin KORUDUGU degerler bunlar:
  * erisim VARSAYILAN KAPALI (eskiden surekli aciktik)
  * her izin SURELIDIR ve kendiliginden kapanir (acik unutulmaz)
  * kim, ne zaman, ne kadar sureyle actigi DENETIME yazilir
  * musteri (engineer) istedigi an tek tikla geri alabilir

Rol kontrolu HIYERARSIK DEGILDIR: `require_roles` tam-eslesme yapar
(`app/api/deps.py`: `if user.role not in roles`). Yani asagidaki liste
"engineer ve ustu" demek DEGIL; her rol acikca yazilmalidir. `ops_manager`
ve `operator` izin VEREMEZ.

Acil durumda (musteri kullanicisi ulasilamiyor, izin acik unutulmus) kapatma
host tarafinda yapilir:  sudo /opt/enerjione-grid/infra/appliance/e1-rad.py close

MIMARI
------
Backend tailscale'i CALISTIRMAZ; istegi /var/lib/e1-grid/remote/request.json
dosyasina yazar, host'ta root ile calisan `e1-rad` ajani dogrulayip uygular
(bkz. infra/appliance/). Bu yuzden 202 Accepted doner — islem asenkrondur.

SURE SAYACI HOST'TADIR: son tarih ajanin lease dosyasinda MUTLAK zaman olarak
durur ve 30 sn'lik bir systemd timer'i uygular. Backend, DB ve container
tamamen kapali olsa bile izin kendiliginden kapanir.
"""

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy.orm import Session

from app.api.deps import require_roles
from app.core.client_ip import client_ip_from_request
from app.db.session import get_db
from app.models.enums import UserRole
from app.models.user import User
from app.schemas.remote_access import (
    RemoteAccessAccepted,
    RemoteAccessGrantRequest,
    RemoteAccessRevokeRequest,
    RemoteAccessStatus,
)
from app.services import remote_access_service
from app.services.event_service import record_event
from app.services.remote_access_service import RemoteAccessError

# Durumu GORMEK daha genis: installer sahayi teshis edebilmeli, ops_manager
# kimin ne zamana kadar izin verdigini gorebilmeli. Gormek ACMAK degildir.
_VIEW_ROLES = [UserRole.ENGINEER, UserRole.INSTALLER, UserRole.OPS_MANAGER]
# Izin verme/geri alma. Bkz. dosya basi: installer bilincli olarak DAHIL.
# Liste hiyerarsik degildir; yeni bir rol eklenecekse buraya ACIKCA yazilir.
_GRANT_ROLES = [UserRole.ENGINEER, UserRole.INSTALLER]

router = APIRouter(
    prefix="/remote-access",
    tags=["remote-access"],
    dependencies=[Depends(require_roles(_VIEW_ROLES))],
)

# Servis hata kodu -> HTTP durumu. Listede olmayan kod 503 olur (ajan/host
# tarafi kullanilamiyor). Mesaj metni frontend'de i18n ile uretilir; backend
# Turkce metin SABITLEMEZ (gateways.py'nin yapisal hata stili).
_ERROR_STATUS = {
    "request_pending": status.HTTP_409_CONFLICT,
    "tailscale_key_expired": status.HTTP_409_CONFLICT,
    "duration_exceeds_site_limit": status.HTTP_400_BAD_REQUEST,
    "duration_below_minimum": status.HTTP_400_BAD_REQUEST,
}


def _http_error(exc: RemoteAccessError) -> HTTPException:
    code = str(exc)
    return HTTPException(
        status_code=_ERROR_STATUS.get(code, status.HTTP_503_SERVICE_UNAVAILABLE),
        detail={"code": code, "message": code},
    )


@router.get("/status", response_model=RemoteAccessStatus)
def get_remote_access_status(
    db: Session = Depends(get_db),
    current_user: User = Depends(require_roles(_VIEW_ROLES)),
) -> RemoteAccessStatus:
    """Uzaktan bakim izninin guncel durumu.

    HER ZAMAN 200 doner. Ajan kurulu degilse `available=false` + sebep gelir
    (VPS kurulumunda NORMAL, hata degil).

    Bu GET denetim uzlastirmasi da yapar: ajanin kendi kapattigi oturumlari
    (sure doldu / saat tutarsiz) system_events'e tasir. Idempotenttir ve cogu
    cagrida hicbir sey yazmaz.
    """
    remote_access_service.reconcile_audit(db)
    result = remote_access_service.read_status()
    # UI "Izin ver" butonunu buna gore gizler; asil kontrol yine de _GRANT_ROLES.
    result.can_grant = current_user.role in _GRANT_ROLES
    return result


@router.post(
    "/grant",
    response_model=RemoteAccessAccepted,
    status_code=status.HTTP_202_ACCEPTED,
)
def grant_remote_access(
    payload: RemoteAccessGrantRequest,
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_roles(_GRANT_ROLES)),
) -> RemoteAccessAccepted:
    """Uzaktan bakim icin SURELI izin ver (engineer veya installer).

    202 Accepted: istek host ajanina kuyruklandi. Kesin son tarihi ajan kendi
    saatinden hesaplar; yanittaki `expires_at` TAHMINDIR.

    Acikken tekrar izin verilirse sure TOPLANMAZ, DEGISIR: yeni son tarih
    `simdi + sure` olur. Aksi halde ust uste basarak tavan asilabilirdi.
    """
    before = remote_access_service.read_status()
    was_open = bool(before.access.open)
    actor_ip = client_ip_from_request(request)

    try:
        request_id, estimated = remote_access_service.request_grant(
            duration_minutes=payload.duration_minutes,
            actor_username=current_user.username,
            actor_role=current_user.role.value,
            actor_ip=actor_ip,
            reason=payload.reason,
            allow_ssh=payload.allow_ssh,
        )
    except RemoteAccessError as exc:
        raise _http_error(exc) from exc

    event_type = "remote_access_extended" if was_open else "remote_access_granted"
    record_event(
        db,
        category="security",
        event_type=event_type,
        # `warning`: disariya kapi acmak rutin bir islem degil, denetimde
        # gorunur olmali.
        severity="warning",
        actor_username=current_user.username,
        message=(
            f"Uzaktan bakim izni {'uzatildi' if was_open else 'verildi'}: "
            f"{payload.duration_minutes} dk (bitis ~{estimated})"
        ),
        metadata={
            "request_id": request_id,
            "duration_minutes": payload.duration_minutes,
            "expires_at": estimated,
            "reason": payload.reason,
            "allow_ssh": payload.allow_ssh,
            # Kim onay verdi — hukuki/sozlesmesel olarak anlamli kayit budur.
            "actor_role": current_user.role.value,
            "actor_ip": actor_ip,
            "tailnet_hostname": before.tailscale.hostname,
            "previous_expires_at": before.access.expires_at if was_open else None,
        },
        i18n_key=event_type,
        i18n_params={"minutes": payload.duration_minutes},
    )
    db.commit()

    return RemoteAccessAccepted(
        request_id=request_id, action="grant", expires_at=estimated
    )


@router.post(
    "/revoke",
    response_model=RemoteAccessAccepted,
    status_code=status.HTTP_202_ACCEPTED,
)
def revoke_remote_access(
    payload: RemoteAccessRevokeRequest,
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_roles(_GRANT_ROLES)),
) -> RemoteAccessAccepted:
    """Uzaktan bakim iznini HEMEN geri al (engineer veya installer).

    Kapali olsa bile istek yazilir ve olay kaydedilir: (a) yakinsama garantisi
    — state.json bayatsa gercekten kapali oldugu dogrulanir, (b) "kapat"
    dugmesine basilmasi niyet olarak denetime degerdir.

    Bekleyen bir "ac" istegi varsa EZILIR (bkz. servis notu).
    """
    before = remote_access_service.read_status()
    actor_ip = client_ip_from_request(request)

    try:
        request_id = remote_access_service.request_revoke(
            actor_username=current_user.username,
            actor_role=current_user.role.value,
            actor_ip=actor_ip,
            reason=payload.reason,
        )
    except RemoteAccessError as exc:
        raise _http_error(exc) from exc

    record_event(
        db,
        category="security",
        event_type="remote_access_revoked",
        severity="warning",
        actor_username=current_user.username,
        message="Uzaktan bakim izni geri alindi.",
        metadata={
            "request_id": request_id,
            "actor_role": current_user.role.value,
            "actor_ip": actor_ip,
            "reason": payload.reason,
            "was_open": bool(before.access.open),
            # "Ne kadar erken kapatildi" bilgisi denetimde degerli.
            "remaining_seconds_at_revoke": before.access.remaining_seconds,
            "session_id": before.access.session_id,
        },
        i18n_key="remote_access_revoked",
    )
    db.commit()

    return RemoteAccessAccepted(request_id=request_id, action="revoke", expires_at=None)
