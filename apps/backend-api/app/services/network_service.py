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

import json
import os
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

from app.core.config import settings
from app.schemas.network import (
    AccessPointInfo,
    NetworkApplyStatus,
    NetworkConfigUpdate,
    NetworkInterface,
    NetworkStatus,
    WifiConnectRequest,
    WifiNetwork,
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
    wifi = WifiState(**wifi_raw) if isinstance(wifi_raw, dict) else WifiState()

    status_raw = _read_json(state_dir() / STATUS_FILE)
    last_apply = NetworkApplyStatus(**status_raw) if status_raw else None

    age: float | None = None
    try:
        age = max(0.0, time.time() - (state_dir() / STATE_FILE).stat().st_mtime)
    except OSError:
        age = None

    return NetworkStatus(
        available=True,
        reason="state_stale" if (age is not None and age > STALE_STATE_SECONDS) else None,
        hostname=raw.get("hostname"),
        mdns_name=raw.get("mdns_name"),
        updated_at=raw.get("updated_at"),
        state_age_seconds=age,
        ap=ap,
        wifi=wifi,
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


def next_url_for(payload: NetworkConfigUpdate, mdns_name: str | None) -> str | None:
    """Reboot sonrasi kullanicinin acmasi gereken adres.

    Statikte yeni IP kesindir. DHCP'de adres onceden bilinemez; mDNS adi
    (e1-grid.local) her iki durumda da calisir, onu oneriyoruz.
    """
    if payload.method == "static" and payload.address:
        return f"http://{payload.address}/"
    if mdns_name:
        return f"http://{mdns_name}/"
    return None
