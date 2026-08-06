"""Guvenlik duvari servisi — host ajani (e1-fwd) ile dosya koprusu.

Mimari (network_service / remote_access_service ile BIREBIR ayni desen):

    UI  --HTTP-->  backend (container, uid 10001)
                       |  request.json yazar  (TEK yetki)
                       v
          /var/lib/e1-grid/fw/       <-- bind mount (root:10001, 0770)
                       ^
                       |  state.json / status.json yazar, istegi uygular
          e1-fwd (host, root, systemd path unit + 60 sn timer)

Backend iptables'i HICBIR ZAMAN calistirmaz. Ajan gelen istegi kendi
kurallariyla yeniden dogrular; kilitlenme korumasi portlari (22/80/443 +
uzaktan bakim tuneli) ajanda SABITTIR ve buradan gecen hicbir istek onlari
kapatamaz.

NEDEN YENI DB MODELI YOK (remote_access_service ile ayni gerekce)
-----------------------------------------------------------------
* Yetkili yapilandirma ajanin config dosyasindadir (root:root 0700) — zorlayici
  host ajani oldugu icin gercek de oradadir. DB'deki kopya yalnizca "tavsiye"
  olur ve iki kaynakli gercek (split-brain) dogururdu.
* Denetim izi: mevcut `system_events` tablosu yeterli (`category=security`).

Ajan kurulu degilse (eski kurulum, setup calistirilmamis) fonksiyonlar hata
firlatmaz; `read_status()` available=False + sebep doner ve UI bunu "bu
kurulumda guvenlik duvari modulu yok" olarak gosterir.
"""

from __future__ import annotations

import json
import os
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

from app.core.config import settings
from app.schemas.firewall import (
    FirewallApplyStatus,
    FirewallConfig,
    FirewallStatus,
)

STATE_FILE = "state.json"
STATUS_FILE = "status.json"
REQUEST_FILE = "request.json"

# state.json bu sureden eskiyse ajan durmus/timer kapali demektir. Ajan 60
# sn'de bir yazar. Timer durmussa reboot sonrasi duvar GERI KURULMAZ —
# kullanici "acik" sanirken portlar acik kalir; UI kirmizi uyari gosterir.
STALE_STATE_SECONDS = 300


class FirewallError(Exception):
    """Istek kabul edilemedi. Mesaj = makine-okunur HATA KODU (i18n frontend'de)."""


def state_dir() -> Path:
    return Path(settings.firewall_state_dir)


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
        # Dizin var ama ajan hic rapor yazmamis — setup-firewall-agent.sh
        # calistirilmamis veya e1-fwd-report.timer kapali.
        return False, "agent_never_reported"
    return True, None


def has_pending_request() -> bool:
    return (state_dir() / REQUEST_FILE).exists()


def read_status() -> FirewallStatus:
    available, reason = availability()
    if not available:
        return FirewallStatus(available=False, reason=reason)

    raw = _read_json(state_dir() / STATE_FILE) or {}

    config: FirewallConfig | None = None
    config_raw = raw.get("config")
    if isinstance(config_raw, dict):
        try:
            config = FirewallConfig(**config_raw)
        except ValueError:
            # Ajan surumu ile sema uyusmuyor — durumu yine de gosterelim,
            # yapilandirma bolumu UI'da bos gorunur.
            config = None

    status_raw = _read_json(state_dir() / STATUS_FILE)
    last_apply = FirewallApplyStatus(**status_raw) if status_raw else None

    age: float | None = None
    try:
        age = max(0.0, time.time() - (state_dir() / STATE_FILE).stat().st_mtime)
    except OSError:
        age = None

    return FirewallStatus(
        available=True,
        reason="state_stale" if (age is not None and age > STALE_STATE_SECONDS) else None,
        agent_reason=raw.get("reason"),
        updated_at=raw.get("updated_at"),
        state_age_seconds=age,
        iptables=bool(raw.get("iptables")),
        ipv6=bool(raw.get("ipv6")),
        enabled=bool(raw.get("enabled")),
        # OLCULEN deger — ajan imza kuralini sahada gercekten gordu mu.
        active=bool(raw.get("active")),
        mismatch=raw.get("mismatch"),
        config=config,
        changed_by=raw.get("changed_by"),
        changed_at=raw.get("changed_at"),
        guard_tcp_ports=[
            int(p) for p in (raw.get("guard_tcp_ports") or []) if isinstance(p, int)
        ],
        reserved_listen_ports=[
            int(p)
            for p in (raw.get("reserved_listen_ports") or [])
            if isinstance(p, int)
        ],
        max_rules=int(raw.get("max_rules") or 50),
        max_forwards=int(raw.get("max_forwards") or 20),
        pending=has_pending_request(),
        last_apply=last_apply,
    )


# ---------------------------------------------------------------- istek ---
def _write_request(body: dict) -> str:
    """request.json'i atomik yaz (tmp + rename) ve request id dondur.

    Bekleyen istek varsa reddedilir: her istek yapilandirmanin TAMAMINI
    tasidigi icin ust uste iki istek "hangisi gecerli" belirsizligi yaratir;
    kullanici bir sonraki yoklamada guncel durumu gorup tekrar kaydeder.

    Dosya izni 0600: sir icermez ama gereksiz genis izin de vermeyiz.
    """
    available, reason = availability()
    if not available:
        raise FirewallError(reason or "unavailable")
    if has_pending_request():
        raise FirewallError("request_pending")

    target = state_dir() / REQUEST_FILE
    tmp = state_dir() / f"{REQUEST_FILE}.tmp"
    try:
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
        raise FirewallError(f"write_failed: {exc}") from exc
    return str(body["id"])


def request_set_config(
    config: FirewallConfig,
    *,
    actor_username: str,
    actor_role: str | None,
    actor_ip: str | None = None,
    reason: str | None = None,
) -> str:
    """Istenen yapilandirmayi kuyrukla. request_id doner.

    On kosullar burada ERKEN UYARI icin kontrol edilir; nihai otorite ajandir
    (ayni sinirlari bagimsiz uygular, kilitlenme korumasini o ekler).
    """
    current = read_status()
    if config.enabled and not current.iptables:
        # "Dugmeye bastim, hicbir sey olmadi" sessiz basarisizligini onle:
        # host'ta iptables yoksa istek hic yazilmaz.
        raise FirewallError("iptables_missing")

    body = {
        "id": uuid.uuid4().hex,
        "action": "set_config",
        "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "requested_by": actor_username,
        "requested_by_role": actor_role,
        "requested_ip": actor_ip,
        "reason": reason,
        "config": config.model_dump(),
    }
    return _write_request(body)
