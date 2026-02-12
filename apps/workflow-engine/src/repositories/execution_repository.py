"""Execution repository for database persistence."""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING, Literal

from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import select

from ..db.models import ExecutionModel

if TYPE_CHECKING:
    from ..engine.types import ExecutionContext, ExecutionRecord


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
            node_data={},
            errors=[],
        )

        self._session.add(db_execution)
        await self._session.commit()
        await self._session.refresh(db_execution)

        # Cleanup old records
        await self._cleanup()

        return self._to_execution_record(db_execution)

    async def complete(
        self,
        context: ExecutionContext,
        workflow_id: str,
        workflow_name: str,
    ) -> ExecutionRecord:
        """Update execution with final state."""
        from ..engine.types import ExecutionRecord

        db_execution = await self._session.get(ExecutionModel, context.execution_id)

        if not db_execution:
            # Create if doesn't exist (shouldn't happen normally)
            db_execution = ExecutionModel(
                id=context.execution_id,
                workflow_id=workflow_id,
                workflow_name=workflow_name,
                status="running",
                mode=context.mode,
                start_time=context.start_time,
                node_data={},
                errors=[],
            )
            self._session.add(db_execution)

        # Update record
        db_execution.status = "failed" if context.errors else "success"
        db_execution.end_time = datetime.now()

        # Serialize node data
        node_data = {}
        for node_name, items in context.node_states.items():
            node_data[node_name] = [
                {"json": item.json, "binary": None}  # Skip binary for now
                for item in items
            ]
        db_execution.node_data = node_data

        # Serialize errors
        db_execution.errors = [
            {
                "node_name": e.node_name,
                "error": e.error,
                "timestamp": e.timestamp.isoformat(),
            }
            for e in context.errors
        ]

        # Serialize node metrics
        db_execution.node_metrics = context.node_metrics

        await self._session.commit()
        await self._session.refresh(db_execution)

        return self._to_execution_record(db_execution)

    async def get(self, execution_id: str) -> ExecutionRecord | None:
        """Get an execution record by ID."""
        db_execution = await self._session.get(ExecutionModel, execution_id)
        if not db_execution:
            return None
        return self._to_execution_record(db_execution)

    async def list(self, workflow_id: str | None = None) -> list[ExecutionRecord]:
        """List execution records, optionally filtered by workflow ID."""
        statement = select(ExecutionModel).order_by(ExecutionModel.start_time.desc())

        if workflow_id:
            statement = statement.where(ExecutionModel.workflow_id == workflow_id)

        result = await self._session.execute(statement)
        executions = result.scalars().all()

        return [self._to_execution_record(e) for e in executions]

    async def delete(self, execution_id: str) -> bool:
        """Delete an execution record."""
        db_execution = await self._session.get(ExecutionModel, execution_id)
        if not db_execution:
            return False

        await self._session.delete(db_execution)
        await self._session.commit()
        return True

    async def clear(self) -> None:
        """Clear all execution records."""
        statement = select(ExecutionModel)
        result = await self._session.execute(statement)
        executions = result.scalars().all()

        for execution in executions:
            await self._session.delete(execution)

        await self._session.commit()

    async def _cleanup(self) -> None:
        """Remove old records if over max."""
        # Count total records
        statement = select(ExecutionModel).order_by(ExecutionModel.start_time.desc())
        result = await self._session.execute(statement)
        executions = result.scalars().all()

        if len(executions) > self._max_records:
            # Delete oldest records
            to_delete = executions[self._max_records:]
            for execution in to_delete:
                await self._session.delete(execution)
            await self._session.commit()

    def _to_execution_record(self, db_execution: ExecutionModel) -> ExecutionRecord:
        """Convert database model to ExecutionRecord."""
        from ..engine.types import ExecutionError, ExecutionRecord, NodeData

        # Reconstruct node data
        node_data = {}
        for node_name, items in db_execution.node_data.items():
            node_data[node_name] = [
                NodeData(json=item.get("json", {}), binary=None)
                for item in items
            ]

        # Reconstruct errors
        errors = [
            ExecutionError(
                node_name=e["node_name"],
                error=e["error"],
                timestamp=datetime.fromisoformat(e["timestamp"]),
            )
            for e in db_execution.errors
        ]

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
            node_metrics=db_execution.node_metrics,
        )
