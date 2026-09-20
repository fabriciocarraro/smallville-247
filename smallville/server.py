"""Zero-dependency live view: static page + JSON snapshot + SSE stream."""
from __future__ import annotations

import json
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

WEB = Path(__file__).resolve().parent.parent / "web"


def make_handler(sim):
    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, *args):    # keep the console for sim output
            pass

        def _send(self, body: bytes, ctype: str, extra: dict | None = None) -> None:
            self.send_response(200)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            for k, v in (extra or {}).items():
                self.send_header(k, v)
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            path = self.path.split("?")[0]

            if path in ("/", "/index.html"):
                f = WEB / "index.html"
                if not f.exists():
                    self._send(b"web/index.html missing", "text/plain")
                    return
                self._send(f.read_bytes(), "text/html; charset=utf-8")

            elif path == "/api/roster":
                self._send(json.dumps(sim.roster()).encode(), "application/json")

            elif path == "/api/state":
                self._send(json.dumps(sim.snapshot()).encode(), "application/json")

            elif path == "/api/stream":
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream")
                self.send_header("Cache-Control", "no-cache")
                self.send_header("Connection", "keep-alive")
                self.end_headers()
                try:
                    while True:
                        payload = json.dumps(sim.snapshot())
                        self.wfile.write(f"data: {payload}\n\n".encode())
                        self.wfile.flush()
                        time.sleep(1.0)
                except (BrokenPipeError, ConnectionResetError, OSError):
                    return

            else:
                self.send_response(404)
                self.send_header("Content-Length", "0")
                self.end_headers()

    return Handler


def serve(sim, port: int = 8765, host: str = "127.0.0.1") -> ThreadingHTTPServer:
    """Refuse to start if the port is already serving.

    On Windows, SO_REUSEADDR lets a second process bind a port another process
    is already listening on. Nothing errors; the older process keeps answering
    and the new one is invisible. That means a restart silently runs the OLD
    code while looking perfectly healthy - I have lost time to this twice, once
    while rewriting the renderer and once while adding the narrator.
    """
    import socket

    probe = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    probe.settimeout(0.4)
    taken = probe.connect_ex(("127.0.0.1", port)) == 0
    probe.close()
    if taken:
        raise OSError(
            f"port {port} is already serving - another instance is running and "
            f"would keep answering while this one sat idle. Stop it first, or "
            f"pass --port with a free one."
        )

    httpd = ThreadingHTTPServer((host, port), make_handler(sim))
    httpd.allow_reuse_address = False
    httpd.daemon_threads = True
    return httpd
