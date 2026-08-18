"""Run the AIAgent loop outside a workflow.

``AIAgentNode`` is a 2,745-line node that assumes it is being driven by
``WorkflowRunner``. It isn't rewritten here and it isn't wrapped in a
one-node workflow either — it only reaches for ``ExecutionContext`` on three
lines (``on_event``, ``http_client``, ``workflow_repository``) plus
``execution_id`` / ``node_states`` for expression resolution, so this module
builds a real ``ExecutionContext`` and calls ``execute()`` directly.

Things the loop does that this module has to compensate for:

* ``_emit_event`` invokes the callback synchronously with no error handling —
  see :mod:`.agent_event_recorder`, whose ``__call__`` cannot raise.
* ``execute()`` iterates once per input item, so exactly **one** ``NodeData``
  is passed. More items would silently run the agent more than once.
* ``{{ $vars.X }}`` resolves off the ``execution_variables_var`` contextvar,
  not off ``context.variables``; both are set.
* Nothing in the loop bounds wall-clock time. ``asyncio.wait_for`` does.
* External (MCP / OpenAPI / promoted-workflow) tools arrive through the
  ``_tools`` key on the input item — the documented seam at
  ``ai_agent.py:552-564`` — so no external tool source touches the loop.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

logger = logging.getLogger(__name__)

DEFAULT_MAX_RUN_SECONDS = 900
_HTTP_TIMEOUT_SECONDS = 900.0


@dataclass
class AgentRunSpec:
    """Everything one turn needs. Built by ``AgentRunService`` from a snapshot."""

    run_id: str
    agent_name: str
    model: str
    task: str
    system_prompt: str = ""
    # AIAgent node parameters verbatim (maxIterations, temperature, memoryType…).
    parameters: dict[str, Any] = field(default_factory=dict)
    # inline_config.resolve_tools() form: "calculator" or {"type": …, "parameters": …}
    builtin_tool_specs: list[Any] = field(default_factory=list)
    # The _tools seam: [{name, description, input_schema, execute}]
    extra_tools: list[dict[str, Any]] = field(default_factory=list)
    input_json: dict[str, Any] = field(default_factory=dict)
    variables: dict[str, str] = field(default_factory=dict)
    output_schema: dict[str, Any] | None = None
    memory_key: str | None = None
    max_run_seconds: int = DEFAULT_MAX_RUN_SECONDS


@dataclass
class AgentRunOutcome:
    """Terminal result of one turn."""

    status: str  # success | failed | cancelled
    response: str = ""
    structured: dict[str, Any] | None = None
    tool_calls: list[Any] = field(default_factory=list)
    iterations: int = 0
    tool_call_count: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    llm_time_ms: float = 0.0
    error: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


class AgentRuntime:
    """Drives one ``AIAgentNode.execute()`` call with a real ExecutionContext."""

    def __init__(self, session_factory: Any | None = None) -> None:
        if session_factory is None:
            from ..db.session import async_session_factory

            session_factory = async_session_factory
        self._session_factory = session_factory

    async def run(self, spec: AgentRunSpec, recorder: Any | None = None) -> AgentRunOutcome:
        """Execute the agent loop. Raises only ``asyncio.CancelledError``."""
        import httpx

        from ..nodes.ai.ai_agent import AIAgentNode
        from ..repositories.workflow_repository import WorkflowRepository
        from .logging import execution_id_var, execution_variables_var
        from .types import ExecutionContext, NodeData, NodeDefinition, Workflow

        exec_token = execution_id_var.set(spec.run_id)
        # $vars resolve off this contextvar inside ExpressionEngine.create_context.
        # Setting context.variables alone leaves every {{ $vars.X }} empty.
        vars_token = execution_variables_var.set(dict(spec.variables or {}))

        started = datetime.now()
        try:
            async with httpx.AsyncClient(
                timeout=_HTTP_TIMEOUT_SECONDS, follow_redirects=True
            ) as http_client:
                async with self._session_factory() as db_session:
                    context = ExecutionContext(
                        # A synthetic, node-less workflow: the loop never walks it,
                        # it only needs the object to exist.
                        workflow=Workflow(
                            name=f"agent:{spec.agent_name}", nodes=[], connections=[]
                        ),
                        execution_id=spec.run_id,
                        start_time=started,
                        mode="manual",
                    )
                    context.on_event = recorder
                    context.http_client = http_client
                    context.workflow_repository = WorkflowRepository(db_session)
                    context.variables = dict(spec.variables or {})

                    node_def = NodeDefinition(
                        name=spec.agent_name,
                        type="AIAgent",
                        parameters=self._build_parameters(spec),
                    )

                    item: dict[str, Any] = dict(spec.input_json or {})
                    if spec.extra_tools:
                        # THE SEAM (ai_agent.py:552-564): tools merged from the
                        # input item. Executors are live closures — this is why
                        # the run snapshot stores binding metadata only.
                        item["_tools"] = spec.extra_tools

                    try:
                        result = await asyncio.wait_for(
                            AIAgentNode().execute(
                                context, node_def, [NodeData(json=item)]
                            ),
                            timeout=spec.max_run_seconds,
                        )
                    except asyncio.TimeoutError:
                        logger.warning(
                            "agent run %s exceeded %ss", spec.run_id, spec.max_run_seconds
                        )
                        return AgentRunOutcome(
                            status="failed",
                            error=(
                                f"Agent run exceeded its {spec.max_run_seconds}s "
                                "wall-clock budget"
                            ),
                        )

                    return self._to_outcome(result)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 — a failed run is data, not a crash
            logger.exception("agent run %s failed", spec.run_id)
            return AgentRunOutcome(status="failed", error=f"{type(exc).__name__}: {exc}")
        finally:
            execution_variables_var.reset(vars_token)
            execution_id_var.reset(exec_token)

    # -- helpers -----------------------------------------------------------

    def _build_parameters(self, spec: AgentRunSpec) -> dict[str, Any]:
        """Agent settings + the fields the run controls. Run fields win."""
        parameters: dict[str, Any] = dict(spec.parameters or {})
        parameters["model"] = spec.model
        parameters["systemPrompt"] = spec.system_prompt or ""
        parameters["task"] = spec.task
        parameters["builtinTools"] = list(spec.builtin_tool_specs or [])
        if spec.output_schema:
            parameters["outputSchema"] = spec.output_schema
        if spec.memory_key and parameters.get("memoryType", "none") not in (None, "", "none"):
            # Never let the node fall back to its "default" session id: that
            # key is shared by every agent in the process.
            parameters["memorySessionId"] = spec.memory_key
        return parameters

    def _to_outcome(self, result: Any) -> AgentRunOutcome:
        """Unpack ``NodeExecutionResult`` into a flat terminal record."""
        metadata: dict[str, Any] = dict(getattr(result, "metadata", {}) or {})
        outputs = getattr(result, "outputs", {}) or {}
        items = outputs.get("main") or []

        payload: dict[str, Any] = {}
        if items:
            candidate = getattr(items[0], "json", None)
            if isinstance(candidate, dict):
                payload = candidate

        structured = payload.get("structured")
        if not isinstance(structured, dict):
            structured = None

        tool_calls = payload.get("toolCalls")
        if not isinstance(tool_calls, list):
            tool_calls = []

        return AgentRunOutcome(
            status="success",
            response=payload.get("response") or "",
            structured=structured,
            tool_calls=tool_calls,
            iterations=int(metadata.get("agentIterations") or payload.get("iterations") or 0),
            tool_call_count=int(metadata.get("toolCallCount") or len(tool_calls)),
            input_tokens=int(metadata.get("inputTokens") or 0),
            output_tokens=int(metadata.get("outputTokens") or 0),
            llm_time_ms=float(metadata.get("llmResponseTimeMs") or 0.0),
            metadata=metadata,
        )
