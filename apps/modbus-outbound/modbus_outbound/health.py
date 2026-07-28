"""HTTP /health + /runtime/<target_id>.

/health       — servis saglik ozeti (Sistem Durumu sayfasi bu portu probe eder)
/runtime/<id> — tek hedefin canli durumu: sunucu ayakta mi, kac SCADA bagli,
                kac istek karsilandi. Gercek Modbus sunuculari backend'de
                degil bu serviste calistigi icin UI bunu buradan ogrenir.
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
        def _send_json(self, payload: dict, status: int = 200) -> None:
            body = json.dumps(payload).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):  # noqa: N802
            try:
                if self.path == "/health":
                    self._send_json(snapshot())
                    return
                if self.path.startswith("/runtime/") and runtime_for is not None:
                    raw = self.path.split("/runtime/", 1)[1]
                    try:
                        target_id = int(raw)
                    except ValueError:
                        self.send_response(400)
                        self.end_headers()
                        return
                    self._send_json(runtime_for(target_id))
                    return
                self.send_response(404)
                self.end_headers()
            except Exception as exc:  # noqa: BLE001
                self._send_json({"status": "error", "error": str(exc)}, status=500)

        def log_message(self, format, *args):  # noqa: A003
            _ = format, args
            return

    server = HTTPServer((host, port), _Handler)
    Thread(target=server.serve_forever, name="modbus-health", daemon=True).start()
    return server
