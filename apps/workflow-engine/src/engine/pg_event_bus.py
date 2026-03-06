"""Cross-pod SSE via Postgres LISTEN/NOTIFY."""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any, AsyncGenerator

logger = logging.getLogger(__name__)

# Max NOTIFY payload is 8000 bytes; we truncate and set ref flag above this
MAX_PAYLOAD_BYTES = 7500


class PgEventBus:
    """Publish/subscribe execution events via Postgres NOTIFY/LISTEN."""

    CHANNEL = "exec_events"

    def __init__(self, database_url: str) -> None:
        # Convert SQLAlchemy URL to raw asyncpg URL
        self._dsn = database_url.replace("postgresql+asyncpg://", "postgresql://")

    async def publish(self, execution_id: str, event_dict: dict[str, Any]) -> None:
        """Publish an event via NOTIFY."""
        import asyncpg

        payload = json.dumps({"execution_id": execution_id, "event": event_dict})

        if len(payload.encode()) > MAX_PAYLOAD_BYTES:
            # Truncate: send reference instead of full data
            truncated = {
                "execution_id": execution_id,
                "event": {
                    "type": event_dict.get("type"),
                    "executionId": event_dict.get("executionId"),
                    "nodeName": event_dict.get("nodeName"),
                    "ref": True,
                },
            }
            payload = json.dumps(truncated)

        conn = await asyncpg.connect(self._dsn)
        try:
            await conn.execute(f"NOTIFY {self.CHANNEL}, $1", payload)
        finally:
            await conn.close()

    async def subscribe(self, execution_id: str) -> AsyncGenerator[dict[str, Any] | None, None]:
        """Listen for events for a specific execution_id. Yields None on completion."""
        import asyncpg

        conn = await asyncpg.connect(self._dsn)
        try:
            queue: asyncio.Queue[str] = asyncio.Queue()

            def _listener(conn, pid, channel, payload):
                queue.put_nowait(payload)

            await conn.add_listener(self.CHANNEL, _listener)

            while True:
                payload_str = await queue.get()
                try:
                    data = json.loads(payload_str)
                except json.JSONDecodeError:
                    continue

                if data.get("execution_id") != execution_id:
                    continue

                event = data.get("event", {})
                event_type = event.get("type", "")

                if event_type in ("execution.complete", "execution.error"):
                    yield event
                    yield None
                    return

                yield event
        finally:
            await conn.remove_listener(self.CHANNEL, _listener)
            await conn.close()
