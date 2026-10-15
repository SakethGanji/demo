"""Per-sandbox-run broker endpoint on a Unix socket (BACKEND-DESIGN §5.5).

Newline-delimited JSON frames ``{token, node|function, parameters, items}`` → ``BrokerService``.
The socket lives in a fresh short path (/tmp/sbx-*/broker.sock; AF_UNIX paths are ≤108 bytes)
that is the only thing bind-mounted at /run/broker inside the sandbox.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os

from .service import BrokerService

logger = logging.getLogger(__name__)
MAX_FRAME = 4 * 1024 * 1024


class BrokerSocket:
    def __init__(self, directory: str) -> None:
        self.path = os.path.join(directory, "broker.sock")
        self._server: asyncio.base_events.Server | None = None

    async def __aenter__(self) -> "BrokerSocket":
        self._server = await asyncio.start_unix_server(self._handle, path=self.path, limit=MAX_FRAME)
        os.chmod(self.path, 0o600)
        return self

    async def __aexit__(self, *exc) -> None:
        if self._server is not None:
            self._server.close()
            await self._server.wait_closed()
        try:
            os.unlink(self.path)
        except FileNotFoundError:
            pass

    async def _handle(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        try:
            line = await reader.readline()
            try:
                frame = json.loads(line or b"{}")
                status, body = await BrokerService().call_with_token(
                    frame.get("token"), frame.get("function"), frame.get("node"),
                    frame.get("parameters") or {}, frame.get("items"))
                body["status"] = status
            except ValueError:
                body = {"ok": False, "status": 400, "error": {"code": "invalid_request", "message": "bad frame"}}
            writer.write((json.dumps(body, default=str) + "\n").encode())
            await writer.drain()
        except Exception:  # pragma: no cover
            logger.warning("broker socket handler failed", exc_info=True)
        finally:
            writer.close()
