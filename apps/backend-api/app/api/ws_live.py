"""Canli telemetry WebSocket endpoint'i.

Frontend `/ws/live-values` endpoint'ine baglandiktan sonra her telemetri
mesajini anlik olarak alir. Polling'e gore avantaj:
  - Cihaz->frontend gecikme: ~10sn (polling) -> ~200ms (WS push).
  - Backend yuku: 600 cihaz × frontend sayisi × 2sn polling yerine,
    600 cihaz × 1 broadcast.
  - DB load: live-values endpoint'i her polling'de tum cihazlar icin
    SELECT yapardi; WS ile sadece degisen sinyaller iletilir.

Auth:
  WebSocket connection sirasinda query param `?token=<bearer>` zorunlu.
  Token gecersizse close(code=1008, reason="invalid_token").

Mesaj formati (server -> client):
  {
    "type": "telemetry",
    "device_code": "...",
    "signal_key": "...",
    "value": 12.34,
    "value_string": null,
    "quality": "good",
    "source_timestamp": "...",
    "signal_data_type": "analog"
  }

Heartbeat:
  Server her 30sn bir `{"type": "ping"}` gonderir; client reply
  `{"type": "pong"}` zorunlu degil — connection alive olmasi yeterli.
"""
from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

from fastapi import APIRouter, Query, WebSocket, WebSocketDisconnect
from jose import JWTError, jwt

from app.core.config import settings
from app.services.ws_broadcaster import broadcaster

logger = logging.getLogger(__name__)

router = APIRouter(tags=["websocket"])


# Heartbeat suresi: client connection alive mi kontrol icin server'dan ping.
# 30sn fazla, 5sn cok az; 30sn dengeli.
_HEARTBEAT_INTERVAL_SEC = 30


def _validate_token(token: str | None) -> str | None:
    """JWT bearer token validate. Basariliysa username doner; aksi halde None."""
    if not token:
        return None
    try:
        payload = jwt.decode(token, settings.secret_key, algorithms=[settings.algorithm])
        username = payload.get("sub")
        if not username or not isinstance(username, str):
            return None
        return username
    except JWTError:
        return None


@router.websocket("/ws/live-values")
async def live_values_ws(
    websocket: WebSocket,
    token: str = Query(..., description="JWT access token (login response'undan)"),
    devices: str | None = Query(
        default=None,
        description="(Opsiyonel) Virgulle ayrilmis cihaz kodlari; sadece bu cihazlarin telemetrisi gelir. Bos = hepsi.",
    ),
):
    """Canli telemetry akisi. Authenticate edilmis user kendi scope'undaki
    tum cihazlarin (veya filter'a uygun olanlarin) anlik degerlerini alir."""

    username = _validate_token(token)
    if username is None:
        # WebSocket spec: 1008 = policy violation; 4401 custom code da kullanilabilir.
        await websocket.close(code=1008, reason="invalid_token")
        return

    # Cihaz filtresi: ?devices=DEV001,DEV002 -> sadece bu kodlar
    device_codes: set[str] | None = None
    if devices:
        device_codes = {c.strip() for c in devices.split(",") if c.strip()}
        if not device_codes:
            device_codes = None

    await websocket.accept()
    logger.info(
        "ws_live_values_connected user=%s filter_devices=%s",
        username,
        "all" if device_codes is None else len(device_codes),
    )

    loop = asyncio.get_running_loop()
    sub = broadcaster.subscribe(loop, device_codes=device_codes)

    # Heartbeat task: her 30sn ping gonderir; client disconnect olursa
    # send'de exception alip cikariz
    async def _heartbeat() -> None:
        try:
            while True:
                await asyncio.sleep(_HEARTBEAT_INTERVAL_SEC)
                await websocket.send_text('{"type":"ping"}')
        except (WebSocketDisconnect, asyncio.CancelledError, RuntimeError):
            return

    heartbeat_task = asyncio.create_task(_heartbeat())

    # Initial hello: client biliyor olsun bagli
    try:
        await websocket.send_json(
            {
                "type": "hello",
                "user": username,
                "filter": "all" if device_codes is None else sorted(device_codes),
            }
        )
    except Exception:  # noqa: BLE001
        broadcaster.unsubscribe(sub)
        heartbeat_task.cancel()
        return

    try:
        while True:
            payload: dict[str, Any] = await sub.queue.get()
            # payload zaten _persist_message'in calistirdigi telemetry mesaji
            # (gateway'den gelen TelemetryIn formatti). Frontend'e "type":"telemetry"
            # ile gonder ki client mesaj tipini ayirt edebilsin.
            event = {"type": "telemetry", **payload}
            await websocket.send_text(json.dumps(event, ensure_ascii=False))
    except WebSocketDisconnect:
        logger.info("ws_live_values_disconnected user=%s", username)
    except Exception as exc:  # noqa: BLE001
        logger.warning("ws_live_values_error user=%s error=%s", username, exc)
        try:
            await websocket.close(code=1011, reason="server_error")
        except Exception:  # noqa: BLE001
            pass
    finally:
        broadcaster.unsubscribe(sub)
        heartbeat_task.cancel()
