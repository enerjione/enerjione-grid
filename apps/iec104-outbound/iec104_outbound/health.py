"""HTTP server: /health + /runtime/<target_id>.

/health         — toplam saglik metrikleri (target sayisi, mesaj sayaci vs.)
/runtime/<id>   — backend-api'nin sorabilecegi tek-target runtime durumu:
                  {server_running, connected_clients}. Frontend'in
                  'Bagli SCADA' rozetinin canli olabilmesi icin bu kritik —
                  cunku gercek IEC 104 TCP server'lari backend'de degil bu
                  serviste calisir.
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
    runtime_for: Callable[[int], dict] | None = None,
) -> HTTPServer:
    class _Handler(BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802
            try:
                if self.path == "/health":
                    body = json.dumps(snapshot()).encode("utf-8")
                    self.send_response(200)
                    self.send_header("Content-Type", "application/json; charset=utf-8")
                    self.send_header("Content-Length", str(len(body)))
                    self.end_headers()
                    self.wfile.write(body)
                    return
                if self.path.startswith("/runtime/") and runtime_for is not None:
                    raw = self.path.split("/runtime/", 1)[1]
                    try:
                        target_id = int(raw)
                    except ValueError:
                        self.send_response(400)
                        self.end_headers()
                        return
                    body = json.dumps(runtime_for(target_id)).encode("utf-8")
                    self.send_response(200)
                    self.send_header("Content-Type", "application/json; charset=utf-8")
                    self.send_header("Content-Length", str(len(body)))
                    self.end_headers()
                    self.wfile.write(body)
                    return
                self.send_response(404)
                self.end_headers()
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
