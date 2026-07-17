"""gateway_command_service.send_operate — mock gateway HTTP ile uctan uca.

Gercek bir gateway olmadan, kucuk bir stdlib HTTP sunucusu gateway'in
`POST /operate` sozlesmesini taklit eder. Bearer token, body ve yanit
parse yollari dogrulanir.
"""

from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from app.services import gateway_command_service as svc


class _FakeGateway:
    """Gateway ORM nesnesinin komut icin gereken minimal yuzeyi."""

    def __init__(self, host: str, port: int, token: str) -> None:
        self.code = "GW-TEST"
        self.control_host = host
        self.control_port = port
        self.command_token = token


def _make_server(handler_cls) -> tuple[HTTPServer, int]:
    server = HTTPServer(("127.0.0.1", 0), handler_cls)
    port = server.server_address[1]
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server, port


def test_send_operate_success() -> None:
    seen: dict = {}

    class H(BaseHTTPRequestHandler):
        def do_POST(self):  # noqa: N802
            seen["path"] = self.path
            seen["auth"] = self.headers.get("Authorization")
            length = int(self.headers.get("Content-Length", "0"))
            seen["body"] = json.loads(self.rfile.read(length))
            payload = json.dumps(
                {"result": {"ok": True, "status": "ok", "index": seen["body"]["index"]}}
            ).encode()
            self.send_response(200)
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def log_message(self, *a):  # noqa: A003
            pass

    server, port = _make_server(H)
    try:
        gw = _FakeGateway("127.0.0.1", port, "cmd-tok")
        result = svc.send_operate(gw, device_code="DEV-1", index=10)
        assert result["ok"] is True and result["status"] == "ok"
        assert seen["path"] == "/operate"
        assert seen["auth"] == "Bearer cmd-tok"
        assert seen["body"]["device_code"] == "DEV-1" and seen["body"]["index"] == 10
    finally:
        server.shutdown()


def test_send_operate_device_rejects_is_not_error() -> None:
    # Gateway 200 + ok=False (cihaz reddetti) -> exception DEGIL, dict doner.
    class H(BaseHTTPRequestHandler):
        def do_POST(self):  # noqa: N802
            self.rfile.read(int(self.headers.get("Content-Length", "0")))
            payload = json.dumps(
                {"result": {"ok": False, "status": "timeout", "error": "cevap yok"}}
            ).encode()
            self.send_response(200)
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def log_message(self, *a):  # noqa: A003
            pass

    server, port = _make_server(H)
    try:
        gw = _FakeGateway("127.0.0.1", port, "cmd-tok")
        result = svc.send_operate(gw, device_code="DEV-1", index=23)
        assert result["ok"] is False and result["status"] == "timeout"
    finally:
        server.shutdown()


def test_send_operate_http_error_raises() -> None:
    class H(BaseHTTPRequestHandler):
        def do_POST(self):  # noqa: N802
            self.rfile.read(int(self.headers.get("Content-Length", "0")))
            self.send_response(401)
            self.send_header("Content-Length", "0")
            self.end_headers()

        def log_message(self, *a):  # noqa: A003
            pass

    server, port = _make_server(H)
    try:
        gw = _FakeGateway("127.0.0.1", port, "wrong")
        with pytest.raises(svc.GatewayCommandError):
            svc.send_operate(gw, device_code="DEV-1", index=10)
    finally:
        server.shutdown()


def test_send_operate_missing_config_raises() -> None:
    # control_port=0 -> adres tanimli degil.
    gw = _FakeGateway("127.0.0.1", 0, "tok")
    with pytest.raises(svc.GatewayCommandError):
        svc.send_operate(gw, device_code="DEV-1", index=10)
    # token bos -> devre disi.
    gw2 = _FakeGateway("127.0.0.1", 9999, "")
    with pytest.raises(svc.GatewayCommandError):
        svc.send_operate(gw2, device_code="DEV-1", index=10)
