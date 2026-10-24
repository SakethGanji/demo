"""Run an unsaved workflow definition for a notebook cell (a copy of ``WorkflowService.run_adhoc_workflow`` that takes
``metadata``, so the execution row carries the notebook attribution). Same runner, egress allowlist, variables, secrets
and API catalog as every other run.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from ...db.session import async_session_factory


async def run_definition(definition: dict[str, Any], *, project_id: str, environment: str | None,
                         metadata: dict[str, Any] | None = None, input_data: dict[str, Any] | None = None,
                         timeout_s: float = 60.0) -> Any:
    """Run ``definition`` ({name, nodes, connections}) and return the finished ``ExecutionContext``."""
    import asyncio

    from ...engine.types import NodeData
    from ...engine.workflow_runner import WorkflowRunner
    from ...repositories.execution_repository import ExecutionRepository
    from ...repositories.workflow_repository import WorkflowRepository
    from ...schemas.workflow import WorkflowCreateRequest
    from ...utils.ids import execution_id as gen_exec_id
    from ..api_catalog import load_api_specs
    from ..execution_service import _egress_allowlist
    from ..project_service import effective_environment, resolve_project
    from ..subworkflow_service import DbSubworkflowInvoker
    from ..variable_loader import load_run_secrets, load_run_variables

    workflow = WorkflowCreateRequest(**{**definition, "project_id": project_id}).to_workflow()
    async with async_session_factory() as session:
        exec_repo = ExecutionRepository(session)
        wf_repo = WorkflowRepository(session)
        project = await resolve_project(session, project_id)
        env = effective_environment(project, environment or "default")
        variables = await load_run_variables(environment=env, project_id=project_id)
        secrets = await load_run_secrets(environment=env, project_id=project_id)
        runner = WorkflowRunner(db_session_factory=async_session_factory)
        start = runner.find_start_node(workflow)
        if start is None:
            raise ValueError("no start node")
        if input_data:
            initial = [NodeData(json={"body": input_data, "headers": {}, "query": {}, "method": "POST",
                                      "triggeredAt": datetime.now().isoformat()})]
        else:
            initial = [NodeData(json={"triggeredAt": datetime.now().isoformat(), "mode": "manual"})]
        exec_id = gen_exec_id()
        meta = {"labels": {"env": env}, "trigger": {"type": "notebook"}, **(metadata or {})}
        await exec_repo.start(exec_id, "adhoc", workflow.name, "manual", project_id=project_id, environment=env,
                              metadata=meta)
        coro = runner.run(
            workflow, start.name, initial, "manual",
            workflow_repository=wf_repo, execution_id=exec_id, variables=variables, secret_values=secrets,
            project_id=project_id, environment=env,
            extras={"egress_allowlist": _egress_allowlist(project),
                    "api_specs": await load_api_specs(session, workflow.nodes)},
            subworkflow_invoker=DbSubworkflowInvoker(),
        )
        try:
            context = await asyncio.wait_for(coro, timeout=timeout_s)
        except asyncio.TimeoutError:
            await exec_repo.mark_failed(exec_id, f"timed out after {timeout_s}s")
            raise
        await exec_repo.complete(context, "adhoc", workflow.name)
        context.nb_secrets = set(secrets or ())  # for the cell to redact what it stores
        return context
