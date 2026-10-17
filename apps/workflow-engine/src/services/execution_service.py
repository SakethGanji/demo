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


def _egress_allowlist(project: Any) -> list[str]:
    """The project's host allowlist for workflow nodes (empty = unrestricted, as for broker calls)."""
    return list(((getattr(project, "settings", None) or {}).get("egress_allowlist")) or [])


class ExecutionService:
    """Service for execution operations."""

    def __init__(
        self,
        execution_repo: ExecutionRepository,
        workflow_repo: WorkflowRepository,
    ) -> None:
        self._execution_repo = execution_repo
        self._workflow_repo = workflow_repo

    async def run_saved_workflow(
        self,
        workflow_id: str,
        input_data: dict[str, Any] | None = None,
        *,
        environment: str | None = "default",
        mode: str | None = None,
        substitute_inputs: bool = True,
        initial_item: dict[str, Any] | None = None,
        version_id: int | None = None,
        timeout_s: float | None = None,
        metadata: dict[str, Any] | None = None,
        extras: dict[str, Any] | None = None,
        on_context_ready: Any | None = None,
        strict_environment: bool = False,
        start_node_name: str | None = None,
        stop_at: str | None = None,
        pre_populated_states: dict[str, list[Any]] | None = None,
        initial_data_override: list[Any] | None = None,
        pending_inputs: dict[str, dict[str, list[Any]]] | None = None,
    ):
        """The single path for running a saved workflow (BACKEND-DESIGN §4.3).

        Used by /api/workflows/{id}/run, published apps, the project router and releases.
        * environment is mapped onto the workflow's project (``effective_environment``).
        * input_data is wrapped webhook-style ({body, headers, query, method}) as before;
          ``initial_item`` replaces the wrapper entirely (route requests).
        * ``substitute_inputs=False`` disables inbound {{ $vars.X }} expansion (anonymous surfaces).
        * ``version_id`` runs a pinned immutable version instead of the draft.
        Returns ``(stored_workflow, ExecutionContext)``. Raises WorkflowNotFoundError /
        WorkflowExecutionError / asyncio.TimeoutError.
        """
        from ..core.exceptions import WorkflowNotFoundError, WorkflowExecutionError
        from ..db.session import async_session_factory
        from ..engine.graph import disabled_trigger_message
        from ..engine.types import NodeData
        from ..engine.workflow_runner import WorkflowRunner
        from ..utils.ids import execution_id as gen_exec_id
        from .project_service import effective_environment, resolve_project
        from .variable_loader import load_run_secrets, load_run_variables
        from .vars_substitution import substitute_vars

        if version_id is not None:
            stored = await self._workflow_repo.get_version_workflow(workflow_id, version_id)
        else:
            stored = await self._workflow_repo.get(workflow_id)
        if not stored:
            raise WorkflowNotFoundError(workflow_id)

        if msg := disabled_trigger_message(stored.workflow.nodes):
            raise WorkflowExecutionError(msg, workflow_id=workflow_id)

        project = await resolve_project(self._workflow_repo._session, stored.project_id)
        env = effective_environment(project, environment, strict=strict_environment)
        runner = WorkflowRunner(db_session_factory=async_session_factory)
        if start_node_name is not None:
            start_node = next((n for n in stored.workflow.nodes if n.name == start_node_name), None)
        else:
            start_node = runner.find_start_node(stored.workflow)
        variables = await load_run_variables(environment=env, project_id=stored.project_id)
        secrets = await load_run_secrets(environment=env, project_id=stored.project_id)
        if substitute_inputs and input_data is not None:
            input_data = substitute_vars(input_data, variables)

        if not start_node:
            raise WorkflowExecutionError("No start node found in workflow", workflow_id=workflow_id)

        if initial_data_override is not None:
            initial_data = initial_data_override
            run_mode = mode or "manual"
        elif initial_item is not None:
            initial_data = [NodeData(json={**initial_item, "triggeredAt": datetime.now().isoformat()})]
            run_mode = mode or "api"
        elif input_data:
            initial_data = [NodeData(json={
                "body": input_data, "headers": {}, "query": {}, "method": "POST",
                "triggeredAt": datetime.now().isoformat(),
            })]
            run_mode = mode or "webhook"
        else:
            initial_data = [NodeData(json={"triggeredAt": datetime.now().isoformat(), "mode": "manual"})]
            run_mode = mode or "manual"

        extras = {**(extras or {}), "egress_allowlist": _egress_allowlist(project)}
        exec_id = gen_exec_id()
        meta = {"labels": {"env": env}, **(metadata or {})}
        await self._execution_repo.start(
            exec_id, stored.id, stored.name, run_mode,
            project_id=stored.project_id, environment=env, metadata=meta,
        )
        if on_context_ready:
            await on_context_ready(exec_id, env, stored)

        coro = runner.run(
            stored.workflow,
            start_node.name,
            initial_data,
            run_mode,
            workflow_repository=self._workflow_repo,
            execution_id=exec_id,
            version_id=version_id,
            variables=variables,
            secret_values=secrets,
            project_id=stored.project_id,
            environment=env,
            extras=extras,
            stop_at=stop_at,
            pre_populated_states=pre_populated_states,
            pending_inputs=pending_inputs,
        )
        if timeout_s:
            try:
                context = await asyncio.wait_for(coro, timeout=timeout_s)
            except asyncio.TimeoutError:
                await self._execution_repo.mark_failed(exec_id, f"timed out after {timeout_s}s")
                raise
        else:
            context = await coro
        context.reused_nodes = set(pre_populated_states or ())
        await self._execution_repo.complete(context, stored.id, stored.name)
        context.extras["secret_values"] = secrets
        return stored, context

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
                    error_code=getattr(e, "error_code", None),
                    details=getattr(e, "details", None),
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

        from .variable_loader import load_run_variables, load_run_secrets
        from .project_service import effective_environment, resolve_project
        project = await resolve_project(self._workflow_repo._session, stored.project_id)
        env = effective_environment(project, "default")
        variables = await load_run_variables(environment=env, project_id=stored.project_id)
        secrets = await load_run_secrets(environment=env, project_id=stored.project_id)

        new_exec_id = runner._generate_id()  # the id the run really uses (returned to the caller, cancellable)

        async def _run() -> None:
            try:
                context = await runner.run(
                    stored.workflow,
                    start_node.name,
                    initial_data,
                    execution.mode,
                    execution_id=new_exec_id,
                    pre_populated_states=pre_populated,
                    variables=variables,
                    secret_values=secrets,
                    project_id=stored.project_id,
                    environment=env,
                    extras={"egress_allowlist": _egress_allowlist(project)},
                )
                async with async_session_factory() as session:
                    from ..repositories.execution_repository import ExecutionRepository
                    exec_repo = ExecutionRepository(session)
                    await exec_repo.complete(context, stored.id, stored.name)
            except Exception:
                logger.exception("Retry execution failed for %s", execution_id)

        task = asyncio.create_task(_run())
        execution_registry.register(new_exec_id, task)

        return new_exec_id

    async def clear_executions(self) -> int:
        """Clear all execution records and return count."""
        executions = await self._execution_repo.list()
        count = len(executions)
        await self._execution_repo.clear()
        return count
