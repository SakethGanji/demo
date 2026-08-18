"""Durable + live recorder for the agent loop's ``agent:*`` events.

``ExecutionEventCallback`` is **synchronous** and ``AIAgentNode._emit_event``
calls it with no ``try/except`` around it. An exception raised here would
propagate straight into the agent loop and kill the run mid-iteration, so
:meth:`AgentEventRecorder.__call__` does exactly two things — stamp a sequence
number and append to an in-memory buffer — and swallows absolutely everything.

All I/O happens on a separate writer task that batch-inserts into
``agent_run_events`` (at most ``batch_size`` rows or every ``flush_interval``
seconds). Events are also mirrored to bounded ``asyncio.Queue`` subscribers so
an SSE endpoint can tail a run live without touching Postgres.
"""

from __future__ import annotations

import asyncio
import json
import logging
from collections import deque
from datetime import datetime
from typing import Any

logger = logging.getLogger(__name__)

# Postgres will happily store a multi-megabyte jsonb; the studio will not
# happily render one. Anything past this is replaced by a preview.
MAX_PAYLOAD_BYTES = 256 * 1024

# Sentinel pushed to live subscribers when the run is finished.
END_OF_STREAM = object()


class AgentEventRecorder:
    """Collects agent events for one run: durable rows + a live tail."""

    def __init__(
        self,
        run_id: str,
        session_factory: Any | None = None,
        batch_size: int = 50,
        flush_interval: float = 0.25,
        max_payload_bytes: int = MAX_PAYLOAD_BYTES,
        live_queue_size: int = 1000,
    ) -> None:
        self.run_id = run_id
        self._batch_size = batch_size
        self._flush_interval = flush_interval
        self._max_payload_bytes = max_payload_bytes
        self._live_queue_size = live_queue_size

        if session_factory is None:
            from ..db.session import async_session_factory

            session_factory = async_session_factory
        self._session_factory = session_factory

        self._buffer: deque[dict[str, Any]] = deque()
        self._seq = 0
        self._written = 0
        self._dropped = 0
        self._subscribers: list[asyncio.Queue] = []
        self._wake = asyncio.Event()
        self._closing = False
        self._task: asyncio.Task | None = None
        self._loop: asyncio.AbstractEventLoop | None = None

    # -- lifecycle ---------------------------------------------------------

    async def start(self) -> None:
        """Spawn the writer task. Idempotent."""
        if self._task is not None:
            return
        self._loop = asyncio.get_running_loop()
        self._task = asyncio.create_task(self._writer(), name=f"agent-events:{self.run_id}")

    async def close(self, timeout: float = 30.0) -> None:
        """Drain the buffer and stop the writer. Never raises."""
        self._closing = True
        self._wake.set()
        if self._task is not None:
            try:
                await asyncio.wait_for(asyncio.shield(self._task), timeout=timeout)
            except (asyncio.TimeoutError, asyncio.CancelledError):
                self._task.cancel()
            except Exception:
                logger.warning("agent event writer failed on close", exc_info=True)
            self._task = None
        # Best-effort final flush for anything the writer never saw.
        if self._buffer:
            try:
                await self._flush(self._drain())
            except Exception:
                logger.warning("final agent-event flush failed", exc_info=True)
        for queue in self._subscribers:
            try:
                queue.put_nowait(END_OF_STREAM)
            except asyncio.QueueFull:
                pass

    # -- the synchronous callback the agent loop calls ---------------------

    def __call__(self, event: Any) -> None:
        """``ExecutionEventCallback``. Enqueue-only; swallows every error.

        A raise here would surface inside ``AIAgentNode._emit_event``, which
        has no handler, and abort the run.
        """
        try:
            self._seq += 1
            event_type = getattr(event, "type", "")
            # ExecutionEventType is a str-Enum; normalise either form.
            type_value = getattr(event_type, "value", event_type) or "unknown"

            # _emit_event packs the agent's event dict into data[0].json.
            payload: dict[str, Any] = {}
            data = getattr(event, "data", None)
            if data:
                first = data[0]
                candidate = getattr(first, "json", None)
                if isinstance(candidate, dict):
                    payload = candidate

            timestamp = getattr(event, "timestamp", None)
            row = {
                "run_id": self.run_id,
                "seq": self._seq,
                "type": str(type_value),
                "node_name": getattr(event, "node_name", None),
                "payload": payload,
                "created_at": timestamp if isinstance(timestamp, datetime) else datetime.now(),
            }
            self._buffer.append(row)
            self._publish(row)
            self._wake.set()
        except Exception:  # noqa: BLE001 — a recorder bug must not kill a run
            try:
                self._dropped += 1
                logger.warning("agent event dropped for run %s", self.run_id, exc_info=True)
            except Exception:
                pass

    # -- live tail ---------------------------------------------------------

    def subscribe(self) -> asyncio.Queue:
        """Return a bounded queue receiving every subsequent event."""
        queue: asyncio.Queue = asyncio.Queue(maxsize=self._live_queue_size)
        self._subscribers.append(queue)
        return queue

    def unsubscribe(self, queue: asyncio.Queue) -> None:
        if queue in self._subscribers:
            self._subscribers.remove(queue)

    def _publish(self, row: dict[str, Any]) -> None:
        for queue in list(self._subscribers):
            try:
                queue.put_nowait(row)
            except asyncio.QueueFull:
                # A slow reader must not stall the agent — drop the oldest.
                try:
                    queue.get_nowait()
                    queue.put_nowait(row)
                except Exception:
                    pass
            except Exception:
                pass

    # -- counters ----------------------------------------------------------

    @property
    def event_count(self) -> int:
        """Total events observed (whether or not they reached Postgres)."""
        return self._seq

    @property
    def written_count(self) -> int:
        return self._written

    # -- writer ------------------------------------------------------------

    def _drain(self) -> list[dict[str, Any]]:
        batch: list[dict[str, Any]] = []
        while self._buffer and len(batch) < self._batch_size:
            batch.append(self._buffer.popleft())
        return batch

    async def _writer(self) -> None:
        while True:
            if not self._buffer:
                if self._closing:
                    return
                try:
                    await asyncio.wait_for(self._wake.wait(), timeout=self._flush_interval)
                except asyncio.TimeoutError:
                    pass
                except asyncio.CancelledError:
                    break
                self._wake.clear()
                continue
            batch = self._drain()
            try:
                await self._flush(batch)
            except asyncio.CancelledError:
                break
            except Exception:
                # Losing a batch of telemetry is survivable; retrying forever
                # while the agent is still producing events is not.
                logger.warning(
                    "dropping %d agent events for run %s", len(batch), self.run_id,
                    exc_info=True,
                )
                self._dropped += len(batch)

        # Cancelled: make one last attempt so a killed run still has a trace.
        if self._buffer:
            try:
                await self._flush(self._drain())
            except Exception:
                pass

    async def _flush(self, batch: list[dict[str, Any]]) -> None:
        if not batch:
            return
        rows = [self._prepare(row) for row in batch]
        from ..repositories.agent_run_repository import AgentRunRepository

        async with self._session_factory() as session:
            repo = AgentRunRepository(session)
            self._written += await repo.insert_events(rows)

    def _prepare(self, row: dict[str, Any]) -> dict[str, Any]:
        """Make the payload JSON-safe and cap its size."""
        payload = row.get("payload") or {}
        truncated = False
        try:
            raw = json.dumps(payload, default=str)
        except Exception:
            raw = json.dumps({"unserializable": repr(payload)[: self._max_payload_bytes]})
            truncated = True

        if len(raw.encode("utf-8", "ignore")) > self._max_payload_bytes:
            keys = list(payload.keys()) if isinstance(payload, dict) else []
            payload = {
                "_truncated": True,
                "_original_bytes": len(raw),
                "_keys": keys[:50],
                "_preview": raw[:8192],
            }
            truncated = True
        else:
            try:
                payload = json.loads(raw)
            except Exception:
                payload = {"unserializable": raw[:8192]}
                truncated = True
            if not isinstance(payload, dict):
                payload = {"value": payload}

        return {
            "run_id": row["run_id"],
            "seq": row["seq"],
            "type": row["type"],
            "node_name": row.get("node_name"),
            "payload": payload,
            "truncated": truncated,
            "created_at": row.get("created_at") or datetime.now(),
        }
