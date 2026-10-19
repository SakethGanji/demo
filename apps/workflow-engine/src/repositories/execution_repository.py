"""Execution repository for database persistence."""

from __future__ import annotations


import json
import logging
import os
from datetime import datetime, timedelta
from typing import TYPE_CHECKING, Any, Awaitable, Callable, Literal

from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import Date, case, cast, func
from sqlmodel import select

from ..db.models import ExecutionModel, NodeOutputModel

if TYPE_CHECKING:
    from ..engine.types import ExecutionContext, ExecutionRecord

logger = logging.getLogger(__name__)

#: Async callbacks ``(context, record)`` run after every real (non-test) ``complete()`` commit: success, failed or waiting.
#: Builder B registers the chat hook here. A hook that raises is logged and never changes the run.
on_complete_hooks: list[Callable[[Any, Any], Awaitable[None]]] = []

#: ``metadata.result`` is capped at 256 KB (DESIGN-STAGE5-UIS 3.4); over the cap it becomes ``{"truncated": true}``.
MAX_RESULT_BYTES = 256 * 1024


_MODE_TRIGGER = {"manual": "manual", "webhook": "webhook", "cron": "cron", "retry": "retry", "app": "app",
                 "api": "public", "sandbox": "draft_test"}
TRIGGER_TYPES = {"manual", "webhook", "cron", "retry", "route_test", "draft_test", "generate_self_test", "app",
                 "app_preview", "api_key", "public"}


def default_trigger_type(mode: str | None) -> str:
    """Review C2: every execution carries metadata.trigger.type; callers that know better set it."""
    return _MODE_TRIGGER.get(mode or "", mode or "manual")


def run_retention() -> int:
    """Newest executions kept globally. ``WORKFLOW_RUN_RETENTION`` overrides the default of 100."""
    try:
        return max(1, int(os.environ.get("WORKFLOW_RUN_RETENTION", "100")))
    except ValueError:
        return 100


class ExecutionRepository:
    """Repository for execution history persistence."""

    on_complete_hooks = on_complete_hooks  # the same list, reachable from the class too

    def __init__(self, session: AsyncSession, max_records: int | None = None) -> None:
        self._session = session
        self._max_records = max_records if max_records is not None else run_retention()

    async def start(
        self,
        execution_id: str,
        workflow_id: str,
        workflow_name: str,
        mode: str,
        project_id: str | None = None,
        environment: str | None = None,
        metadata: dict[str, Any] | None = None,
        *,
        parent_execution_id: str | None = None,
        parent_node_name: str | None = None,
        depth: int = 0,
        root_execution_id: str | None = None,
        workflow_version_id: int | None = None,
    ) -> ExecutionRecord:
        """Create a new execution record when workflow starts.

        The keyword-only lineage arguments are written for sub-workflow runs (DESIGN-STAGE8-REUSE 3.3): the parent link
        (FK, ON DELETE CASCADE), the top-level run, the depth and the pinned version row id. A top-level run leaves them
        at their defaults and is its own root (``root_execution_id`` stays NULL, which ``_cleanup`` reads as "a root").
        """
        from ..engine.types import ExecutionRecord

        if project_id is None:
            # Derive the owning project from the workflow row (adhoc runs stay NULL→default).
            from ..db.models import WorkflowModel
            wf = await self._session.get(WorkflowModel, workflow_id)
            project_id = wf.project_id if wf else "proj_default"

        meta = dict(metadata or {})
        trigger = dict(meta.get("trigger") or {})
        trigger.setdefault("type", default_trigger_type(mode))
        meta["trigger"] = trigger
        db_execution = ExecutionModel(
            id=execution_id,
            workflow_id=workflow_id,
            workflow_name=workflow_name,
            status="running",
            mode=mode,
            start_time=datetime.now(),
            project_id=project_id,
            environment=environment,
            exec_metadata=meta,
            parent_execution_id=parent_execution_id,
            parent_node_name=parent_node_name,
            depth=depth,
            root_execution_id=root_execution_id,
            workflow_version_id=workflow_version_id,
        )

        self._session.add(db_execution)
        await self._session.commit()
        await self._session.refresh(db_execution)

        # Cleanup old records (a child never prunes: only roots count, section 3.3)
        if parent_execution_id is None:
            await self._cleanup()

        return self._to_execution_record(db_execution, [], {})

    async def set_metadata(self, execution_id: str, patch: dict[str, Any]) -> None:
        """Merge keys into executions.metadata (e.g. the per-request trace)."""
        db_execution = await self._session.get(ExecutionModel, execution_id)
        if not db_execution:
            return
        db_execution.exec_metadata = {**(db_execution.exec_metadata or {}), **patch}
        await self._session.commit()

    async def complete(
        self,
        context: ExecutionContext,
        workflow_id: str,
        workflow_name: str,
    ) -> ExecutionRecord:
        """Update execution with final state."""
        db_execution = await self._session.get(ExecutionModel, context.execution_id)

        created_here = False
        if not db_execution:
            # Paths that never called start() (cron, retry): same defaults as start() (review C2/D6).
            from ..db.models import WorkflowModel
            wf = await self._session.get(WorkflowModel, workflow_id)
            db_execution = ExecutionModel(
                id=context.execution_id,
                workflow_id=workflow_id,
                workflow_name=workflow_name,
                status="running",
                mode=context.mode,
                start_time=context.start_time,
                project_id=getattr(context, "project_id", None) or (wf.project_id if wf else "proj_default"),
                environment=getattr(context, "environment", None),
                exec_metadata={"trigger": {"type": default_trigger_type(context.mode)}},
            )
            self._session.add(db_execution)
            await self._session.flush()
            created_here = True

        # Update execution status. Stage 5: a run with an Approval paused (or with decided tasks still to resume) is "waiting".
        from .task_repository import TaskRepository

        tasks = TaskRepository(self._session)
        resuming = context.extras.get("resume") if isinstance(context.extras.get("resume"), dict) else None
        resuming_task_id = resuming.get("task_id") if resuming else None
        suspended = bool(context.suspensions) and not context.errors
        waiting = False
        if context.errors:
            status = "failed"
        else:
            waiting = suspended or (
                context.mode != "test"
                and await self._savepoint(tasks.blocking_count(context.execution_id, exclude_task_id=resuming_task_id), 0) > 0
            )
            status = "waiting" if waiting else "success"
        db_execution.status = status
        db_execution.end_time = None if waiting else datetime.now()
        db_execution.error_count = len(context.errors)
        db_execution.completed_nodes = len(context.node_states)
        db_execution.total_nodes = len(context.node_states)

        # Persist per-node outputs into node_outputs table
        for node_name, items in context.node_states.items():
            if node_name in context.reused_nodes:
                continue  # partial run: outputs borrowed from an earlier execution are not this run's
            output_data = [
                {"json": item.json, "binary": None}
                for item in items
            ]
            metrics = context.node_metrics.get(node_name)

            # Check for error on this node
            node_error = None
            node_status = "success"
            for e in context.errors:
                if e.node_name == node_name:
                    node_error = e.error
                    node_status = "error"
                    break

            # Upsert node output
            stmt = select(NodeOutputModel).where(
                NodeOutputModel.execution_id == context.execution_id,
                NodeOutputModel.node_name == node_name,
                NodeOutputModel.run_index == 0,
            )
            result = await self._session.execute(stmt)
            existing = result.scalar_one_or_none()

            if existing:
                existing.output = output_data
                existing.metrics = metrics
                existing.status = node_status
                existing.error = node_error
            else:
                record = NodeOutputModel(
                    execution_id=context.execution_id,
                    node_name=node_name,
                    output=output_data,
                    metrics=metrics,
                    status=node_status,
                    error=node_error,
                    run_index=0,
                )
                self._session.add(record)

        # Also persist errors for nodes that failed but may not be in node_states
        for e in context.errors:
            if e.node_name not in context.node_states:
                stmt = select(NodeOutputModel).where(
                    NodeOutputModel.execution_id == context.execution_id,
                    NodeOutputModel.node_name == e.node_name,
                    NodeOutputModel.run_index == 0,
                )
                result = await self._session.execute(stmt)
                existing = result.scalar_one_or_none()
                if not existing:
                    record = NodeOutputModel(
                        execution_id=context.execution_id,
                        node_name=e.node_name,
                        output=[],
                        metrics=context.node_metrics.get(e.node_name),
                        status="error",
                        error=e.error,
                        run_index=0,
                    )
                    self._session.add(record)

        meta = dict(db_execution.exec_metadata or {})
        to_notify: list[dict[str, Any]] = []
        if status == "failed":
            # nothing will ever decide these: a failed run does not keep tasks open
            if context.mode != "test":
                await self._savepoint(tasks.cancel_open_for_execution(context.execution_id), 0)
            meta.pop("suspension", None)
        elif waiting:
            if suspended:
                to_notify = await self._create_tasks(tasks, context, db_execution)
            open_ids = await tasks.open_ids(context.execution_id)
            since = (meta.get("suspension") or {}).get("since") or datetime.now().isoformat()
            meta["suspension"] = {"tasks": open_ids, "since": since}
        else:
            meta.pop("suspension", None)
            if "interface" in meta:
                meta["result"] = self._result_of(context)
        db_execution.exec_metadata = meta

        await self._session.commit()
        await self._session.refresh(db_execution)
        if to_notify:  # after the commit: the task rows exist, so the email can link to them; a failed email never fails the run
            await self._notify_approvers(to_notify, db_execution)

        # Read back node outputs for the return value
        node_outputs = await self._get_node_outputs(context.execution_id)
        node_data, node_metrics = self._outputs_to_dicts(node_outputs)
        record = self._to_execution_record(db_execution, node_outputs, node_metrics)
        if created_here and not waiting:
            await self._cleanup()
        if context.mode != "test":
            if not waiting:  # a paused run has no complete set of replies to compare yet
                await self._check_drift(context)
            await self._run_hooks(context, record)
        return record

    # ── Stage 5: suspended runs ───────────────────────────────────────────────

    async def _savepoint(self, coro: Awaitable[Any], default: Any) -> Any:
        """Await a task-table query inside a savepoint; a database without the Stage 5 tables (migration not applied
        yet) answers ``default`` instead of failing every run."""
        from sqlalchemy.exc import ProgrammingError

        try:
            async with self._session.begin_nested():
                return await coro
        except ProgrammingError as e:
            if "human_tasks" not in str(e):
                raise
            logger.warning("human_tasks table missing: apply migration 20261019120000_interfaces_tasks")
            return default

    async def _create_tasks(self, tasks: Any, context: ExecutionContext, db_execution: ExecutionModel) -> list[dict[str, Any]]:
        """One ``human_tasks`` row per Approval that paused this run (idempotent on ``(execution, node, run_index)``).
        Returns the newly inserted rows whose Approval asked for an approver email (never in test mode)."""
        from ..engine.task_resumer import build_snapshot
        from .task_repository import new_task_id

        to_notify: list[dict[str, Any]] = []
        for entry in context.suspensions:
            payload = entry["suspension"].payload
            snapshot = build_snapshot(context, entry)
            due_raw = payload.get("due_at")
            row = {
                "id": new_task_id(),
                "project_id": db_execution.project_id or "proj_default",
                "workflow_id": db_execution.workflow_id,
                "workflow_version_id": context.version_id,
                "execution_id": context.execution_id,
                "node_name": entry["node"],
                "run_index": entry["run_index"],
                "kind": "approval",
                "title": payload.get("title") or entry["node"],
                "context": payload.get("context") or {},
                "form": payload.get("form") or [],
                "assignees": payload.get("assignees") or [],
                "due_at": datetime.fromisoformat(due_raw) if due_raw else None,
                "on_timeout": payload.get("on_timeout") or "timed_out",
                "snapshot": snapshot,
            }
            inserted = await tasks.insert(row)
            if inserted and payload.get("notify") and context.mode != "test":
                to_notify.append({"task": row, "notify": payload["notify"]})
        return to_notify

    async def _notify_approvers(self, to_notify: list[dict[str, Any]], db_execution: ExecutionModel) -> None:
        """Email the approvers of the tasks just created (``services.approval_notify``). Never raises."""
        try:
            from ..services.approval_notify import notify_created_tasks

            await notify_created_tasks(self._session, to_notify, workflow_name=db_execution.workflow_name or db_execution.workflow_id,
                                       project_id=db_execution.project_id)
        except Exception:
            logger.warning("approver notification failed for %s", db_execution.id, exc_info=True)
        try:  # a rollback inside the notifier expires loaded rows; reload before the record is built from it
            await self._session.refresh(db_execution)
        except Exception:
            logger.warning("couldn't reload execution %s after the approver notification", db_execution.id, exc_info=True)

    @staticmethod
    def _result_of(context: ExecutionContext) -> dict[str, Any]:
        """The redacted result a surface shows: the Respond body, else the last node's first item (cap 256 KB)."""
        from ..services.vars_substitution import redact_deep

        body: Any = None
        if context.webhook_response is not None:
            body = context.webhook_response.body
        elif context.last_completed_node and context.node_states.get(context.last_completed_node):
            body = context.node_states[context.last_completed_node][0].json
        if body is None:
            return {}
        if isinstance(body, (bytes, bytearray)):
            body = {"truncated": True}
        elif not isinstance(body, dict):
            body = {"value": body}
        body = redact_deep(body, context.extras.get("secret_values") or set())
        try:
            if len(json.dumps(body, default=str)) > MAX_RESULT_BYTES:
                return {"truncated": True}
        except (TypeError, ValueError):
            return {"truncated": True}
        return json.loads(json.dumps(body, default=str))

    async def _run_hooks(self, context: ExecutionContext, record: Any) -> None:
        for hook in list(on_complete_hooks):
            try:
                await hook(context, record)
            except Exception:
                logger.warning("on_complete hook failed for %s", context.execution_id, exc_info=True)

    async def _check_drift(self, context: ExecutionContext) -> None:
        """Stage 3B: compare what the outside steps returned with what the workflow expects. Never changes the run."""
        try:
            from ..engine.effects import effect_of
            from ..services.drift import guarded_check_execution

            if not any(effect_of(n.type) in ("network", "database") for n in context.workflow.nodes):
                return
            await guarded_check_execution(self._session, context.execution_id)
        except Exception:
            logger.warning("drift hook failed for %s", context.execution_id, exc_info=True)

    async def get(self, execution_id: str) -> ExecutionRecord | None:
        """Get an execution record by ID."""
        db_execution = await self._session.get(ExecutionModel, execution_id)
        if not db_execution:
            return None

        node_outputs = await self._get_node_outputs(execution_id)
        _, node_metrics = self._outputs_to_dicts(node_outputs)
        return self._to_execution_record(db_execution, node_outputs, node_metrics)

    async def daily_counts(self, project_id: str, days: int = 30, environment: str | None = None) -> list[dict[str, Any]]:
        """Per-day {date,total,failed} for the last ``days`` days (today included), zero-filled, grouped in SQL.

        Days are the calendar dates of ``start_time`` (stored as naive timestamps; servers run in UTC).
        """
        today = datetime.now().date()
        first = today - timedelta(days=days - 1)
        day = cast(ExecutionModel.start_time, Date)
        stmt = (
            select(day.label("d"), func.count().label("total"),
                   func.coalesce(func.sum(case((ExecutionModel.status == "failed", 1), else_=0)), 0).label("failed"))
            .where(ExecutionModel.project_id == project_id, ExecutionModel.mode != "test",
                   ExecutionModel.parent_execution_id.is_(None),
                   ExecutionModel.start_time >= datetime.combine(first, datetime.min.time()))
            .group_by(day)
        )
        if environment:
            stmt = stmt.where(ExecutionModel.environment == environment)
        found = {r.d: (int(r.total), int(r.failed)) for r in (await self._session.execute(stmt)).all()}
        out = []
        for i in range(days):
            d = first + timedelta(days=i)
            t, f = found.get(d, (0, 0))
            out.append({"date": d.isoformat(), "total": t, "failed": f})
        return out

    async def list(
        self, workflow_id: str | None = None, project_id: str | None = None,
        mode: str | None = None, status: str | None = None, limit: int | None = None,
        before: str | None = None, route_id: str | None = None, environment: str | None = None,
        trigger: str | None = None, include_test: bool = False, include_children: bool = False,
        parent_execution_id: str | None = None,
    ) -> list[ExecutionRecord]:
        """List execution records, optionally filtered by workflow / project / mode / status.

        Test runs (``mode="test"``) are left out unless ``include_test`` or ``mode="test"`` asks for them.
        Sub-workflow runs (those with a parent) are left out unless ``include_children``; ``parent_execution_id`` lists
        the children of one run (DESIGN-STAGE8-REUSE section 11)."""
        statement = select(ExecutionModel).order_by(ExecutionModel.start_time.desc())
        if parent_execution_id:
            statement = statement.where(ExecutionModel.parent_execution_id == parent_execution_id)
        elif not include_children:
            statement = statement.where(ExecutionModel.parent_execution_id.is_(None))
        if not include_test and mode != "test":
            statement = statement.where(ExecutionModel.mode != "test")

        if workflow_id:
            statement = statement.where(ExecutionModel.workflow_id == workflow_id)
        if project_id:
            statement = statement.where(ExecutionModel.project_id == project_id)
        if mode:
            statement = statement.where(ExecutionModel.mode == mode)
        if status:
            statement = statement.where(ExecutionModel.status == status)
        if environment:
            statement = statement.where(ExecutionModel.environment == environment)
        if route_id:
            statement = statement.where(ExecutionModel.exec_metadata["trigger"]["route_id"].astext == route_id)
        if trigger:
            statement = statement.where(ExecutionModel.exec_metadata["trigger"]["type"].astext == trigger)
        if before:
            pivot = await self._session.get(ExecutionModel, before)
            if pivot is None:
                # Review D6: the cursor row was pruned (or never existed) — an empty page, never page 1 again.
                return []
            statement = statement.where(
                (ExecutionModel.start_time < pivot.start_time)
                | ((ExecutionModel.start_time == pivot.start_time) & (ExecutionModel.id < pivot.id))
            )
            statement = statement.order_by(None).order_by(ExecutionModel.start_time.desc(), ExecutionModel.id.desc())
        if limit:
            statement = statement.limit(limit)

        result = await self._session.execute(statement)
        executions = result.scalars().all()

        if not executions:
            return []

        # Batch-fetch all node outputs in one query instead of N+1
        exec_ids = [e.id for e in executions]
        outputs_stmt = (
            select(NodeOutputModel)
            .where(NodeOutputModel.execution_id.in_(exec_ids))
            .order_by(NodeOutputModel.created_at)
        )
        outputs_result = await self._session.execute(outputs_stmt)
        all_outputs = outputs_result.scalars().all()

        # Group by execution_id
        outputs_by_exec: dict[str, list[NodeOutputModel]] = {}
        for o in all_outputs:
            outputs_by_exec.setdefault(o.execution_id, []).append(o)

        records = []
        for e in executions:
            node_outputs = outputs_by_exec.get(e.id, [])
            _, node_metrics = self._outputs_to_dicts(node_outputs)
            records.append(self._to_execution_record(e, node_outputs, node_metrics))
        return records

    async def lineage(self, execution_id: str) -> dict[str, Any] | None:
        """Where a run sits in a sub-workflow tree: its parent link, root, depth and direct children (oldest first)."""
        row = await self._session.get(ExecutionModel, execution_id)
        if row is None:
            return None
        parent = await self._session.get(ExecutionModel, row.parent_execution_id) if row.parent_execution_id else None
        kids = (await self._session.execute(
            select(ExecutionModel).where(ExecutionModel.parent_execution_id == execution_id)
            .order_by(ExecutionModel.start_time, ExecutionModel.id))).scalars().all()
        return {
            "parent": None if parent is None else {
                "execution_id": parent.id, "workflow_id": parent.workflow_id, "workflow_name": parent.workflow_name,
                "node": row.parent_node_name},
            "root_execution_id": row.root_execution_id or row.id,
            "depth": row.depth or 0,
            "children": [{
                "execution_id": k.id, "node": k.parent_node_name, "workflow_id": k.workflow_id,
                "workflow_name": k.workflow_name, "workflow_version_id": k.workflow_version_id, "status": k.status,
                "duration_ms": int((k.end_time - k.start_time).total_seconds() * 1000) if k.end_time else None,
            } for k in kids],
        }

    async def find_latest_successful(
        self, workflow_id: str
    ) -> ExecutionRecord | None:
        """Find the latest successful execution for a workflow."""
        statement = (
            select(ExecutionModel)
            .where(
                ExecutionModel.workflow_id == workflow_id,
                ExecutionModel.status == "success",
                ExecutionModel.mode != "test",
            )
            .order_by(ExecutionModel.start_time.desc())
            .limit(1)
        )
        result = await self._session.execute(statement)
        db_execution = result.scalar_one_or_none()
        if not db_execution:
            return None

        node_outputs = await self._get_node_outputs(db_execution.id)
        _, node_metrics = self._outputs_to_dicts(node_outputs)
        return self._to_execution_record(db_execution, node_outputs, node_metrics)

    async def find_latest_successful_id(
        self, workflow_id: str
    ) -> str | None:
        """Find the latest successful execution ID (lightweight — no node outputs)."""
        statement = (
            select(ExecutionModel.id)
            .where(
                ExecutionModel.workflow_id == workflow_id,
                ExecutionModel.status == "success",
                ExecutionModel.mode != "test",
            )
            .order_by(ExecutionModel.start_time.desc())
            .limit(1)
        )
        result = await self._session.execute(statement)
        return result.scalar_one_or_none()

    async def delete(self, execution_id: str) -> bool:
        """Delete an execution record."""
        db_execution = await self._session.get(ExecutionModel, execution_id)
        if not db_execution:
            return False

        # Delete node outputs first (FK constraint)
        stmt = select(NodeOutputModel).where(NodeOutputModel.execution_id == execution_id)
        result = await self._session.execute(stmt)
        for output in result.scalars().all():
            await self._session.delete(output)

        await self._session.delete(db_execution)
        await self._session.commit()
        return True

    async def open_task_summaries(self, execution_id: str) -> list[dict[str, Any]]:
        """The open human tasks of a waiting execution (what ``execution_waiting`` tells a stream), oldest first."""
        from sqlalchemy import text

        rows = (await self._session.execute(
            text("""SELECT id, node_name, run_index, title, assignees, due_at FROM human_tasks
                    WHERE execution_id = :e AND status = 'open' ORDER BY created_at, id"""),
            {"e": execution_id},
        )).mappings().all()
        return [
            {"id": r["id"], "node_name": r["node_name"], "run_index": r["run_index"], "title": r["title"],
             "assignees": list(r["assignees"] or []), "due_at": r["due_at"].isoformat() if r["due_at"] else None}
            for r in rows
        ]

    async def mark_failed(
        self, execution_id: str, error: str, error_code: str | None = None, *, node_name: str | None = None, run_index: int = 0,
    ) -> None:
        db_execution = await self._session.get(ExecutionModel, execution_id)
        if not db_execution:
            return
        db_execution.status = "failed"
        db_execution.end_time = datetime.now()
        db_execution.error_count = max(1, db_execution.error_count or 0)
        meta = {**(db_execution.exec_metadata or {}), "error": error}
        if error_code:
            meta["error_code"] = error_code
        meta.pop("suspension", None)
        db_execution.exec_metadata = meta
        if error_code in ("RESUME_INTERRUPTED", "RESUME_FAILED"):
            from sqlalchemy import text as sa_text

            from .task_repository import TaskRepository

            await TaskRepository(self._session).cancel_open_for_execution(execution_id)
            if node_name:  # show the failure on the Approval node the run was resumed from
                await self._session.execute(
                    sa_text("""UPDATE node_outputs
                                  SET status = 'error', error = :msg,
                                      metrics = COALESCE(metrics, '{}'::jsonb) || CAST(:m AS jsonb)
                                WHERE execution_id = :e AND node_name = :n AND run_index = :i"""),
                    {"msg": error, "e": execution_id, "n": node_name, "i": run_index,
                     "m": json.dumps({"status": "error", "errorCode": error_code})},
                )
        await self._session.commit()

    async def cancel(self, execution_id: str) -> bool:
        """Mark an execution as cancelled."""
        db_execution = await self._session.get(ExecutionModel, execution_id)
        if not db_execution:
            return False
        db_execution.status = "cancelled"
        db_execution.cancelled_at = datetime.now()
        db_execution.end_time = datetime.now()
        # a waiting run's open tasks go with it: a later decide answers "already_closed"
        from .task_repository import TaskRepository

        await TaskRepository(self._session).cancel_open_for_execution(execution_id)
        await self._session.commit()
        return True

    async def purge_test_runs(self, older_than_days: int = 7) -> int:
        """Delete test executions (``mode="test"``) older than ``older_than_days``; returns how many went."""
        from sqlalchemy import delete as sa_delete

        cutoff = datetime.now() - timedelta(days=older_than_days)
        ids = (await self._session.execute(
            select(ExecutionModel.id).where(ExecutionModel.mode == "test", ExecutionModel.start_time < cutoff)
        )).scalars().all()
        if not ids:
            return 0
        await self._session.execute(sa_delete(NodeOutputModel).where(NodeOutputModel.execution_id.in_(ids)))
        await self._session.execute(sa_delete(ExecutionModel).where(ExecutionModel.id.in_(ids)))
        await self._session.commit()
        return len(ids)

    async def clear(self) -> None:
        """Clear all execution records and their node outputs."""
        # Bulk deletes: the parent FK cascades to sub-workflow rows, which a per-row ORM delete would find already gone.
        from sqlalchemy import delete as sa_delete

        await self._session.execute(sa_delete(NodeOutputModel))
        await self._session.execute(sa_delete(ExecutionModel))
        await self._session.commit()

    async def _cleanup(self) -> None:
        """Keep the newest ``max_records`` root executions (review D6: called on every insert path).

        Only roots count (``parent_execution_id IS NULL``): a busy sub-workflow can't prune its own parents' history
        (DESIGN-STAGE8-REUSE 3.3). Pruned executions take their node outputs and broker-call rows with them, and their
        children go through the ``fk_executions_parent`` cascade (their broker-call rows are removed here)."""
        from sqlalchemy import delete as sa_delete

        from ..db.models import BrokerCallModel

        ids = (await self._session.execute(
            select(ExecutionModel.id).where(ExecutionModel.mode != "test",  # test runs expire by age instead
                                            ExecutionModel.parent_execution_id.is_(None))
            .order_by(ExecutionModel.start_time.desc(), ExecutionModel.id.desc())
            .offset(self._max_records))).scalars().all()
        if not ids:
            return
        # Stage 5: a waiting run (or one with a task still open / decided and not yet resumed) is never pruned
        protected = await self._savepoint(self._protected_ids(list(ids)), set())
        ids = [i for i in ids if i not in protected]
        if not ids:
            return
        all_ids = await self._with_descendants(ids)
        await self._session.execute(sa_delete(NodeOutputModel).where(NodeOutputModel.execution_id.in_(all_ids)))
        await self._session.execute(sa_delete(BrokerCallModel).where(BrokerCallModel.execution_id.in_(all_ids)))
        await self._session.execute(sa_delete(ExecutionModel).where(ExecutionModel.id.in_(ids)))
        await self._session.commit()

    async def _with_descendants(self, ids: list[str]) -> list[str]:
        """``ids`` plus every sub-workflow execution under them (one query on the root link, one on the parent link)."""
        from sqlalchemy import or_

        rows = (await self._session.execute(
            select(ExecutionModel.id).where(or_(ExecutionModel.root_execution_id.in_(ids),
                                                ExecutionModel.parent_execution_id.in_(ids))))).scalars().all()
        return list(dict.fromkeys([*ids, *rows]))

    async def _protected_ids(self, ids: list[str]) -> set[str]:
        from sqlalchemy import text as sa_text

        rows = await self._session.execute(
            sa_text("""SELECT id FROM executions WHERE id = ANY(:ids) AND status = 'waiting'
                       UNION
                       SELECT execution_id FROM human_tasks
                        WHERE execution_id = ANY(:ids) AND (status = 'open' OR resume_state IN ('pending', 'running'))"""),
            {"ids": ids},
        )
        return {r[0] for r in rows.all()}

    async def cleanup(self) -> None:
        await self._cleanup()

    async def _get_node_outputs(self, execution_id: str) -> list[NodeOutputModel]:
        """Get all node outputs for an execution."""
        stmt = (
            select(NodeOutputModel)
            .where(NodeOutputModel.execution_id == execution_id)
            .order_by(NodeOutputModel.created_at)
        )
        result = await self._session.execute(stmt)
        return list(result.scalars().all())

    @staticmethod
    def _outputs_to_dicts(
        outputs: list[NodeOutputModel],
    ) -> tuple[dict[str, list[Any]], dict[str, Any]]:
        """Convert NodeOutputModels to node_data and node_metrics dicts."""
        node_data: dict[str, list[Any]] = {}
        node_metrics: dict[str, Any] = {}
        for o in outputs:
            node_data[o.node_name] = o.output if isinstance(o.output, list) else []
            if o.metrics:
                node_metrics[o.node_name] = o.metrics
        return node_data, node_metrics

    def _to_execution_record(
        self,
        db_execution: ExecutionModel,
        node_outputs: list[NodeOutputModel],
        node_metrics: dict[str, Any],
    ) -> ExecutionRecord:
        """Convert database model to ExecutionRecord."""
        from ..engine.types import ExecutionError, ExecutionRecord, NodeData

        # Reconstruct node data from node_outputs
        node_data: dict[str, list[NodeData]] = {}
        errors: list[ExecutionError] = []
        for o in node_outputs:
            items = o.output if isinstance(o.output, list) else []
            node_data[o.node_name] = [
                NodeData(json=item.get("json", {}), binary=None)
                for item in items
            ]
            if o.status == "error" and o.error:
                m = o.metrics or {}
                errors.append(
                    ExecutionError(
                        node_name=o.node_name,
                        error=o.error,
                        timestamp=o.created_at,
                        error_code=m.get("errorCode"),
                        details=m.get("errorDetails"),
                    )
                )

        return ExecutionRecord(
            id=db_execution.id,
            workflow_id=db_execution.workflow_id,
            workflow_name=db_execution.workflow_name,
            status=db_execution.status,  # type: ignore
            mode=db_execution.mode,  # type: ignore
            start_time=db_execution.start_time,
            end_time=db_execution.end_time,
            node_data=node_data,
            errors=errors,
            node_metrics=node_metrics,
            project_id=getattr(db_execution, "project_id", None),
            environment=getattr(db_execution, "environment", None),
            metadata=dict(db_execution.exec_metadata or {}),
        )
