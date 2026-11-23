"""Transport between the ``studio`` SDK and the engine's broker.

Credentials never reach user code: every node call is sent to the broker with a
run-scoped token, and the broker resolves ``{{ $vars.KEY }}`` server-side.

Transports:
* in-process (set by the engine for legacy in-process Code nodes),
* Unix socket (inside the sandbox: ``/run/broker/broker.sock``),
* HTTP (``STUDIO_BROKER_URL`` + ``STUDIO_BROKER_TOKEN``, e.g. a developer laptop).
"""

from __future__ import annotations

import contextvars
import json
import os
import socket
import urllib.request
from typing import Any, Callable


class BrokerError(Exception):
    """A broker call failed. ``code`` is stable (e.g. host_not_allowed, missing_secret)."""

    def __init__(self, code: str, message: str, status: int | None = None, details: dict | None = None) -> None:
        super().__init__(f"{code}: {message}")
        self.code = code
        self.message = message
        self.status = status
        self.details = details or {}


Transport = Callable[[str, dict, list], dict]
_current: contextvars.ContextVar[Transport | None] = contextvars.ContextVar("studio_transport", default=None)
_default: Transport | None = None


def configure(transport: Transport | None) -> None:
    global _default
    _default = transport


def use(transport: Transport):
    """Context-local transport (returns a token for ``_current.reset``)."""
    return _current.set(transport)


def _from_env() -> Transport | None:
    url, token = os.environ.get("STUDIO_BROKER_URL"), os.environ.get("STUDIO_BROKER_TOKEN")
    if url and token:
        return HttpTransport(url, token)
    return None


def _unwrap(resp: dict) -> list[dict]:
    if not resp.get("ok"):
        err = resp.get("error") or {}
        details = {k: v for k, v in err.items() if k not in ("code", "message")}
        raise BrokerError(err.get("code", "error"), err.get("message", "broker call failed"), resp.get("status"), details)
    return resp.get("items") or []


def call(node: str, parameters: dict[str, Any], items: list[dict] | None = None) -> list[dict]:
    """Call one node by type through the broker; returns the output items' json."""
    t = _current.get() or _default or _from_env()
    if t is None:
        raise BrokerError("no_transport", "studio SDK is not connected to a broker")
    return _unwrap(t(node, parameters, items if items is not None else [{}]))


class UnixSocketTransport:
    def __init__(self, path: str, token: str, timeout: float = 120.0) -> None:
        self.path, self.token, self.timeout = path, token, timeout

    def __call__(self, node: str, parameters: dict, items: list) -> dict:
        frame = json.dumps({"token": self.token, "node": node, "parameters": parameters, "items": items}) + "\n"
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as s:
            s.settimeout(self.timeout)
            s.connect(self.path)
            s.sendall(frame.encode())
            buf = b""
            while not buf.endswith(b"\n"):
                chunk = s.recv(65536)
                if not chunk:
                    break
                buf += chunk
        return json.loads(buf.decode() or "{}")


class HttpTransport:
    def __init__(self, base_url: str, token: str, timeout: float = 120.0) -> None:
        self.base_url, self.token, self.timeout = base_url.rstrip("/"), token, timeout

    def __call__(self, node: str, parameters: dict, items: list) -> dict:
        req = urllib.request.Request(
            self.base_url + "/api/broker/call",
            data=json.dumps({"node": node, "parameters": parameters, "items": items}).encode(),
            headers={"Content-Type": "application/json", "Authorization": f"Bearer {self.token}"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as r:
                body = json.loads(r.read() or b"{}")
                body.setdefault("status", r.status)
                return body
        except urllib.error.HTTPError as e:
            body = json.loads(e.read() or b"{}")
            body["status"] = e.code
            return body
