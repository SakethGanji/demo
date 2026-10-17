"""Tiny fake HTTP server for the e2e stack's "Refund triage" workflow (stdlib only).

    python tests/fake_refund_api.py [--port N]     # port 0 (default) picks a free one

Prints one JSON line ``{"port": N}`` on stdout once listening, then serves:
    /ok     -> 200 {"ok": true, ...}       (the working risk API)
    /error  -> 500 {"error": ...}          (the HttpRequest node records the status but does not fail)
    /drop   -> closes the connection with no reply (the HttpRequest node fails: this is the seeded failure)
    /health -> 200
"""

from __future__ import annotations

import argparse
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def _reply(self, code: int, payload: dict) -> None:
        body = json.dumps(payload).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _handle(self) -> None:
        n = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(n).decode() if n else ""
        path = self.path.split("?", 1)[0]
        if path == "/ok":
            self._reply(200, {"ok": True, "decision": "review", "received": raw})
        elif path == "/error":
            self._reply(500, {"error": "risk service unavailable"})
        elif path == "/drop":
            self.close_connection = True
            self.connection.close()
        elif path == "/health":
            self._reply(200, {"ok": True})
        else:
            self._reply(404, {"error": "not found"})

    do_GET = do_POST = do_PUT = do_PATCH = do_DELETE = _handle

    def log_message(self, *args) -> None:  # silent
        pass


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=0)
    args = ap.parse_args()
    server = ThreadingHTTPServer(("127.0.0.1", args.port), Handler)
    print(json.dumps({"port": server.server_address[1]}), flush=True)
    server.serve_forever()


if __name__ == "__main__":
    main()
