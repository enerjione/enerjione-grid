"""Tek-endpointli /health HTTP server.

Yanit JSON'da kac IEC 104 server'i deploy edilmis, kac mesaj islenmis ve son
hata gozukur — operasyon ekibi icin temel saglik metrikleri.
"""

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
) -> HTTPServer:
    class _Handler(BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802
            if self.path != "/health":
                self.send_response(404)
                self.end_headers()
                return
            try:
                body = json.dumps(snapshot()).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
            except Exception as exc:
                msg = json.dumps({"status": "error", "error": str(exc)}).encode("utf-8")
                self.send_response(500)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.end_headers()
                self.wfile.write(msg)

        def log_message(self, format, *args):  # noqa: A003
            _ = format, args
            return

    server = HTTPServer((host, port), _Handler)
    Thread(target=server.serve_forever, name="iec104-health", daemon=True).start()
    return server
