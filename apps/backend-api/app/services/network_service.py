"""Appliance ag ayarlari servisi — host ajani (e1-netd) ile dosya kopruSu.

Mimari:
    UI  --HTTP-->  backend (container, uid 10001)
                       |  request.json yazar  (TEK yetki)
                       v
              /var/lib/e1-grid/net/   <-- bind mount (root:10001, 0770)
                       ^
                       |  state.json / status.json yazar, request'i uygular
              e1-netd (host, root, systemd path unit ile tetiklenir)

Backend hicbir zaman nmcli/ip/systemctl CALISTIRMAZ; host agina erisimi
yoktur. Ajan gelen istegi kendi kurallariyla yeniden dogrular.

Appliance modu kapaliysa (VPS kurulumu, dizin yok/yazilamiyor) fonksiyonlar
hata firlatmaz; `read_status()` available=False + sebep doner ve UI bunu
"bu kurulum appliance degil" olarak gosterir.
"""

from __future__ import annotations

import ipaddress
import json
import os
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

from app.core.config import settings
from app.schemas.network import (
    AccessPointInfo,
    InternetState,
    NetworkApplyStatus,
    NetworkConfigUpdate,
    NetworkInterface,
    NetworkStatus,
    WifiConnectRequest,
    WifiNetwork,
    WifiRadioState,
    WifiRoleState,
    WifiScanResult,
    WifiState,
)

STATE_FILE = "state.json"
STATUS_FILE = "status.json"
REQUEST_FILE = "request.json"
# WiFi tarama sonucu ajan tarafindan buraya yazilir (state.json'i sismesin).
SCAN_FILE = "wifi-scan.json"

# state.json bu suredan eskiyse ajan durmus/timer kapali demektir.
STALE_STATE_SECONDS = 180


def state_dir() -> Path:
    return Path(settings.network_state_dir)


def _read_json(path: Path) -> dict | None:
    try:
        with path.open("r", encoding="utf-8") as fh:
            data = json.load(fh)
        return data if isinstance(data, dict) else None
    except (FileNotFoundError, json.JSONDecodeError, OSError, PermissionError):
        return None


def _model(model_cls, raw):
    """Ajanin yazdigi blogu modele cevir; blok yok/bozuksa varsayilan.

    Ajan ile backend ayri ayri guncellenebilir (eski state.json + yeni
    backend). Bilinmeyen/eksik alan 500 uretmemeli.
    """
    if not isinstance(raw, dict):
        return model_cls()
    try:
        return model_cls(**raw)
    except (TypeError, ValueError):
        return model_cls()


def _model_or_none(model_cls, raw):
    """Blok YOKSA None dondur — "olcum yok" ile "olcum negatif"i AYIRIR.

    NEDEN: `_model` blok yokken bos bir nesne uretiyordu (supported=False).
    Ajan `build_state()` icinde hata alip kisa bir hata state'i yazdiginda
    (nmcli zaman asimi, NetworkManager cevap vermiyor) radio/role/internet
    bloklari HIC olmuyor; bos nesne yuzunden arayuz "Cihazda WiFi karti
    bulunamadi" diyordu. Bu OLCULMUS bir donanim iddiasi, oysa tek
    bildigimiz ajanin cevap veremedigi. UI None gorunce "Bilinmiyor" der.
    """
    if not isinstance(raw, dict):
        return None
    try:
        return model_cls(**raw)
    except (TypeError, ValueError):
        return None


def availability() -> tuple[bool, str | None]:
    """(kullanilabilir_mi, sebep). Sebep sadece kapali durumda dolu."""
    directory = state_dir()
    if not directory.is_dir():
        return False, "state_dir_missing"
    if not os.access(directory, os.W_OK):
        return False, "state_dir_not_writable"
    if not (directory / STATE_FILE).exists():
        # Dizin var ama ajan hic rapor yazmamis — setup-appliance.sh
        # calistirilmamis veya e1-netd-report.timer kapali.
        return False, "agent_never_reported"
    return True, None


def has_pending_request() -> bool:
    return (state_dir() / REQUEST_FILE).exists()


def read_status() -> NetworkStatus:
    available, reason = availability()
    if not available:
        return NetworkStatus(available=False, reason=reason)

    raw = _read_json(state_dir() / STATE_FILE) or {}
    interfaces = [
        NetworkInterface(**item)
        for item in raw.get("interfaces", [])
        if isinstance(item, dict) and item.get("ifname")
    ]
    ap_raw = raw.get("ap")
    ap = AccessPointInfo(**ap_raw) if isinstance(ap_raw, dict) else AccessPointInfo()
    wifi_raw = raw.get("wifi")
    # Eski ajan surumu (schema 1) `wifi` alanini yazmaz — bos state ile devam.
    wifi = _model_or_none(WifiState, wifi_raw)
    # schema 3 bloklari. Eski ajan bunlari yazmaz; varsayilanla devam eder ve
    # UI `agent_schema`ya bakip "ag ajani eski, guncelleyin" der (varsayilani
    # olcum gibi gosterip yalan soylemek YASAK).
    radio = _model_or_none(WifiRadioState, raw.get("radio"))
    role = _model_or_none(WifiRoleState, raw.get("wifi_role"))
    internet = _model_or_none(InternetState, raw.get("internet"))

    status_raw = _read_json(state_dir() / STATUS_FILE)
    last_apply = NetworkApplyStatus(**status_raw) if status_raw else None

    age: float | None = None
    try:
        age = max(0.0, time.time() - (state_dir() / STATE_FILE).stat().st_mtime)
    except OSError:
        age = None

    agent_schema = raw.get("schema")
    return NetworkStatus(
        available=True,
        reason="state_stale" if (age is not None and age > STALE_STATE_SECONDS) else None,
        hostname=raw.get("hostname"),
        mdns_name=raw.get("mdns_name"),
        updated_at=raw.get("updated_at"),
        state_age_seconds=age,
        agent_schema=agent_schema if isinstance(agent_schema, int) else None,
        ap=ap,
        wifi=wifi,
        radio=radio,
        role=role,
        internet=internet,
        interfaces=interfaces,
        pending=has_pending_request(),
        last_apply=last_apply,
    )


def find_interface(ifname: str) -> NetworkInterface | None:
    for iface in read_status().interfaces:
        if iface.ifname == ifname:
            return iface
    return None


class NetworkRequestError(Exception):
    """Istek kabul edilemedi (appliance kapali, arayuz yok, kuyruk dolu...)."""


def submit_request(payload: NetworkConfigUpdate, actor_username: str) -> str:
    """Ag ayari istegini ajanin izledigi dosyaya yaz. Request id doner.

    Dosya once .tmp'ye yazilip rename edilir; systemd path unit'i yarim
    dosyayi okumasin. Ajan uyguladiktan sonra request.json'i siler.
    """
    available, reason = availability()
    if not available:
        raise NetworkRequestError(reason or "unavailable")

    if has_pending_request():
        # Ust uste istek: onceki daha uygulanmadan ikincisi gelirse hangisinin
        # gecerli oldugu belirsizlesir.
        raise NetworkRequestError("request_pending")

    iface = find_interface(payload.ifname)
    if iface is None:
        raise NetworkRequestError("interface_not_found")
    if iface.type != "ethernet":
        # WiFi AP kullanicinin geri donus yolu; ona dokunulmasina izin yok.
        raise NetworkRequestError("interface_not_ethernet")

    request_id = uuid.uuid4().hex
    body = {
        "id": request_id,
        "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "requested_by": actor_username,
        "ifname": payload.ifname,
        "method": payload.method,
        "reboot": payload.reboot,
    }
    if payload.method == "static":
        body.update(
            {
                "address": payload.address,
                "prefix": payload.prefix or 24,
                "gateway": payload.gateway,
                "dns": payload.dns,
            }
        )

    target = state_dir() / REQUEST_FILE
    tmp = state_dir() / f"{REQUEST_FILE}.tmp"
    try:
        with tmp.open("w", encoding="utf-8") as fh:
            json.dump(body, fh, ensure_ascii=False, indent=2)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, target)
    except OSError as exc:
        try:
            tmp.unlink(missing_ok=True)
        except OSError:
            pass
        raise NetworkRequestError(f"write_failed: {exc}") from exc

    return request_id


# ---------------------------------------------------------------- WiFi ---
def _write_request(body: dict) -> str:
    """request.json'i atomik yaz (tmp + rename) ve request id dondur.

    Dosya izni 0600: WiFi sifresi kisa sure de olsa diskte durur, sadece
    yazan/okuyan surecler gorebilsin. Ajan isledikten sonra siler ve
    arsive maskelenmis kopyayi yazar (bkz. e1-netd `_redact`).
    """
    available, reason = availability()
    if not available:
        raise NetworkRequestError(reason or "unavailable")
    if has_pending_request():
        raise NetworkRequestError("request_pending")

    target = state_dir() / REQUEST_FILE
    tmp = state_dir() / f"{REQUEST_FILE}.tmp"
    try:
        # Once 0600 ile olustur, sonra icerigi yaz — sifre hicbir an
        # genis izinli bir dosyada bulunmasin.
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(body, fh, ensure_ascii=False, indent=2)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, target)
    except OSError as exc:
        try:
            tmp.unlink(missing_ok=True)
        except OSError:
            pass
        raise NetworkRequestError(f"write_failed: {exc}") from exc
    return str(body["id"])


def _base_request(action: str, actor_username: str) -> dict:
    return {
        "id": uuid.uuid4().hex,
        "action": action,
        "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "requested_by": actor_username,
    }


def _require_radio_on() -> None:
    """WiFi eylemleri icin fiziksel onkosul: kart acik olmali.

    ERKEN UYARI: nihai otorite yine ajandir (ayni kurallari bagimsiz
    uygular). Amac, kullaniciya "istek gonderildi" deyip 20 sn sonra
    sessizce basarisiz olmak yerine sebebi HEMEN soylemek.
    """
    status = read_status()
    if status.agent_schema is None or status.agent_schema < 3:
        return  # eski ajan radyo durumunu olcmuyor; engelleme, ona birak
    if not status.radio.supported:
        raise NetworkRequestError("wifi_not_supported")
    if not status.radio.enabled:
        raise NetworkRequestError("radio_off")


def has_wired_fallback() -> bool:
    """WiFi kapatilirsa cihaza ulasilacak IKINCI yol var mi?

    Kriter ajandakiyle AYNI olmali (bagli + gercek IPv4 almis ethernet);
    ayrisirlarsa kullanici burada "olur" cevabi alip ajandan ret yer.
    """
    for iface in read_status().interfaces:
        if iface.type != "ethernet":
            continue
        if (iface.state or "").lower() not in ("connected", "connected (site only)"):
            continue
        for cidr in iface.addresses:
            text = cidr.split("/")[0].strip()
            try:
                addr = ipaddress.IPv4Address(text)
            except ipaddress.AddressValueError:
                continue
            if not (addr.is_loopback or addr.is_link_local or addr.is_unspecified):
                return True
    return False


def request_wifi_scan(deep: bool, actor_username: str) -> str:
    """Ajana 'aglari tara' de. Sonuc wifi-scan.json'a yazilir.

    `deep=True` cihazin kendi agini ~15 sn indirir; tek radyo AP modunda
    kanal degistiremedigi icin normal tarama bos donebilir.
    """
    _require_radio_on()
    body = _base_request("wifi_scan", actor_username)
    body["deep"] = bool(deep)
    return _write_request(body)


def request_wifi_connect(payload: WifiConnectRequest, actor_username: str) -> str:
    """Bir aga baglanma istegi kuyruga al.

    Sifre burada SAKLANMAZ: sadece request.json'a yazilir, ajan uygulayip
    dosyayi siler. Log'a da dusurulmez.
    """
    _require_radio_on()
    body = _base_request("wifi_connect", actor_username)
    body["ssid"] = payload.ssid
    if payload.psk:
        body["psk"] = payload.psk
    return _write_request(body)


def request_wifi_forget(actor_username: str) -> str:
    """Kayitli WiFi profilini sil ve AP'yi geri ac."""
    return _write_request(_base_request("wifi_forget", actor_username))


def request_wifi_radio(enabled: bool, actor_username: str) -> str:
    """WiFi kartini ac/kapa.

    KAPATMA ENGELLENMEZ, KOSULLANIR: tek radyo kapaninca erisim noktasi da
    duser, yani cihaza kablosuz ulasim tamamen biter. Bu yuzden IP almis
    bagli bir ethernet sart. Kosul saglansa bile AP uzerinden bagli
    kullanicinin oturumu KOPAR — UI onay istemeli.
    """
    status = read_status()
    if status.agent_schema is not None and status.agent_schema >= 3:
        if not status.radio.supported:
            raise NetworkRequestError("wifi_not_supported")
        if enabled and not status.radio.hardware_enabled:
            raise NetworkRequestError("radio_hardware_blocked")
    if not enabled and not has_wired_fallback():
        raise NetworkRequestError("radio_off_would_strand")
    body = _base_request("wifi_radio", actor_username)
    body["enabled"] = bool(enabled)
    return _write_request(body)


def request_wifi_mode(mode: str, actor_username: str) -> str:
    """WiFi kartinin gorevini sec: "ap" (kendi agini yayinla) | "client".

    "client" KAYITLI profili kullanir; yeni ag secmek ayri akistir.
    """
    if mode not in ("ap", "client"):
        raise NetworkRequestError("invalid_mode")
    _require_radio_on()
    if mode == "client" and not read_status().wifi.saved:
        raise NetworkRequestError("no_saved_network")
    body = _base_request("wifi_mode", actor_username)
    body["mode"] = mode
    return _write_request(body)


def request_internet_check(actor_username: str) -> str:
    """Internet durumunu SIMDI sina (tek seferlik, kullanici istegi).

    Periyodik rapor bu kontrolu YAPMAZ: ag trafigi uretir ve bloklar.
    """
    return _write_request(_base_request("net_check", actor_username))


def read_scan() -> WifiScanResult:
    """Ajanin yazdigi son tarama sonucunu oku."""
    available, _reason = availability()
    if not available:
        return WifiScanResult(available=False)
    raw = _read_json(state_dir() / SCAN_FILE)
    if not raw:
        return WifiScanResult(available=True)
    networks = [
        WifiNetwork(**item)
        for item in raw.get("networks", [])
        if isinstance(item, dict) and item.get("ssid")
    ]
    age: float | None = None
    try:
        age = max(0.0, time.time() - (state_dir() / SCAN_FILE).stat().st_mtime)
    except OSError:
        age = None
    return WifiScanResult(
        available=True,
        updated_at=raw.get("updated_at"),
        ifname=raw.get("ifname"),
        networks=networks,
        age_seconds=age,
        deep=bool(raw.get("deep")),
        ap_was_active=bool(raw.get("ap_was_active")),
    )


def next_url_for(payload: NetworkConfigUpdate, mdns_name: str | None) -> str | None:
    """Reboot sonrasi kullanicinin acmasi gereken adres.

    Statikte yeni IP kesindir. DHCP'de adres onceden bilinemez; mDNS adi
    (enerjione.local) her iki durumda da calisir, onu oneriyoruz.
    """
    if payload.method == "static" and payload.address:
        return f"http://{payload.address}/"
    if mdns_name:
        return f"http://{mdns_name}/"
    return None
