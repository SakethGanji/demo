"""In-process registry of running execution tasks for cancellation + graceful shutdown."""

from __future__ import annotations

import asyncio
import logging

logger = logging.getLogger(__name__)

running_executions: dict[str, asyncio.Task] = {}


def register(execution_id: str, task: asyncio.Task) -> None:
    running_executions[execution_id] = task
    task.add_done_callback(lambda _: unregister(execution_id))


def unregister(execution_id: str) -> None:
    running_executions.pop(execution_id, None)


def cancel(execution_id: str) -> bool:
    task = running_executions.get(execution_id)
    if task and not task.done():
        task.cancel()
        return True
    return False


def get_all() -> dict[str, asyncio.Task]:
    return dict(running_executions)
