"""Cron trigger scheduler — polls for due cron triggers and executes them."""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime

from ..engine.types import NodeData

logger = logging.getLogger(__name__)

TICK_INTERVAL_SECONDS = 30
SCHEDULE_TICK_SECONDS = 5  # module @schedule functions run as often as every 15 s, so they get a faster loop


class CronScheduler:
    """Polls active_triggers for due crons and runs them."""

    def __init__(self, db_session_factory) -> None:
        self._db_session_factory = db_session_factory
        self._task: asyncio.Task | None = None
        self._smoke_tasks: set[asyncio.Task] = set()
        self._schedule_task: asyncio.Task | None = None
        self._schedule_runs: set[asyncio.Task] = set()

    def start(self) -> None:
        self._task = asyncio.create_task(self._tick_loop())
        self._schedule_task = asyncio.create_task(self._schedule_loop())
        logger.info("CronScheduler started (interval=%ds)", TICK_INTERVAL_SECONDS)

    async def stop(self) -> None:
        if self._task and not self._task.done():
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        for t in list(self._smoke_tasks) + list(self._schedule_runs) + ([self._schedule_task] if self._schedule_task else []):
            t.cancel()
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
        """One poll: cron triggers, smoke runs, then human tasks. Each part is isolated, so a failure in one never skips the others."""
        try:
            await self._tick_triggers()
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("CronScheduler trigger tick error")
        try:
            await self._tick_smoke()
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("CronScheduler smoke tick error")
        try:
            await self._tick_tasks()
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("CronScheduler task tick error")

    async def _schedule_loop(self) -> None:
        while True:
            try:
                await self._tick_module_schedules()
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("CronScheduler module-schedule tick error")
            await asyncio.sleep(SCHEDULE_TICK_SECONDS)

    async def _tick_module_schedules(self) -> list[asyncio.Task]:
        """Run the due ``@schedule`` functions of code modules (claimed first, so a slow run is never started twice)."""
        from ..services import module_schedules as ms

        started: list[asyncio.Task] = []
        for sid in await ms.claim_due(self._db_session_factory):
            task = asyncio.create_task(ms.execute(self._db_session_factory, sid))
            self._schedule_runs.add(task)
            task.add_done_callback(self._schedule_runs.discard)
            started.append(task)
        return started

    async def _tick_tasks(self) -> list[asyncio.Task]:
        """Human tasks (Stage 5): time out the ones past their deadline (``SKIP LOCKED``), resume decided tasks nobody
        claimed, and fail resumes whose process died (never re-run them). Returns the background resumes it started."""
        from . import task_resumer

        return await task_resumer.tick(self._db_session_factory)

    async def _tick_triggers(self) -> None:
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

    async def _tick_smoke(self) -> list[asyncio.Task]:
        """Start the live smoke runs that are due (enabled, scheduled, ``next_run_at`` passed) and move their next run on."""
        from sqlmodel import select

        from ..db.models import SmokeConfigModel
        from ..services.smoke import next_run_after

        started: list[asyncio.Task] = []
        now = datetime.now()
        # Claim phase: lock the due rows (SKIP LOCKED so a second replica skips them), move next_run_at on and COMMIT
        # before anything is spawned, so no replica or later tick can start the same smoke run twice.
        claimed: list[str] = []
        async with self._db_session_factory() as session:
            stmt = select(SmokeConfigModel).where(
                SmokeConfigModel.enabled == True,  # noqa: E712
                SmokeConfigModel.schedule.is_not(None),
                SmokeConfigModel.next_run_at <= now,
            ).with_for_update(skip_locked=True)
            for cfg in (await session.execute(stmt)).scalars().all():
                try:
                    cfg.next_run_at = next_run_after(cfg.schedule, now)
                except Exception:
                    cfg.enabled = False  # an unusable schedule never retries every tick
                    logger.warning("Disabled smoke schedule of %s: invalid cron", cfg.workflow_id)
                    continue
                claimed.append(cfg.workflow_id)
            await session.commit()
        for workflow_id in claimed:
            task = asyncio.create_task(self._run_smoke(workflow_id))
            self._smoke_tasks.add(task)
            task.add_done_callback(self._smoke_tasks.discard)
            started.append(task)
        return started

    async def _run_smoke(self, workflow_id: str) -> None:
        from ..repositories.workflow_repository import WorkflowRepository
        from ..services.smoke import SmokeError, run_smoke

        try:
            async with self._db_session_factory() as session:
                repo = WorkflowRepository(session)
                stored = await repo.get(workflow_id)
                if stored is not None:
                    await run_smoke(repo, stored, "schedule")
        except SmokeError as e:
            logger.info("Smoke run of %s skipped: %s", workflow_id, e.message)
        except Exception:
            logger.exception("Scheduled smoke run failed for workflow %s", workflow_id)

    async def _run_cron(self, trigger_id: int, workflow_id: str) -> None:
        from ..repositories.workflow_repository import WorkflowRepository
        from ..repositories.execution_repository import ExecutionRepository
        from ..engine.workflow_runner import WorkflowRunner
        from ..services.subworkflow_wiring import subworkflow_invoker
        from ..utils.ids import execution_id as gen_exec_id

        exec_id: str | None = None
        started_row = False
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

                from ..services.variable_loader import load_run_variables, load_run_secrets
                from ..services.project_service import effective_environment, resolve_project
                project = await resolve_project(session, stored.project_id)
                env = effective_environment(project, "default")
                variables = await load_run_variables(environment=env, project_id=stored.project_id)
                secrets = await load_run_secrets(environment=env, project_id=stored.project_id)

                exec_repo = ExecutionRepository(session)
                exec_id = gen_exec_id()
                if any(n.type in ("ExecuteWorkflow", "WorkflowTool") for n in stored.workflow.nodes):
                    # A run that calls sub-workflows needs its row while it runs, so the child rows can link to it. Other cron
                    # runs keep inserting their row at completion, as before.
                    await exec_repo.start(exec_id, stored.id, stored.name, "cron",
                                          project_id=stored.project_id, environment=env)
                    started_row = True
                context = await runner.run(
                    stored.workflow,
                    start_node.name,
                    initial_data,
                    "cron",
                    workflow_repository=workflow_repo,
                    execution_id=exec_id,
                    variables=variables,
                    secret_values=secrets,
                    project_id=stored.project_id,
                    environment=env,
                    subworkflow_invoker=subworkflow_invoker(self._db_session_factory),
                )

                await exec_repo.complete(context, stored.id, stored.name)
        except Exception as e:
            logger.exception("Cron execution failed for workflow %s", workflow_id)
            if started_row and exec_id is not None:  # the row was created up front: don't leave it "running" for the stale reaper
                try:
                    async with self._db_session_factory() as session:
                        await ExecutionRepository(session).mark_failed(exec_id, str(e))
                except Exception:
                    logger.warning("could not mark cron run %s failed", exec_id, exc_info=True)
