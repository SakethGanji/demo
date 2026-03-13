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
        mode: Literal["manual", "webhook", "cron"],
    ) -> ExecutionRecord:
        """Create a new execution record when workflow starts."""
        from ..engine.types import ExecutionRecord

        db_execution = ExecutionModel(
            id=execution_id,
            workflow_id=workflow_id,
            workflow_name=workflow_name,
            status="running",
            mode=mode,
            start_time=datetime.now(),
        )

        self._session.add(db_execution)
        await self._session.commit()
        await self._session.refresh(db_execution)

        # Cleanup old records
        await self._cleanup()

        return self._to_execution_record(db_execution, [], {})

    async def complete(
        self,
        context: ExecutionContext,
        workflow_id: str,
        workflow_name: str,
    ) -> ExecutionRecord:
        """Update execution with final state."""
        db_execution = await self._session.get(ExecutionModel, context.execution_id)

        if not db_execution:
            db_execution = ExecutionModel(
                id=context.execution_id,
                workflow_id=workflow_id,
                workflow_name=workflow_name,
                status="running",
                mode=context.mode,
                start_time=context.start_time,
            )
            self._session.add(db_execution)
            await self._session.flush()

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

        return self._to_execution_record(db_execution, node_outputs, node_metrics)

    async def get(self, execution_id: str) -> ExecutionRecord | None:
        """Get an execution record by ID."""
        db_execution = await self._session.get(ExecutionModel, execution_id)
        if not db_execution:
            return None

        node_outputs = await self._get_node_outputs(execution_id)
        _, node_metrics = self._outputs_to_dicts(node_outputs)
        return self._to_execution_record(db_execution, node_outputs, node_metrics)

    async def list(self, workflow_id: str | None = None) -> list[ExecutionRecord]:
        """List execution records, optionally filtered by workflow ID."""
        statement = select(ExecutionModel).order_by(ExecutionModel.start_time.desc())

        if workflow_id:
            statement = statement.where(ExecutionModel.workflow_id == workflow_id)

        result = await self._session.execute(statement)
        executions = result.scalars().all()

        records = []
        for e in executions:
            node_outputs = await self._get_node_outputs(e.id)
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
        """Remove old records if over max."""
        statement = select(ExecutionModel).order_by(ExecutionModel.start_time.desc())
        result = await self._session.execute(statement)
        executions = result.scalars().all()

        if len(executions) > self._max_records:
            to_delete = executions[self._max_records:]
            for execution in to_delete:
                # Delete node outputs first
                stmt = select(NodeOutputModel).where(
                    NodeOutputModel.execution_id == execution.id
                )
                res = await self._session.execute(stmt)
                for output in res.scalars().all():
                    await self._session.delete(output)
                await self._session.delete(execution)
            await self._session.commit()

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
                errors.append(
                    ExecutionError(
                        node_name=o.node_name,
                        error=o.error,
                        timestamp=o.created_at,
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
        )
