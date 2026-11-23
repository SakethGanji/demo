"""Mock bank APIs for live workflow runs: every operation of the 8 specs the workflows were built against.

Records every call (GET /_calls, DELETE /_calls to reset). Scenario knobs (POST /_control with JSON) steer answers:
  score            credit score (default 700)
  envelope_status  list of statuses getEnvelope returns in turn, last one repeats (default ["signed"])
  hit              sanctions hit (default false)
  uploaded         {customer_id: bool} for KYC documents (default false)
Records whether each bank call carried the API key (authed).
"""
import json
import re
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

STATE = {"calls": [], "control": {}}
API_KEY = "sk-bank-31337"


def ctl(key, default):
    return STATE["control"].get(key, default)


def route(method, path, body):
    m = lambda pat: re.fullmatch(pat, path)  # noqa: E731
    if method == "POST" and path == "/credit-checks":
        return {"applicant_id": body.get("applicant_id"), "score": ctl("score", 700)}
    if method == "POST" and path == "/envelopes":
        return {"envelope_id": "ENV-" + str(body.get("reference") or "1")}
    if method == "GET" and (r := m(r"/envelopes/([^/]+)")):
        seq = ctl("envelope_status", ["signed"])
        n = sum(1 for c in STATE["calls"] if c["method"] == "GET" and c["path"].startswith("/envelopes/"))
        return {"envelope_id": r[1], "status": seq[min(n - 1, len(seq) - 1)]}
    if method == "POST" and (r := m(r"/envelopes/([^/]+)/cancel")):
        return {"envelope_id": r[1], "status": "cancelled"}
    if method == "POST" and path == "/mortgages":
        return {"mortgage_id": "MG-" + str(body.get("application_id"))}
    if method == "POST" and (r := m(r"/wires/([^/]+)/(release|hold|block)")):
        return {"wire_id": r[1], "status": {"release": "released", "hold": "held", "block": "blocked"}[r[2]]}
    if method == "POST" and (r := m(r"/accounts/([^/]+)/freeze")):
        return {"account_id": r[1], "status": "frozen"}
    if method == "POST" and (r := m(r"/customers/([^/]+)/(restrict|lift-restriction)")):
        return {"customer_id": r[1]}
    if method == "POST" and (r := m(r"/loans/([^/]+)/restructure")):
        return {"loan_id": r[1], "status": "restructured"}
    if method == "POST" and path == "/screen":
        return {"hit": bool(ctl("hit", False)), "list": "OFAC" if ctl("hit", False) else ""}
    if method == "GET" and (r := m(r"/customers/([^/]+)/documents")):
        return {"customer_id": r[1], "uploaded": bool(ctl("uploaded", {}).get(r[1], False))}
    if method == "POST" and (r := m(r"/users/([^/]+)/revoke-access")):
        return {"user_id": r[1]}
    return None


class H(BaseHTTPRequestHandler):
    def _send(self, code, obj):
        data = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _handle(self, method):
        n = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(n) if n else b""
        try:
            body = json.loads(raw) if raw else {}
        except ValueError:
            body = {"_raw": raw.decode(errors="replace")}
        path = self.path.split("?")[0]
        if path == "/_calls":
            if method == "DELETE":
                STATE["calls"].clear()
                return self._send(200, {"ok": True})
            return self._send(200, STATE["calls"])
        if path == "/_control":
            STATE["control"] = body if isinstance(body, dict) else {}
            return self._send(200, STATE["control"])
        key = self.headers.get("X-API-Key") or (self.headers.get("Authorization") or "").removeprefix("Bearer ").strip()
        STATE["calls"].append({"method": method, "path": path, "body": body, "authed": key == API_KEY})
        # The generated workflows send no key (finding); record it on each call instead of refusing.
        out = route(method, path, body if isinstance(body, dict) else {})
        if out is None:
            return self._send(404, {"error": f"no route {method} {path}"})
        return self._send(200, out)

    def do_GET(self): self._handle("GET")
    def do_POST(self): self._handle("POST")
    def do_DELETE(self): self._handle("DELETE")
    def log_message(self, *a): pass


if __name__ == "__main__":
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 9100
    ThreadingHTTPServer(("0.0.0.0", port), H).serve_forever()
