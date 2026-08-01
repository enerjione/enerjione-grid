"""Gateway kurulum ajani servisi — host ajani (e1-gwd) ile dosya koprusu.

Mimari (network_service.py ile ayni desen):

    UI  --HTTP-->  backend (container, uid 10001)
                       |  request.json yazar  (TEK yetki)
                       v
              /var/lib/e1-grid/gw/   <-- bind mount (root:10001, 0770)
                       ^
                       |  state.json / status.json yazar, request'i uygular
              e1-gwd (host, root, systemd path unit ile tetiklenir)

Backend Docker daemon'a ERISMEZ. Container'a /var/run/docker.sock vermek
host'ta root vermekle esdegerdir ve compose'daki cap_drop / read_only /
no-new-privileges sertlestirmesini anlamsizlastirirdi.

PROTOKOL: backend compose GOVDESI GONDERMEZ — yalnizca kucuk bir skaler
parametre kumesi (imaj, token, URL'ler, portlar) gonderir. compose YAML'ini
ajan kendi sabit sablonundan uretir ve her parametreyi kati bir regex'ten
gecirir. Boylece "backend ele gecirilirse ne olur" sorusunun cevabi
"hicbir sey": ajan calistirilabilir bir metin kabul etmiyor.

(Eskiden compose govdesi gonderiliyor ve ajan bunu bir regex KARA LISTESI ile
suzuyordu. Kara liste YAML'in uzun-form bind, named-volume driver_opts,
`security_opt: unconfined`, `build.context` gibi varyantlari karsisinda
asilabilirdi; bkz. e1-gwd.py basindaki aciklama.)

Ajan kurulu degilse (VPS'te setup-gateway-agent.sh calistirilmamis, veya
dizin yok) fonksiyonlar hata firlatmaz; `read_status()` available=False +
sebep doner ve UI "bu cihaza kur" secenegini kapali gosterir. "Baska cihaza
kur" akisi bundan etkilenmez.
"""

from __future__ import annotations

import json
import os
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

from app.core.config import settings
from app.schemas.gateway_agent import (
    GatewayAgentStatus,
    GatewayApplyStatus,
    LocalGateway,
)

STATE_FILE = "state.json"
STATUS_FILE = "status.json"
REQUEST_FILE = "request.json"

# state.json bu suredan eskiyse ajan durmus/timer kapali demektir.
# Timer 30 sn'de bir yazar; 180 sn tolerans agir yuk altinda bile yeterli.
STALE_STATE_SECONDS = 180


def state_dir() -> Path:
    return Path(settings.gateway_state_dir)


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
        # Dizin var ama ajan hic rapor yazmamis — setup-gateway-agent.sh
        # calistirilmamis veya e1-gwd-report.timer kapali.
        return False, "agent_never_reported"
    return True, None


def has_pending_request() -> bool:
    return (state_dir() / REQUEST_FILE).exists()


def read_status() -> GatewayAgentStatus:
    available, reason = availability()
    if not available:
        return GatewayAgentStatus(available=False, reason=reason)

    raw = _read_json(state_dir() / STATE_FILE) or {}
    gateways = [
        LocalGateway(**item)
        for item in raw.get("gateways", [])
        if isinstance(item, dict) and item.get("code")
    ]

    status_raw = _read_json(state_dir() / STATUS_FILE)
    last_apply = GatewayApplyStatus(**status_raw) if status_raw else None

    age: float | None = None
    try:
        age = max(0.0, time.time() - (state_dir() / STATE_FILE).stat().st_mtime)
    except OSError:
        age = None

    return GatewayAgentStatus(
        available=True,
        reason="state_stale" if (age is not None and age > STALE_STATE_SECONDS) else None,
        docker_available=bool(raw.get("docker_available")),
        updated_at=raw.get("updated_at"),
        state_age_seconds=age,
        gateways=gateways,
        pending=has_pending_request(),
        last_apply=last_apply,
    )


def is_installed_locally(code: str) -> bool:
    return any(gw.code == code for gw in read_status().gateways)


class GatewayAgentError(Exception):
    """Istek kabul edilemedi (ajan yok, kuyruk dolu, yazilamadi...)."""


def _write_request(body: dict) -> str:
    """request.json'i atomik yaz (tmp + rename) ve request id dondur.

    Dosya izni 0600: compose govdesi gateway token'i icerir, kisa sure de
    olsa diskte durur; sadece root ve backend gorebilsin. Ajan isledikten
    sonra siler ve arsive compose'suz kopyayi yazar (bkz. e1-gwd
    `_archive_request`).
    """
    available, reason = availability()
    if not available:
        raise GatewayAgentError(reason or "unavailable")
    if has_pending_request():
        # Ust uste istek: onceki daha uygulanmadan ikincisi gelirse hangisinin
        # gecerli oldugu belirsizlesir.
        raise GatewayAgentError("request_pending")

    target = state_dir() / REQUEST_FILE
    tmp = state_dir() / f"{REQUEST_FILE}.tmp"
    try:
        # Once 0600 ile olustur, sonra icerigi yaz — token hicbir an genis
        # izinli bir dosyada bulunmasin.
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
        raise GatewayAgentError(f"write_failed: {exc}") from exc
    return str(body["id"])


def _base_request(action: str, code: str, actor_username: str) -> dict:
    return {
        "id": uuid.uuid4().hex,
        "action": action,
        "code": code,
        "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "requested_by": actor_username,
    }


def request_install(
    code: str,
    name: str,
    actor_username: str,
    *,
    image: str,
    token: str,
    backend_url: str,
    nats_url: str,
    host_port: int,
    app_environment: str,
    initiating_port_base: int,
    initiating_port_count: int,
) -> str:
    """Gateway'i bu cihaza kur.

    compose GOVDESI GONDERILMEZ; ajan asagidaki parametrelerden kendi
    sablonuyla uretir (bkz. infra/appliance/e1-gwd.py `render_compose`).
    Anahtar isimleri ajanin ALLOWED_PARAM_KEYS listesiyle birebir eslesmeli —
    ajan bilinmeyen anahtari reddeder.
    """
    body = _base_request("install", code, actor_username)
    body["name"] = name
    body["params"] = {
        "image": image,
        "token": token,
        "backend_url": backend_url,
        "nats_url": nats_url,
        "host_port": host_port,
        "app_environment": app_environment,
        "initiating_port_base": initiating_port_base,
        "initiating_port_count": initiating_port_count,
    }
    return _write_request(body)


def request_remove(code: str, actor_username: str) -> str:
    """Gateway container'ini durdur ve compose dizinini sil."""
    return _write_request(_base_request("remove", code, actor_username))


def request_restart(code: str, actor_username: str) -> str:
    return _write_request(_base_request("restart", code, actor_username))


def request_update(code: str, actor_username: str) -> str:
    """Yeni imaji cekip container'i yeniden olustur.

    `restart`ten FARKI: restart AYNI imajla yeniden baslatir, bu once `pull`
    yapar. Ajan cekme basarisiz olursa container'a DOKUNMAZ — yarim bir
    guncelleme yerine calisan eski surumde kalir.
    """
    return _write_request(_base_request("update", code, actor_username))
