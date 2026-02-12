"""Execution service for business logic."""

from __future__ import annotations

from typing import TYPE_CHECKING

from ..core.exceptions import ExecutionNotFoundError
from ..schemas.execution import (
    ExecutionListItem,
    ExecutionDetailResponse,
    ExecutionErrorSchema,
)

if TYPE_CHECKING:
    from ..repositories import ExecutionRepository, WorkflowRepository


class ExecutionService:
    """Service for execution operations."""

    def __init__(
        self,
        execution_repo: ExecutionRepository,
        workflow_repo: WorkflowRepository,
    ) -> None:
        self._execution_repo = execution_repo
        self._workflow_repo = workflow_repo

    async def list_executions(self, workflow_id: str | None = None) -> list[ExecutionListItem]:
        """List execution history."""
        executions = await self._execution_repo.list(workflow_id)

        return [
            ExecutionListItem(
                id=e.id,
                workflow_id=e.workflow_id,
                workflow_name=e.workflow_name,
                status=e.status,
                mode=e.mode,
                start_time=e.start_time.isoformat(),
                end_time=e.end_time.isoformat() if e.end_time else None,
                error_count=len(e.errors),
            )
            for e in executions
        ]

    async def get_execution(self, execution_id: str) -> ExecutionDetailResponse:
        """Get execution details."""
        execution = await self._execution_repo.get(execution_id)
        if not execution:
            raise ExecutionNotFoundError(execution_id)

        return ExecutionDetailResponse(
            id=execution.id,
            workflow_id=execution.workflow_id,
            workflow_name=execution.workflow_name,
            status=execution.status,
            mode=execution.mode,
            start_time=execution.start_time.isoformat(),
            end_time=execution.end_time.isoformat() if execution.end_time else None,
            errors=[
                ExecutionErrorSchema(
                    node_name=e.node_name,
                    error=e.error,
                    timestamp=e.timestamp.isoformat(),
                )
                for e in execution.errors
            ],
            node_data={
                name: [{"json": d.json} for d in data]
                for name, data in execution.node_data.items()
            },
            node_metrics=execution.node_metrics,
        )

    async def delete_execution(self, execution_id: str) -> bool:
        """Delete an execution record."""
        deleted = await self._execution_repo.delete(execution_id)
        if not deleted:
            raise ExecutionNotFoundError(execution_id)
        return True

    async def clear_executions(self) -> int:
        """Clear all execution records and return count."""
        executions = await self._execution_repo.list()
        count = len(executions)
        await self._execution_repo.clear()
        return count
