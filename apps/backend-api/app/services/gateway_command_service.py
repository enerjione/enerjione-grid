"""Gateway cihaz komutu (DNP3 CROB) HTTP proxy'si.

Backend, gateway'in kontrol adresine (`control_host:control_port`) `POST /operate`
istegi atarak tek bir cihaza binary output (CROB) komutu gonderir. Gateway bu
istegi Bearer `command_token` ile dogrular ve yadnp3 `DirectOperate` cagirir.

Iletim = anlik HTTP (refresh-all gibi DB-nonce polling DEGIL) cunku komut
gecikmeye tahammul etmez. stdlib `urllib.request` kullanilir; yeni bagimlilik
yok (bkz `outbound_dispatch_service._send_rest`).
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from typing import Any

from app.models.gateway import Gateway


# Gateway operate cevabi bu sureden uzun surerse timeout. Gateway kendi
# tarafinda DirectOperate icin ~10s bekliyor; biraz ustunde tut.
_OPERATE_TIMEOUT_SEC = 12.0


class GatewayCommandError(Exception):
    """Komut gateway'e ulastirilamadi (yapilandirma/ag/HTTP hatasi).

    Router bunu 400/502/503'e cevirir. `result.ok=False` (cihaz komutu
    reddetti) bir HATA DEGILDIR — o durumda dict normal doner, router
    result.ok'a bakip operator'a bildirir.
    """


def send_operate(
    gateway: Gateway,
    *,
    device_code: str,
    index: int,
    op_type: str = "pulse_on",
    count: int = 1,
    on_time_ms: int = 100,
    off_time_ms: int = 100,
) -> dict[str, Any]:
    """Gateway'e tek cihaz komutu gonderir, sonucu doner.

    Returns: gateway'in dondugu `result` dict'i, orn.
        {"ok": True, "status": "ok", "device_code": ..., "index": ..., ...}

    Raises GatewayCommandError: control adresi/token eksik, ag/HTTP/timeout
        hatasi, ya da gateway'den beklenmeyen govde.
    """
    host = (gateway.control_host or "").strip()
    port = int(gateway.control_port or 0)
    token = (gateway.command_token or "").strip()
    if not host or port <= 0:
        raise GatewayCommandError(
            f"Gateway {gateway.code} icin kontrol adresi (control_host/control_port) tanimli degil."
        )
    if not token:
        raise GatewayCommandError(
            f"Gateway {gateway.code} icin command_token tanimli degil; komut endpoint'i devre disi."
        )

    url = f"http://{host}:{port}/operate"
    body = json.dumps(
        {
            "device_code": device_code,
            "index": int(index),
            "op_type": op_type,
            "count": int(count),
            "on_time_ms": int(on_time_ms),
            "off_time_ms": int(off_time_ms),
            "timeout_sec": _OPERATE_TIMEOUT_SEC,
        }
    ).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=body,
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {token}",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=_OPERATE_TIMEOUT_SEC + 3) as resp:
            raw = resp.read().decode("utf-8")
    except urllib.error.HTTPError as exc:
        # Gateway 401/404/503 vb. dondu — govdeyi hataya ekle (secret icermez).
        detail = ""
        try:
            detail = exc.read().decode("utf-8")[:200]
        except Exception:  # noqa: BLE001
            pass
        raise GatewayCommandError(
            f"Gateway komutu reddetti (HTTP {exc.code}): {detail}"
        ) from exc
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise GatewayCommandError(
            f"Gateway'e ulasilamadi ({url}): {exc}"
        ) from exc

    try:
        parsed = json.loads(raw)
    except ValueError as exc:
        raise GatewayCommandError("Gateway yaniti JSON degil.") from exc
    result = parsed.get("result") if isinstance(parsed, dict) else None
    if not isinstance(result, dict):
        raise GatewayCommandError("Gateway yaniti beklenen 'result' alanini icermiyor.")
    return result
