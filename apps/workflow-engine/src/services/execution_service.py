"""Execution service for business logic."""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime
from typing import TYPE_CHECKING, Any

from ..core.exceptions import ExecutionNotFoundError
from ..schemas.execution import (
    ExecutionListItem,
    ExecutionDetailResponse,
    ExecutionErrorSchema,
)

if TYPE_CHECKING:
    from ..repositories import ExecutionRepository, WorkflowRepository

logger = logging.getLogger(__name__)


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

    async def cancel_execution(self, execution_id: str) -> bool:
        """Cancel a running execution."""
        from ..engine import execution_registry

        execution = await self._execution_repo.get(execution_id)
        if not execution:
            raise ExecutionNotFoundError(execution_id)

        if execution.status != "running":
            return False

        # Cancel the in-process task
        cancelled = execution_registry.cancel(execution_id)

        # Update DB status
        await self._execution_repo.cancel(execution_id)
        return True

    async def retry_from_failure(self, execution_id: str) -> str:
        """Retry a failed execution, skipping previously successful nodes."""
        from ..repositories.node_output_repository import NodeOutputRepository
        from ..engine.workflow_runner import WorkflowRunner
        from ..engine.types import NodeData
        from ..engine import execution_registry
        from ..db.session import async_session_factory

        execution = await self._execution_repo.get(execution_id)
        if not execution:
            raise ExecutionNotFoundError(execution_id)

        if execution.status != "failed":
            raise ValueError(f"Can only retry failed executions (status={execution.status})")

        # Load the workflow
        stored = await self._workflow_repo.get(execution.workflow_id)
        if not stored:
            raise ValueError(f"Workflow {execution.workflow_id} not found")

        # Get successful outputs from the failed execution
        async with async_session_factory() as session:
            output_repo = NodeOutputRepository(session)
            successful = await output_repo.get_successful_outputs(execution_id)

        # Build pre_populated_states from successful outputs
        pre_populated: dict[str, list[NodeData]] = {}
        for output in successful:
            items = output.output if isinstance(output.output, list) else []
            pre_populated[output.node_name] = [
                NodeData(json=item.get("json", {})) for item in items
            ]

        # Create and run new execution
        runner = WorkflowRunner(db_session_factory=async_session_factory)
        start_node = runner.find_start_node(stored.workflow)
        if not start_node:
            raise ValueError("No start node found")

        initial_data = [
            NodeData(json={"triggeredAt": datetime.now().isoformat(), "mode": "retry"})
        ]

        async def _run() -> None:
            try:
                context = await runner.run(
                    stored.workflow,
                    start_node.name,
                    initial_data,
                    execution.mode,
                    pre_populated_states=pre_populated,
                )
                async with async_session_factory() as session:
                    from ..repositories.execution_repository import ExecutionRepository
                    exec_repo = ExecutionRepository(session)
                    await exec_repo.complete(context, stored.id, stored.name)
            except Exception:
                logger.exception("Retry execution failed for %s", execution_id)

        task = asyncio.create_task(_run())
        # We don't know the execution_id yet since runner generates it,
        # but we can register using a placeholder
        new_exec_id = runner._generate_id()
        execution_registry.register(new_exec_id, task)

        return new_exec_id

    async def clear_executions(self) -> int:
        """Clear all execution records and return count."""
        executions = await self._execution_repo.list()
        count = len(executions)
        await self._execution_repo.clear()
        return count
