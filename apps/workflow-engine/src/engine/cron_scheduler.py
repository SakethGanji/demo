"""Cron trigger scheduler — polls for due cron triggers and executes them."""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime

from ..engine.types import NodeData

logger = logging.getLogger(__name__)

TICK_INTERVAL_SECONDS = 30


class CronScheduler:
    """Polls active_triggers for due crons and runs them."""

    def __init__(self, db_session_factory) -> None:
        self._db_session_factory = db_session_factory
        self._task: asyncio.Task | None = None

    def start(self) -> None:
        self._task = asyncio.create_task(self._tick_loop())
        logger.info("CronScheduler started (interval=%ds)", TICK_INTERVAL_SECONDS)

    async def stop(self) -> None:
        if self._task and not self._task.done():
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        logger.info("CronScheduler stopped")

    async def _tick_loop(self) -> None:
        while True:
            try:
                await self._tick()
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("CronScheduler tick error")
            await asyncio.sleep(TICK_INTERVAL_SECONDS)

    async def _tick(self) -> None:
        from ..repositories.trigger_repository import TriggerRepository
        from ..repositories.workflow_repository import WorkflowRepository
        from ..repositories.execution_repository import ExecutionRepository
        from ..engine.workflow_runner import WorkflowRunner

        async with self._db_session_factory() as session:
            trigger_repo = TriggerRepository(session)
            due = await trigger_repo.get_due_triggers()

            for trigger in due:
                asyncio.create_task(
                    self._run_cron(trigger.id, trigger.workflow_id)
                )
                await trigger_repo.update_after_run(trigger.id)

            await session.commit()

    async def _run_cron(self, trigger_id: int, workflow_id: str) -> None:
        from ..repositories.workflow_repository import WorkflowRepository
        from ..repositories.execution_repository import ExecutionRepository
        from ..engine.workflow_runner import WorkflowRunner

        try:
            async with self._db_session_factory() as session:
                workflow_repo = WorkflowRepository(session)
                stored = await workflow_repo.get(workflow_id)
                if not stored or not stored.active:
                    return

                runner = WorkflowRunner(db_session_factory=self._db_session_factory)
                start_node = runner.find_start_node(stored.workflow)
                if not start_node:
                    return

                initial_data = [
                    NodeData(
                        json={
                            "triggeredAt": datetime.now().isoformat(),
                            "mode": "cron",
                            "triggerId": trigger_id,
                        }
                    )
                ]

                context = await runner.run(
                    stored.workflow,
                    start_node.name,
                    initial_data,
                    "cron",
                    workflow_repository=workflow_repo,
                )

                exec_repo = ExecutionRepository(session)
                await exec_repo.complete(context, stored.id, stored.name)
        except Exception:
            logger.exception("Cron execution failed for workflow %s", workflow_id)
