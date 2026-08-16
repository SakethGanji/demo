"""Reaper for stale (stuck) executions — marks old running executions as failed."""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta

from sqlalchemy import text, update
from sqlmodel import select

from ..db.models import ExecutionModel

logger = logging.getLogger(__name__)

TICK_INTERVAL_SECONDS = 300  # 5 minutes
STALE_EXECUTION_HOURS = 4


class StaleReaper:
    def __init__(self, db_session_factory) -> None:
        self._db_session_factory = db_session_factory
        self._task: asyncio.Task | None = None

    def start(self) -> None:
        self._task = asyncio.create_task(self._tick_loop())
        logger.info("StaleReaper started (interval=%ds, threshold=%dh)", TICK_INTERVAL_SECONDS, STALE_EXECUTION_HOURS)

    async def stop(self) -> None:
        if self._task and not self._task.done():
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        logger.info("StaleReaper stopped")

    async def _tick_loop(self) -> None:
        while True:
            try:
                await self._tick()
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("StaleReaper tick error")
            await asyncio.sleep(TICK_INTERVAL_SECONDS)

    async def _tick(self) -> None:
        cutoff = datetime.now() - timedelta(hours=STALE_EXECUTION_HOURS)
        async with self._db_session_factory() as session:
            stmt = (
                select(ExecutionModel)
                .where(
                    ExecutionModel.status == "running",
                    ExecutionModel.start_time < cutoff,
                )
            )
            result = await session.execute(stmt)
            stale = result.scalars().all()

            if stale:
                logger.warning("Marking %d stale executions as failed", len(stale))
                for ex in stale:
                    ex.status = "failed"
                    ex.end_time = datetime.now()
                await session.commit()
