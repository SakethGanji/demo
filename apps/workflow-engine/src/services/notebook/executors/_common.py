"""Helpers shared by the executors (connections, timing)."""

from __future__ import annotations

import time
from typing import Any

from . import CellOutcome


class Fail(Exception):
    """Raised inside an executor helper; ``outcome`` is the cell error to return."""

    def __init__(self, outcome: CellOutcome) -> None:
        super().__init__(outcome.error)
        self.outcome = outcome


async def connection_url(ctx: Any, ref: str) -> tuple[Any, str]:
    """``(connection, url)`` for a connection id or key of the notebook's project, in the run's environment."""
    from ...connection_service import ConnectionService
    from ...project_service import resolve_project
    from ..service import resolve_connection

    async with ctx.session() as s:
        conn = await resolve_connection(s, ctx.project_id, ref)
        if conn is None:
            raise Fail(CellOutcome.fail("connection_not_found", f"connection '{ref}' is not in this project"))
        project = await resolve_project(s, ctx.project_id)
        try:
            url = await ConnectionService(s).url_of(project, conn, ctx.environment)
        except Exception as e:  # noqa: BLE001 - a missing secret is a cell error, not a crash
            raise Fail(CellOutcome.fail("query_failed", f"connection '{conn.key}' has no URL: "
                                                        f"{getattr(e, 'message', type(e).__name__)}")) from None
        return conn, url


def ms_since(t0: float) -> int:
    return round((time.perf_counter() - t0) * 1000)
