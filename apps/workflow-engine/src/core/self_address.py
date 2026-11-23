"""Record the port(s) this engine serves on (audit #1): read-only broker tokens may never call back
into the engine, and the listening port is only known from the ASGI scope."""

from __future__ import annotations


class RecordOwnPort:
    def __init__(self, app) -> None:
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] in ("http", "websocket"):
            server = scope.get("server")
            if server and len(server) > 1 and server[1]:
                from ..broker.policy import note_own_port
                note_own_port(server[1])
        await self.app(scope, receive, send)
