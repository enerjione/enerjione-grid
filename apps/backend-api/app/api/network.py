"""Appliance ag ayarlari (IP / DNS) API endpoint'leri.

Yetki: SADECE installer. Ag ayari yanlis girilirse cihaz kablolu agdan
erisilemez hale gelir; bu yuzden en dar rol.

Endpoint'ler:
  GET  /network/status         -> host ag durumu (arayuzler, AP, WiFi, radyo,
                                  gorev, internet, son uygulama)
  PUT  /network/config         -> ethernet IP/DNS ayarini uygula (+ yeniden baslat)
  GET  /network/wifi/scan      -> son tarama sonucu
  POST /network/wifi/scan      -> yeni tarama tetikle (deep=true: AP'yi indirir)
  POST /network/wifi/connect   -> bir aga baglan (SSID + sifre)
  POST /network/wifi/forget    -> kayitli agi unut, AP'yi geri ac
  POST /network/wifi/radio     -> WiFi kartini ac/kapa
  POST /network/wifi/mode      -> kartin gorevi: erisim noktasi | internete baglan
  POST /network/internet/check -> internet durumunu simdi sina

WiFi NOTU (tek radyo): appliance'ta tek WiFi karti var; AP ile client AYNI
ANDA calisamaz. Bu yuzden kullaniciya uc AYRI sey raporlanir ve hicbiri
digerinin yerine gecmez:
  status.radio    -> kart acik mi (yazilim/DONANIM kilidi ayri ayri)
  status.role     -> `mode` KALICI TERCIH, `effective` ise OLCUM. Bu ikisi
                     UI'da ayni rozete BAGLANMAMALI; eskiden "client yoksa AP
                     acik olmali" KURALI olcum gibi gosteriliyordu ve AP
                     gercekte kapaliyken "acik" yaziyordu.
  status.internet -> internet var/yok + hangi arayuzden ("AP acik" ile ayni
                     sey DEGIL: guncelleme/uzaktan bakim/saat senkronu buna
                     bagli).

DEGISMEZ: cihaz erisilemez kalmaz. Client baglantisi kurulamazsa ajan muhafiz
suresi (varsayilan 180 sn) sonunda AP'yi otomatik geri acar; WiFi'yi kapatmak
yalnizca IP almis bir ethernet varken kabul edilir; radyo kapaliyken kablo da
giderse ajan WiFi'yi kendiliginden geri acar. AP'nin kendi ayarlari bu
API'den DEGISTIRILEMEZ.

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
    WifiConnectRequest,
    WifiForgetRequest,
    WifiModeRequest,
    WifiRadioRequest,
    WifiScanRequest,
    WifiScanResult,
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
    "radio_off_would_strand": (
        "WiFi kapatilirsa cihaza ulasilacak baska yol kalmiyor. "
        "Once kablolu baglantiyi kurun."
    ),
    "radio_hardware_blocked": (
        "Cihaz uzerindeki WiFi anahtari kapali; WiFi arayuzden acilamaz."
    ),
    "wifi_not_supported": "Bu cihazda WiFi karti bulunamadi.",
    "no_saved_network": (
        "Kayitli bir WiFi agi yok. Once listeden bir ag secip baglanin."
    ),
    "radio_off": "WiFi karti kapali. Once WiFi'yi acin.",
    "invalid_mode": "Gecersiz gorev secimi.",
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


# --------------------------------------------------------------- WiFi ---
def _queue(fn, *args) -> str:
    """Ajan istegi kuyrukla; servis hatasini 409'a cevir."""
    try:
        return fn(*args)
    except network_service.NetworkRequestError as exc:
        code = str(exc)
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=_ERROR_MESSAGES.get(code, f"Islem uygulanamadi: {code}"),
        ) from exc


@router.get("/wifi/scan", response_model=WifiScanResult)
def get_wifi_scan(
    _: User = Depends(require_roles([UserRole.INSTALLER])),
):
    """Host ajaninin yazdigi SON tarama sonucu (yeni tarama tetiklemez)."""
    return network_service.read_scan()


@router.post("/wifi/scan", status_code=status.HTTP_202_ACCEPTED)
def trigger_wifi_scan(
    payload: WifiScanRequest | None = None,
    current_user: User = Depends(require_roles([UserRole.INSTALLER])),
):
    """Yeni tarama tetikle. Sonuc birkac saniye icinde GET /wifi/scan'de.

    `deep=true`: cihazin kendi agi ~15 sn kapatilir. Tek radyo AP modunda
    kanal degistiremedigi icin normal tarama cogu surucude bos doner ve
    kullanici baglanacak agi SECEMEZ. Bedeli: AP uzerinden bagli olanin
    sayfasi kisa sureligine acilmaz — UI bunu onceden soylemeli.
    """
    request_id = _queue(
        network_service.request_wifi_scan,
        bool(payload.deep) if payload else False,
        current_user.username,
    )
    return {"request_id": request_id}


@router.post("/wifi/connect", status_code=status.HTTP_202_ACCEPTED)
def connect_wifi(
    payload: WifiConnectRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_roles([UserRole.INSTALLER])),
):
    """Appliance'i bir WiFi agina baglar.

    DIKKAT (tek radyo): baglanti sirasinda AP duser. Basarisiz olursa host
    ajani muhafiz suresi sonunda AP'yi otomatik geri acar — cihaz erisilemez
    kalmaz.
    """
    request_id = _queue(network_service.request_wifi_connect, payload, current_user.username)
    # Olay kaydi: SSID yazilir, SIFRE ASLA yazilmaz.
    record_event(
        db,
        category="system",
        event_type="wifi_connect_requested",
        severity="warning",
        actor_username=current_user.username,
        message=f"WiFi baglantisi istendi: {payload.ssid} (AP gecici olarak duser)",
        metadata={"request_id": request_id, "ssid": payload.ssid},
    )
    return {"request_id": request_id}


@router.post("/wifi/forget", status_code=status.HTTP_202_ACCEPTED)
def forget_wifi(
    payload: WifiForgetRequest | None = None,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_roles([UserRole.INSTALLER])),
):
    """Agi unutur.

    `ssid` verilirse yalnizca o ag bilinen aglar listesinden silinir (aktif
    baglanti ve AP etkilenmez). Verilmezse aktif profil silinir, AP geri acilir.
    """
    ssid = payload.ssid if payload else None
    request_id = _queue(
        network_service.request_wifi_forget, current_user.username, ssid
    )
    record_event(
        db,
        category="system",
        event_type="wifi_forgotten",
        severity="info",
        actor_username=current_user.username,
        message=(
            f"Bilinen WiFi agi unutuldu: {ssid}"
            if ssid
            else "WiFi baglantisi kaldirildi, erisim noktasi (AP) geri acildi."
        ),
        metadata={"request_id": request_id, "ssid": ssid},
    )
    return {"request_id": request_id}


@router.post("/wifi/radio", status_code=status.HTTP_202_ACCEPTED)
def set_wifi_radio(
    payload: WifiRadioRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_roles([UserRole.INSTALLER])),
):
    """WiFi kartini ac/kapa (fiziksel onkosul).

    KAPATMA: tek radyo kapaninca erisim noktasi da duser; cihaza kablosuz
    ulasim tamamen biter. Bu yuzden yalnizca IP almis bagli bir ethernet
    varsa kabul edilir (409 aksi halde). Kosul saglansa bile AP uzerinden
    bagli kullanicinin oturumu KOPAR.

    ACMA: donanim anahtari kapaliysa yazilimla acilamaz — bu da 409.
    """
    request_id = _queue(
        network_service.request_wifi_radio, payload.enabled, current_user.username
    )
    record_event(
        db,
        category="system",
        event_type="wifi_radio_changed",
        severity="info" if payload.enabled else "warning",
        actor_username=current_user.username,
        message=(
            "WiFi karti acildi."
            if payload.enabled
            else "WiFi karti kapatildi (cihaza yalnizca kablolu adresten ulasilir)."
        ),
        metadata={"request_id": request_id, "enabled": payload.enabled},
    )
    return {"request_id": request_id}


@router.post("/wifi/mode", status_code=status.HTTP_202_ACCEPTED)
def set_wifi_mode(
    payload: WifiModeRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_roles([UserRole.INSTALLER])),
):
    """WiFi kartinin gorevini sec (tek radyo, ikisi ayni anda olmaz).

    "ap"     -> cihaz kendi agini yayinlar. Ulasim garantisi, INTERNET YOK.
    "client" -> KAYITLI aga katilir, internet gelir, AP kapanir. Baglanti
                kurulamazsa ajan AP'yi otomatik geri acar ve ag geri
                geldiginde yeniden dener.

    NOT: otomatik dongu calisan bir client baglantisini asla dusurmez; bu
    ACIK bir istektir ve UI kullaniciyi uyarmis olmalidir.
    """
    request_id = _queue(
        network_service.request_wifi_mode, payload.mode, current_user.username
    )
    record_event(
        db,
        category="system",
        event_type="wifi_mode_changed",
        severity="warning",
        actor_username=current_user.username,
        message=(
            "WiFi gorevi degistirildi: cihaz kendi agini yayinlayacak "
            "(internet baglantisi kesilir)."
            if payload.mode == "ap"
            else "WiFi gorevi degistirildi: kayitli aga baglanilacak "
            "(cihazin kendi agi kapanir)."
        ),
        metadata={"request_id": request_id, "mode": payload.mode},
    )
    return {"request_id": request_id}


@router.post("/internet/check", status_code=status.HTTP_202_ACCEPTED)
def check_internet(
    current_user: User = Depends(require_roles([UserRole.INSTALLER])),
):
    """Internet durumunu SIMDI sina.

    Periyodik rapor bunu yapmaz (ag trafigi uretir ve bloklar); sonuc
    birkac saniye icinde GET /network/status -> internet alaninda.
    Kalici bir state degisimi olmadigi icin audit kaydi da yok.
    """
    request_id = _queue(network_service.request_internet_check, current_user.username)
    return {"request_id": request_id}
