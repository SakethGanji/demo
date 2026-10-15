"""Execution repository for database persistence."""

from __future__ import annotations

import logging
from datetime import datetime
from typing import TYPE_CHECKING, Any, Literal

from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import select

from ..db.models import ExecutionModel, NodeOutputModel

if TYPE_CHECKING:
    from ..engine.types import ExecutionContext, ExecutionRecord

logger = logging.getLogger(__name__)


_MODE_TRIGGER = {"manual": "manual", "webhook": "webhook", "cron": "cron", "retry": "retry", "app": "app",
                 "api": "public", "sandbox": "draft_test"}
TRIGGER_TYPES = {"manual", "webhook", "cron", "retry", "route_test", "draft_test", "generate_self_test", "app",
                 "app_preview", "api_key", "public"}


def default_trigger_type(mode: str | None) -> str:
    """Review C2: every execution carries metadata.trigger.type; callers that know better set it."""
    return _MODE_TRIGGER.get(mode or "", mode or "manual")


class ExecutionRepository:
    """Repository for execution history persistence."""

    def __init__(self, session: AsyncSession, max_records: int = 100) -> None:
        self._session = session
        self._max_records = max_records

    async def start(
        self,
        execution_id: str,
        workflow_id: str,
        workflow_name: str,
        mode: str,
        project_id: str | None = None,
        environment: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> ExecutionRecord:
        """Create a new execution record when workflow starts."""
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
        )

        self._session.add(db_execution)
        await self._session.commit()
        await self._session.refresh(db_execution)

        # Cleanup old records
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

        # Update execution status
        db_execution.status = "failed" if context.errors else "success"
        db_execution.end_time = datetime.now()
        db_execution.error_count = len(context.errors)
        db_execution.completed_nodes = len(context.node_states)
        db_execution.total_nodes = len(context.node_states)

        # Persist per-node outputs into node_outputs table
        for node_name, items in context.node_states.items():
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

        await self._session.commit()
        await self._session.refresh(db_execution)

        # Read back node outputs for the return value
        node_outputs = await self._get_node_outputs(context.execution_id)
        node_data, node_metrics = self._outputs_to_dicts(node_outputs)
        record = self._to_execution_record(db_execution, node_outputs, node_metrics)
        if created_here:
            await self._cleanup()
        return record

    async def get(self, execution_id: str) -> ExecutionRecord | None:
        """Get an execution record by ID."""
        db_execution = await self._session.get(ExecutionModel, execution_id)
        if not db_execution:
            return None

        node_outputs = await self._get_node_outputs(execution_id)
        _, node_metrics = self._outputs_to_dicts(node_outputs)
        return self._to_execution_record(db_execution, node_outputs, node_metrics)

    async def list(
        self, workflow_id: str | None = None, project_id: str | None = None,
        mode: str | None = None, status: str | None = None, limit: int | None = None,
        before: str | None = None, route_id: str | None = None, environment: str | None = None,
        trigger: str | None = None,
    ) -> list[ExecutionRecord]:
        """List execution records, optionally filtered by workflow / project / mode / status."""
        statement = select(ExecutionModel).order_by(ExecutionModel.start_time.desc())

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

    async def find_latest_successful(
        self, workflow_id: str
    ) -> ExecutionRecord | None:
        """Find the latest successful execution for a workflow."""
        statement = (
            select(ExecutionModel)
            .where(
                ExecutionModel.workflow_id == workflow_id,
                ExecutionModel.status == "success",
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

    async def mark_failed(self, execution_id: str, error: str) -> None:
        db_execution = await self._session.get(ExecutionModel, execution_id)
        if not db_execution:
            return
        db_execution.status = "failed"
        db_execution.end_time = datetime.now()
        db_execution.error_count = max(1, db_execution.error_count or 0)
        db_execution.exec_metadata = {**(db_execution.exec_metadata or {}), "error": error}
        await self._session.commit()

    async def cancel(self, execution_id: str) -> bool:
        """Mark an execution as cancelled."""
        db_execution = await self._session.get(ExecutionModel, execution_id)
        if not db_execution:
            return False
        db_execution.status = "cancelled"
        db_execution.cancelled_at = datetime.now()
        db_execution.end_time = datetime.now()
        await self._session.commit()
        return True

    async def clear(self) -> None:
        """Clear all execution records and their node outputs."""
        # Delete all node outputs first
        stmt = select(NodeOutputModel)
        result = await self._session.execute(stmt)
        for output in result.scalars().all():
            await self._session.delete(output)

        statement = select(ExecutionModel)
        result = await self._session.execute(statement)
        executions = result.scalars().all()

        for execution in executions:
            await self._session.delete(execution)

        await self._session.commit()

    async def _cleanup(self) -> None:
        """Keep the newest ``max_records`` executions (review D6: called on every insert path).

        Pruned executions take their node outputs and broker-call rows with them."""
        from sqlalchemy import delete as sa_delete

        from ..db.models import BrokerCallModel

        ids = (await self._session.execute(
            select(ExecutionModel.id).order_by(ExecutionModel.start_time.desc(), ExecutionModel.id.desc())
            .offset(self._max_records))).scalars().all()
        if not ids:
            return
        await self._session.execute(sa_delete(NodeOutputModel).where(NodeOutputModel.execution_id.in_(ids)))
        await self._session.execute(sa_delete(BrokerCallModel).where(BrokerCallModel.execution_id.in_(ids)))
        await self._session.execute(sa_delete(ExecutionModel).where(ExecutionModel.id.in_(ids)))
        await self._session.commit()

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
