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
            # Find old executions
            stmt = select(ExecutionModel).where(ExecutionModel.start_time < cutoff)
            result = await session.execute(stmt)
            old_executions = result.scalars().all()

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
