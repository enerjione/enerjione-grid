"""HTTP /health — FTP sunucusu canli mi + basit metrikler."""

from __future__ import annotations

import json
from http.server import BaseHTTPRequestHandler, HTTPServer
from threading import Thread
from typing import Callable


def start_health_server(
    *,
    host: str,
    port: int,
    snapshot: Callable[[], dict],
    is_healthy: Callable[[], bool] | None = None,
) -> HTTPServer:
    """`is_healthy` verilmezse uc HER ZAMAN 200 doner (eski davranis).

    ONCEDEN SABIT 200 DONUYORDU. Saglik sunucusu AYRI bir iplikte oldugu
    icin FTP ioloop'u kilitlense de 200 doner ve container "saglikli"
    gorunurdu — yani tam da yakalamasi gereken arizayi yakalamiyordu.
    `is_healthy` verildiginde takilma 503 olarak bildirilir.
    """
    class _Handler(BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802
            if self.path != "/health":
                self.send_response(404)
                self.end_headers()
                return
            try:
                canli = True if is_healthy is None else bool(is_healthy())
                govde = snapshot()
                if not canli:
                    govde = {**govde, "status": "stalled"}
                body = json.dumps(govde).encode("utf-8")
                self.send_response(200 if canli else 503)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
            except Exception as exc:  # noqa: BLE001
                msg = json.dumps({"status": "error", "error": str(exc)}).encode("utf-8")
                self.send_response(500)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.end_headers()
                self.wfile.write(msg)

        def log_message(self, format, *args):  # noqa: A003
            _ = format, args
            return

    server = HTTPServer((host, port), _Handler)
    Thread(target=server.serve_forever, name="ftp-health", daemon=True).start()
    return server
