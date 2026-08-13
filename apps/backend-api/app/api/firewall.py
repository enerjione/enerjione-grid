"""Guvenlik duvari (host firewall) API endpoint'leri.

Endpoint'ler:
  GET /firewall/status   -> ajan + duvar durumu, mevcut yapilandirma
  PUT /firewall/config   -> istenen yapilandirmanin TAMAMINI uygula (202, asenkron)

YETKI
------
  okuma (status)      : installer
  yapilandirma (PUT)  : installer

YALNIZCA INSTALLER — GORME DE DAHIL.
Once okuma engineer + ops_manager'a da acikti ("gormek degistirmek degildir"),
yapilandirma engineer'a acikti. Ikisi de daraltildi:

  * Guvenlik duvari kurallari sahadaki mini PC'nin AG YUZEYIDIR — hangi
    portun disariya acik oldugu, hangi adresin gecebildigi. Bu liste tek
    basina bir kesif haritasi; "sadece bakma" yetkisi diye dagitilacak bir
    bilgi degil. Ag Ayarlari zaten yalnizca installer'da ve ayni siniftan
    bir yetki.
  * Degistirme tarafinda kilitlenme korumasi ajanda sabit (22/80/443 +
    uzaktan bakim tuneli hep acik), yani cihaz erisilemez hale gelmez; ama
    yanlis kural saha telemetrisini (NATS 4222) kesebilir ve bunu fark
    etmek dakikalar alir. Kurulumu yapan kisi bu sorumlulugu tasir.

Rol daraltmasi UC yerde birden yasar: burasi, `EngineeringNav.canSee` ve
`tabModel` rol listeleri. Biri unutulursa menu gorunur ama sayfa 403 alir.

Rol kontrolu HIYERARSIK DEGILDIR: `require_roles` tam-eslesme yapar
(app/api/deps.py). Yeni rol eklenirse listeye ACIKCA yazilir.

Acil durumda (yanlis kural, arayuze erisim var ama duzeltilemiyor) kapatma
host tarafinda: sudo /opt/enerjione-grid/infra/appliance/e1-fwd.py disable

MIMARI
------
Backend iptables CALISTIRMAZ; istegi /var/lib/e1-grid/fw/request.json
dosyasina yazar, host'ta root ile calisan `e1-fwd` ajani dogrulayip uygular
(bkz. infra/appliance/). Bu yuzden 202 Accepted doner — islem asenkrondur ve
sonuc bir sonraki `GET /status` yoklamasinda gorunur.

KALICILIK HOST'TADIR: yapilandirma ajanin config dosyasinda durur ve 60 sn'lik
systemd timer'i reboot/`iptables -F` sonrasi geri kurar. Backend, DB ve
container tamamen kapali olsa bile duvar ayakta kalir.
"""

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy.orm import Session

from app.api.deps import require_roles
from app.core.client_ip import client_ip_from_request
from app.db.session import get_db
from app.models.enums import UserRole
from app.models.user import User
from app.schemas.firewall import (
    FirewallConfig,
    FirewallConfigAccepted,
    FirewallStatus,
)
from app.services import firewall_service
from app.services.event_service import record_event
from app.services.firewall_service import FirewallError

# Gorme de yapilandirma da YALNIZCA installer (gerekce dosya basinda).
# Iki liste ayni ama AYRI durmaya devam ediyor: `can_manage` bayragi ve
# ileride gorme/degistirme tekrar ayrilirsa tek satirlik degisiklik olsun.
_VIEW_ROLES = [UserRole.INSTALLER]
_MANAGE_ROLES = [UserRole.INSTALLER]

router = APIRouter(
    prefix="/firewall",
    tags=["firewall"],
    dependencies=[Depends(require_roles(_VIEW_ROLES))],
)

# Servis hata kodu -> HTTP durumu. Listede olmayan kod 503 olur (ajan/host
# tarafi kullanilamiyor). Mesaj metni frontend'de i18n ile uretilir; backend
# Turkce metin SABITLEMEZ (remote_access.py'nin yapisal hata stili).
_ERROR_STATUS = {
    "request_pending": status.HTTP_409_CONFLICT,
    "iptables_missing": status.HTTP_409_CONFLICT,
}


def _http_error(exc: FirewallError) -> HTTPException:
    code = str(exc)
    return HTTPException(
        status_code=_ERROR_STATUS.get(code, status.HTTP_503_SERVICE_UNAVAILABLE),
        detail={"code": code, "message": code},
    )


@router.get("/status", response_model=FirewallStatus)
def get_firewall_status(
    current_user: User = Depends(require_roles(_VIEW_ROLES)),
) -> FirewallStatus:
    """Guvenlik duvarinin guncel durumu.

    HER ZAMAN 200 doner. Ajan kurulu degilse `available=false` + sebep gelir
    (setup-firewall-agent.sh calistirilmamis eski kurulumda NORMAL, hata
    degil). `active` OLCULEN degerdir: ajan kurallarin sahada gercekten
    kurulu oldugunu imza kuralindan dogrular.
    """
    result = firewall_service.read_status()
    # UI kaydet/ac butonlarini buna gore gizler; asil kontrol yine _MANAGE_ROLES.
    result.can_manage = current_user.role in _MANAGE_ROLES
    return result


@router.put(
    "/config",
    response_model=FirewallConfigAccepted,
    status_code=status.HTTP_202_ACCEPTED,
)
def put_firewall_config(
    payload: FirewallConfig,
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_roles(_MANAGE_ROLES)),
) -> FirewallConfigAccepted:
    """Istenen yapilandirmanin TAMAMINI uygula (engineer veya installer).

    202 Accepted: istek host ajanina kuyruklandi. Artimli degisiklik yok —
    her PUT tum kural/yonlendirme listesini tasir, ajan atomik degistirir.

    Kilitlenme korumasi AJANDADIR: 22/80/443 ve uzaktan bakim tuneli her
    zaman acik kalir; buradan gecen hicbir kural onlari kapatamaz.
    """
    before = firewall_service.read_status()
    actor_ip = client_ip_from_request(request)

    try:
        request_id = firewall_service.request_set_config(
            payload,
            actor_username=current_user.username,
            actor_role=current_user.role.value,
            actor_ip=actor_ip,
        )
    except FirewallError as exc:
        raise _http_error(exc) from exc

    # Denetim: ac/kapat ayri olay tipi (guvenlik denetiminde ilk bakilan sey),
    # kural degisikligi ucuncu tip. Duvara dokunmak rutin is degil -> warning.
    if payload.enabled and not before.enabled:
        event_type = "firewall_enabled"
    elif not payload.enabled and before.enabled:
        event_type = "firewall_disabled"
    else:
        event_type = "firewall_config_changed"

    record_event(
        db,
        category="security",
        event_type=event_type,
        severity="warning",
        actor_username=current_user.username,
        message=(
            f"Guvenlik duvari {'acildi' if payload.enabled else 'kapatildi'}: "
            f"{len(payload.rules)} kural, {len(payload.forwards)} yonlendirme"
            if event_type != "firewall_config_changed"
            else (
                "Guvenlik duvari kurallari guncellendi: "
                f"{len(payload.rules)} kural, {len(payload.forwards)} yonlendirme"
            )
        ),
        metadata={
            "request_id": request_id,
            "enabled": payload.enabled,
            "rule_count": len(payload.rules),
            "forward_count": len(payload.forwards),
            # Denetimde "ne degisti" sorusuna cevap: tam listeler kucuktur
            # (<=50/<=20) ve metadata'ya sigar; diff'i okuyan hesaplar.
            "rules": [rule.model_dump() for rule in payload.rules],
            "forwards": [fwd.model_dump() for fwd in payload.forwards],
            "was_enabled": before.enabled,
            "actor_role": current_user.role.value,
            "actor_ip": actor_ip,
        },
        i18n_key=event_type,
        i18n_params={
            "rules": len(payload.rules),
            "forwards": len(payload.forwards),
        },
    )
    db.commit()

    return FirewallConfigAccepted(request_id=request_id)
