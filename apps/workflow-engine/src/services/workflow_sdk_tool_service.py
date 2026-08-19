"""The workflow SDK as an agent toolkit (HANDOFF-SDK-COMPLETION.md §2).

Provider for ``agent_tool_resolver``'s ``"sdk"`` source. THREE tools that
together make an agent a workflow author *and* operator:

``build_workflow``
    Takes a complete Python script written against the injected SDK, executes
    it in the subprocess sandbox (never in-process — agent scripts are
    untrusted by definition), validates the graph even if the script forgot
    to, and on success persists a real workflow through ``WorkflowRepository``.
``list_workflows``
    Discover what exists: id, name, active, node count — so the agent can
    find its own earlier work (or anyone's) instead of rebuilding it.
``run_workflow``
    Execute any saved workflow by id with an input payload and get every
    node's output back. ``build_workflow`` → ``run_workflow`` → feed the
    output into the next ``run_workflow`` is how an agent CHAINS workflows.

The tool dict carries a ``system_prompt_appendix`` — ``signature_reference()``
plus the usage contract — which ``AgentRunService._execute`` splices into the
agent's system prompt and strips before the tool list reaches the model. The
agent therefore sees the full SDK surface up front instead of discovering it
through failed calls.

Binding shape (agent_tool_bindings row): ``source="sdk"``, ``tool_key`` free
(conventionally ``build_workflow``), optional ``alias`` renames the tool.
"""

from __future__ import annotations

import logging
from typing import Any

from ..engine.workflow_sdk import signature_reference
from ..engine.workflow_sdk_sandbox import (
    execute_script_isolated,
    workflow_from_payload,
)

logger = logging.getLogger(__name__)

TOOL_NAME = "build_workflow"

_TOOL_DESCRIPTION = (
    "Build (and by default persist) a workflow from a Python script written "
    "against the workflow SDK documented in your system prompt. The script runs "
    "in an isolated sandbox; every constructor call adds a node, `>>` wires "
    "nodes, and validate() should be the last line. On any script error or "
    "validation problem nothing is persisted and the exact errors are returned "
    "— fix the script and call again. Set dry_run=true to only build and "
    "validate (returns the graph summary, persists nothing)."
)

_INPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "script": {
            "type": "string",
            "description": (
                "Complete Python script using ONLY the SDK names from the "
                "system prompt (no imports). End with validate()."
            ),
        },
        "name": {
            "type": "string",
            "description": "Workflow name to persist under (default: agent_workflow).",
        },
        "dry_run": {
            "type": "boolean",
            "description": "Build and validate only; do not persist. Default false.",
        },
    },
    "required": ["script"],
}

_LIST_DESCRIPTION = (
    "List saved workflows (id, name, active, node count), newest first. Use "
    "the optional `query` to filter by name substring. Use this to find an "
    "existing workflow before building a duplicate, or to get the id of a "
    "workflow you built earlier."
)

_RUN_DESCRIPTION = (
    "Execute a saved workflow by id and wait for it to finish. `input` is the "
    "JSON payload the workflow's trigger receives. Returns the execution "
    "status, an execution_id, each node's output, and any errors. To CHAIN "
    "workflows: run one, take what you need from its outputs, and pass it as "
    "the next run_workflow's input. This tool performs whatever real reads "
    "and writes the workflow's nodes perform."
)

_PROMPT_APPENDIX_HEADER = (
    "## Workflow toolkit — build_workflow / list_workflows / run_workflow\n"
    "You can create workflows (build_workflow returns a workflow_id), discover\n"
    "them (list_workflows), and execute them (run_workflow). Chain workflows by\n"
    "feeding one run's outputs into the next run's input. When calling\n"
    "build_workflow, write the script against exactly this SDK:\n"
)


def _auto_layout(workflow: Any) -> None:
    """Grid positions by BFS depth from the trigger(s) — SDK scripts carry no
    coordinates, and the studio canvas expects every node to have some."""
    adjacency: dict[str, list[str]] = {}
    indegree: dict[str, int] = {n.name: 0 for n in workflow.nodes}
    for c in workflow.connections:
        adjacency.setdefault(c.source_node, []).append(c.target_node)
        if c.target_node in indegree:
            indegree[c.target_node] += 1

    depth: dict[str, int] = {}
    frontier = [n.name for n in workflow.nodes if indegree.get(n.name, 0) == 0]
    for name in frontier:
        depth[name] = 0
    while frontier:
        current = frontier.pop(0)
        for nxt in adjacency.get(current, []):
            if nxt not in depth or depth[current] + 1 > depth[nxt]:
                depth[nxt] = depth[current] + 1
                frontier.append(nxt)

    rows: dict[int, int] = {}
    for node in workflow.nodes:
        d = depth.get(node.name, 0)
        row = rows.get(d, 0)
        rows[d] = row + 1
        node.position = {"x": 120 + d * 280, "y": 120 + row * 160}


def _summarize(payload: dict[str, Any]) -> dict[str, Any]:
    wf = payload["workflow"]
    return {
        "nodes": [{"name": n["name"], "type": n["type"]} for n in wf["nodes"]],
        "connections": [
            f"{c['source_node']}.{c.get('source_output', 'main')} -> {c['target_node']}"
            for c in wf["connections"]
        ],
    }


async def build_agent_tools(bindings: list[Any], session_factory: Any) -> list[dict[str, Any]]:
    """Provider contract: ``fn(bindings, session_factory) -> list[tool_dict]``.

    ``session_factory`` is the factory per the resolver's contract; a legacy
    caller passing a live session gets a working tool whose persistence uses
    that session directly (single-call safety is the caller's problem then).
    """
    if not bindings:
        return []

    factory = session_factory if callable(session_factory) else None
    legacy_session = None if factory else session_factory

    # One tool no matter how many sdk-source bindings; first alias wins.
    alias = next(
        (a for a in (_attr(b, "alias", None) for b in bindings) if a), None
    )
    tool_name = alias or TOOL_NAME

    async def execute(
        script: str, name: str | None = None, dry_run: bool = False, **_ignored: Any
    ) -> dict[str, Any]:
        workflow_name = (name or "").strip() or "agent_workflow"
        payload = await execute_script_isolated(script, workflow_name=workflow_name)
        if not payload["ok"]:
            return {
                "ok": False,
                "error": payload["error"],
                "problems": payload["problems"],
                # The partial graph tells the agent which lines DID work.
                "partial_graph": _summarize(payload),
                "persisted": False,
            }
        if dry_run:
            return {
                "ok": True,
                "dry_run": True,
                "persisted": False,
                "workflow": _summarize(payload),
            }

        workflow = workflow_from_payload(payload)
        _auto_layout(workflow)
        from ..repositories.workflow_repository import WorkflowRepository

        if factory is not None:
            async with factory() as session:
                stored = await WorkflowRepository(session).create(workflow)
        elif legacy_session is not None:
            stored = await WorkflowRepository(legacy_session).create(workflow)
        else:
            return {
                "ok": False,
                "error": "no database session available to persist the workflow",
                "persisted": False,
                "workflow": _summarize(payload),
            }
        return {
            "ok": True,
            "persisted": True,
            "workflow_id": stored.id,
            "name": stored.name,
            "workflow": _summarize(payload),
        }

    async def list_workflows(query: str | None = None, **_ignored: Any) -> dict[str, Any]:
        from ..repositories.workflow_repository import WorkflowRepository

        async def _list(session: Any) -> dict[str, Any]:
            rows = await WorkflowRepository(session).list()
            q = (query or "").strip().lower()
            items = [
                {
                    "id": w.id,
                    "name": w.name,
                    "active": w.active,
                    "nodes": len(w.workflow.nodes),
                }
                for w in rows
                if not q or q in w.name.lower()
            ]
            return {"ok": True, "count": len(items), "workflows": items[:50]}

        if factory is not None:
            async with factory() as session:
                return await _list(session)
        if legacy_session is not None:
            return await _list(legacy_session)
        return {"ok": False, "error": "no database session available"}

    async def run_workflow(
        workflow_id: str, input: dict[str, Any] | None = None, **_ignored: Any
    ) -> dict[str, Any]:
        from ..core.exceptions import WorkflowExecutionError, WorkflowNotFoundError
        from ..engine.node_registry import node_registry
        from ..repositories import ExecutionRepository, WorkflowRepository
        from ..services.node_service import NodeService
        from ..services.workflow_service import WorkflowService

        async def _run(session: Any) -> dict[str, Any]:
            service = WorkflowService(
                WorkflowRepository(session),
                ExecutionRepository(session, max_records=100),
                NodeService(node_registry),
                node_registry,
            )
            try:
                response = await service.run_workflow(workflow_id, input or None)
            except WorkflowNotFoundError:
                return {"ok": False, "error": f"workflow '{workflow_id}' not found"}
            except WorkflowExecutionError as exc:
                return {"ok": False, "error": str(exc)}
            return {
                "ok": response.status == "success",
                "status": response.status,
                "execution_id": response.execution_id,
                "outputs": _cap_outputs(response.data),
                "errors": [
                    {"node": e.node_name, "message": e.error} for e in response.errors
                ],
            }

        if factory is not None:
            async with factory() as session:
                return await _run(session)
        if legacy_session is not None:
            return await _run(legacy_session)
        return {"ok": False, "error": "no database session available"}

    return [
        {
            "name": tool_name,
            "description": _TOOL_DESCRIPTION,
            "input_schema": _INPUT_SCHEMA,
            "execute": execute,
            "system_prompt_appendix": _PROMPT_APPENDIX_HEADER + signature_reference(),
        },
        {
            "name": "list_workflows",
            "description": _LIST_DESCRIPTION,
            "input_schema": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "Optional name filter (case-insensitive substring).",
                    },
                },
            },
            "execute": list_workflows,
        },
        {
            "name": "run_workflow",
            "description": _RUN_DESCRIPTION,
            "input_schema": {
                "type": "object",
                "properties": {
                    "workflow_id": {
                        "type": "string",
                        "description": "Id of the workflow to execute (from build_workflow or list_workflows).",
                    },
                    "input": {
                        "type": "object",
                        "description": "JSON payload handed to the workflow's trigger.",
                    },
                },
                "required": ["workflow_id"],
            },
            "execute": run_workflow,
        },
    ]


# Tool results are read by a model with a token budget; a workflow that moved
# megabytes must not dump them into the loop.
_MAX_OUTPUT_CHARS = 20_000


def _cap_outputs(data: dict[str, Any]) -> dict[str, Any]:
    import json

    try:
        serialized = json.dumps(data, default=str)
    except (TypeError, ValueError):
        return {"_error": "outputs were not serializable"}
    if len(serialized) <= _MAX_OUTPUT_CHARS:
        return data
    return {
        "_truncated": True,
        "_original_chars": len(serialized),
        "_nodes": list(data.keys()),
        "_preview": serialized[:_MAX_OUTPUT_CHARS],
    }


def _attr(obj: Any, name: str, default: Any = None) -> Any:
    if isinstance(obj, dict):
        return obj.get(name, default)
    return getattr(obj, name, default)
