"""Retention cleaner — deletes old executions and node outputs."""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta

from sqlmodel import select

from ..db.models import ExecutionModel, NodeOutputModel

logger = logging.getLogger(__name__)

TICK_INTERVAL_SECONDS = 3600  # 1 hour
RETENTION_DAYS = 30


def prune_broker_calls_sql() -> list[str]:
    """Review D6: broker_calls older than the retention window (param: days), and rows whose
    execution no longer exists. Plain SQL (one %s = days) so tests can run exactly what the tick runs."""
    return [
        "DELETE FROM broker_calls WHERE created_at < now() - make_interval(days => %s)",
        "DELETE FROM broker_calls b WHERE b.execution_id IS NOT NULL "
        "AND NOT EXISTS (SELECT 1 FROM executions e WHERE e.id = b.execution_id)",
    ]


class RetentionCleaner:
    def __init__(self, db_session_factory) -> None:
        self._db_session_factory = db_session_factory
        self._task: asyncio.Task | None = None

    def start(self) -> None:
        self._task = asyncio.create_task(self._tick_loop())
        logger.info("RetentionCleaner started (interval=%ds, retention=%dd)", TICK_INTERVAL_SECONDS, RETENTION_DAYS)

    async def stop(self) -> None:
        if self._task and not self._task.done():
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        logger.info("RetentionCleaner stopped")

    async def _tick_loop(self) -> None:
        while True:
            try:
                await self._tick()
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("RetentionCleaner tick error")
            await asyncio.sleep(TICK_INTERVAL_SECONDS)

    async def _tick(self) -> None:
        cutoff = datetime.now() - timedelta(days=RETENTION_DAYS)
        async with self._db_session_factory() as session:
            from sqlalchemy import text
            for stmt in prune_broker_calls_sql():
                await session.execute(text(stmt.replace("%s", ":days")), {"days": RETENTION_DAYS} if "%s" in stmt else {})
            await session.commit()
            # Find old executions
            stmt = select(ExecutionModel).where(ExecutionModel.start_time < cutoff)
            result = await session.execute(stmt)
            old_executions = result.scalars().all()
            # Stage 5: a run waiting for a person (days, up to a 90 d deadline) is never pruned, nor one with a task still to resume
            keep = {r[0] for r in (await session.execute(text(
                """SELECT id FROM executions WHERE status = 'waiting'
                   UNION SELECT execution_id FROM human_tasks WHERE status = 'open' OR resume_state IN ('pending', 'running')"""
            ))).all()} if old_executions else set()
            old_executions = [e for e in old_executions if e.id not in keep]

            if not old_executions:
                return

            exec_ids = [e.id for e in old_executions]
            logger.info("Cleaning %d executions older than %s", len(exec_ids), cutoff.isoformat())

            # Delete node outputs for these executions
            out_stmt = select(NodeOutputModel).where(NodeOutputModel.execution_id.in_(exec_ids))
            out_result = await session.execute(out_stmt)
            for output in out_result.scalars().all():
                await session.delete(output)

            # Delete the executions themselves
            for ex in old_executions:
                await session.delete(ex)

            await session.commit()
