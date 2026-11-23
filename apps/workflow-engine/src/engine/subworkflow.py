"""Sub-workflow calls (Stage 8, DESIGN-STAGE8-REUSE section 3).

This module is the frozen interface between the engine (``ExecuteWorkflow``, ``WorkflowTool``) and whatever runs a
child workflow. The node builds a ``SubworkflowCall`` and asks ``context.subworkflow_invoker`` to ``invoke`` it; the
answer is a ``SubworkflowResult``. Two invokers exist:

* ``InMemoryInvoker`` (here): loads the child *draft* through ``context.workflow_repository``, runs it in process and
  writes nothing. Used when the context has no invoker (pure engine tests, legacy callers).
* ``DbSubworkflowInvoker`` (``services/subworkflow_service.py``): resolves versions, writes a child execution row.

The guards, the return-node rule and the failure shaping live here as plain functions so both invokers behave the
same way.
"""

from __future__ import annotations

import asyncio
import math
import time
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Callable, Literal, Protocol, runtime_checkable

from .types import (
    ExecutionContext,
    ExecutionError,
    ExecutionEvent,
    NodeData,
    RecursionLimitError,
    Workflow,
)

# --- error codes (section 3.4, 3.5, 3.6, 2.1) ---------------------------------------------------------------------

SUBWORKFLOW_NOT_FOUND = "SUBWORKFLOW_NOT_FOUND"
SUBWORKFLOW_VERSION_MISSING = "SUBWORKFLOW_VERSION_MISSING"
SUBWORKFLOW_NOT_CALLABLE = "SUBWORKFLOW_NOT_CALLABLE"
RECURSION_LIMIT = "RECURSION_LIMIT"
SUBWORKFLOW_CYCLE = "SUBWORKFLOW_CYCLE"
APPROVAL_IN_SUBWORKFLOW = "APPROVAL_IN_SUBWORKFLOW"
SUBWORKFLOW_TIMEOUT = "SUBWORKFLOW_TIMEOUT"
SUBWORKFLOW_FAILED = "SUBWORKFLOW_FAILED"
SUBWORKFLOW_INPUT_INVALID = "SUBWORKFLOW_INPUT_INVALID"

#: guard codes in the order they are checked (section 3.4); the first four need a database to be fully applied
GUARD_CODES = (
    SUBWORKFLOW_NOT_FOUND,
    SUBWORKFLOW_VERSION_MISSING,
    SUBWORKFLOW_NOT_CALLABLE,
    RECURSION_LIMIT,
    SUBWORKFLOW_CYCLE,
    APPROVAL_IN_SUBWORKFLOW,
)

TRIGGER_TYPE = "ExecuteWorkflowTrigger"
DEFAULT_TIMEOUT_SECONDS = 300.0
MAX_TIMEOUT_SECONDS = 3600.0


class SubworkflowError(Exception):
    """A failed sub-workflow call. ``code`` and ``details`` are read by the runner into the node's ``ExecutionError``."""

    def __init__(self, code: str, message: str, details: dict[str, Any] | None = None) -> None:
        super().__init__(message)
        self.code = code
        self.details = details or {}


# --- the frozen interface (section 3.2) ---------------------------------------------------------------------------


@dataclass(frozen=True)
class SubworkflowCall:
    parent_context: ExecutionContext
    parent_node: str
    workflow_id: str
    version: str | int  # "draft" | "latest" | version_number
    items: list[NodeData]  # already mapped by the node
    timeout_seconds: float
    test_inline: bool = False  # section 6.3


@dataclass
class SubworkflowResult:
    items: list[NodeData]
    child_execution_id: str | None
    child_workflow_id: str
    child_version_number: int | None  # None = draft
    status: Literal["success", "failed", "timeout"]
    error: ExecutionError | None = None
    # Additions beyond section 3.2, all optional so an invoker may ignore them. The legacy "merged" output
    # (outputMode "merged") needs the child's name and node states; see ``result_from_context``.
    child_name: str | None = None
    child_context: ExecutionContext | None = None


@runtime_checkable
class SubworkflowInvoker(Protocol):
    async def invoke(self, call: SubworkflowCall) -> SubworkflowResult: ...


# --- small pure helpers -------------------------------------------------------------------------------------------


def find_trigger_node(workflow: Workflow):
    """The child's entry point: its ``ExecuteWorkflowTrigger`` (never a Webhook the child may also have)."""
    return next((n for n in workflow.nodes if n.type == TRIGGER_TYPE), None)


def has_approval(workflow: Workflow) -> bool:
    """Direct scan for an ``Approval`` node. Nested children are guarded again when they are called."""
    return any(n.type == "Approval" for n in workflow.nodes)


def call_stack_of(context: ExecutionContext) -> tuple[str, ...]:
    """The workflow ids from the root to this run. A root run without a stack counts as just itself."""
    if context.call_stack:
        return tuple(context.call_stack)
    wid = getattr(context.workflow, "id", None)
    return (wid,) if wid else ()


def effective_timeout(call: SubworkflowCall) -> float:
    """``min(timeout_seconds, deadline - now)``, never negative, capped at one hour."""
    t = call.timeout_seconds
    try:
        t = float(t)
    except (TypeError, ValueError):
        t = DEFAULT_TIMEOUT_SECONDS
    if math.isnan(t):
        t = DEFAULT_TIMEOUT_SECONDS
    t = min(max(t, 0.0), MAX_TIMEOUT_SECONDS)
    deadline = call.parent_context.deadline
    if deadline is not None:
        t = min(t, max(deadline - time.monotonic(), 0.0))
    return t


def check_guards(
    parent: ExecutionContext, child_id: str, child_name: str, workflow: Workflow
) -> SubworkflowError | None:
    """The guards that need no database, in section 3.4 order: not callable, depth, cycle, approval."""
    if find_trigger_node(workflow) is None:
        return SubworkflowError(
            SUBWORKFLOW_NOT_CALLABLE, f"'{child_name}' can't be called: it has no Execute Workflow Trigger."
        )
    if parent.execution_depth + 1 > parent.max_execution_depth:
        return SubworkflowError(
            RECURSION_LIMIT, f"Sub-workflows are nested more than {parent.max_execution_depth} deep."
        )
    stack = call_stack_of(parent)
    if child_id in stack:
        names = {**parent.call_names, child_id: child_name}
        pid = getattr(parent.workflow, "id", None)
        if pid:
            names.setdefault(pid, parent.workflow.name)
        path = list(stack[stack.index(child_id):]) + [child_id]
        chain = " → ".join(f"'{names.get(i, i)}'" for i in path)
        return SubworkflowError(
            SUBWORKFLOW_CYCLE,
            f"{chain}: a workflow can't call itself, directly or indirectly.",
            {"call_stack": list(stack), "workflow_id": child_id},
        )
    if has_approval(workflow):
        return SubworkflowError(
            APPROVAL_IN_SUBWORKFLOW,
            f"'{child_name}' has an Approval step; approvals must be in the top-level workflow.",
        )
    return None


def not_found(workflow_id: str) -> SubworkflowError:
    return SubworkflowError(SUBWORKFLOW_NOT_FOUND, f"Sub-workflow '{workflow_id}' isn't in this project.")


def return_items(workflow: Workflow, context: ExecutionContext) -> list[NodeData]:
    """The child's return node output (section 2.3): the declared return node, else the first ``Respond`` that ran, else the last node that finished.

    A child may name its return node in ``settings.returnNode`` (convert-selection does, for a selection that exits through a
    branch port). Then the output is exactly what that node received: nothing when the run never reached it, instead of the
    last-finished node's stored items, which for a branching node can be the items of the port that was not taken.
    """
    declared = (workflow.settings or {}).get("returnNode") if isinstance(workflow.settings, dict) else None
    if isinstance(declared, str) and any(n.name == declared for n in workflow.nodes):
        return list(context.node_states.get(declared) or [])
    ran = [n for n in workflow.nodes if n.type == "Respond" and context.node_states.get(n.name)]
    if ran:
        def order(n) -> int:
            return int((context.node_metrics.get(n.name) or {}).get("executionOrder") or 10**9)

        return list(context.node_states[min(ran, key=order).name])
    last = context.last_completed_node
    if last and context.node_states.get(last):
        return list(context.node_states[last])
    return []


def failure_of(context: ExecutionContext) -> ExecutionError | None:
    """The first error that failed a finished child run.

    A node whose ``on_error`` is ``continue`` records its error but the run went on past it, so (section 3.6) that counts
    as a success for the parent; only errors from other nodes fail the child.
    """
    continued = {n.name for n in context.workflow.nodes if n.on_error == "continue"}
    return next((e for e in context.errors if e.node_name not in continued), None)


def result_from_context(
    context: ExecutionContext,
    workflow: Workflow,
    *,
    workflow_id: str,
    child_name: str,
    version_number: int | None,
    child_execution_id: str | None = None,
) -> SubworkflowResult:
    """Shape a finished child ``ExecutionContext`` into a ``SubworkflowResult`` (success or failed)."""
    eid = child_execution_id or context.execution_id
    err = failure_of(context)
    if err is not None:
        return SubworkflowResult(
            items=[], child_execution_id=eid, child_workflow_id=workflow_id, child_version_number=version_number,
            status="failed", error=err, child_name=child_name, child_context=context,
        )
    return SubworkflowResult(
        items=return_items(workflow, context), child_execution_id=eid, child_workflow_id=workflow_id,
        child_version_number=version_number, status="success", child_name=child_name, child_context=context,
    )


def failed_result(
    call: SubworkflowCall, err: SubworkflowError, *, child_name: str | None = None, version_number: int | None = None
) -> SubworkflowResult:
    """A result for a call that never started (a guard refused it)."""
    return SubworkflowResult(
        items=[], child_execution_id=None, child_workflow_id=call.workflow_id, child_version_number=version_number,
        status="failed",
        error=ExecutionError(
            node_name=call.parent_node, error=str(err), timestamp=datetime.now(), error_code=err.code, details=err.details
        ),
        child_name=child_name,
    )


def timeout_result(
    call: SubworkflowCall,
    seconds: float,
    *,
    child_name: str | None = None,
    version_number: int | None = None,
    child_execution_id: str | None = None,
) -> SubworkflowResult:
    label = child_name or call.workflow_id
    return SubworkflowResult(
        items=[], child_execution_id=child_execution_id, child_workflow_id=call.workflow_id,
        child_version_number=version_number, status="timeout",
        error=ExecutionError(
            node_name=call.parent_node,
            error=f"Sub-workflow '{label}' timed out after {seconds:g}s.",
            timestamp=datetime.now(),
            error_code=SUBWORKFLOW_TIMEOUT,
        ),
        child_name=child_name,
    )


def tagged_event_callback(
    parent: ExecutionContext, parent_node: str, workflow_id: str
) -> Callable[[ExecutionEvent], None] | None:
    """Re-emit the child's node events on the parent's stream, marked with the parent node and child id."""
    parent_on_event = parent.on_event
    if parent_on_event is None:
        return None

    def tagged(event: ExecutionEvent) -> None:
        if event.type.value.startswith("node:"):
            event.subworkflow_parent_node = parent_node
            event.subworkflow_id = workflow_id
        parent_on_event(event)

    return tagged


# --- the in-memory invoker ----------------------------------------------------------------------------------------


class InMemoryInvoker:
    """Loads the child draft via ``context.workflow_repository``, runs it via ``WorkflowRunner.run_subworkflow``, writes nothing.

    Applies the guards that need no database: not callable, depth, call-stack cycle, approval. It cannot resolve
    versions, so ``latest`` and numbers run the draft and report ``child_version_number = None``.
    """

    def __init__(self, runner: Any | None = None) -> None:
        self._runner = runner  # tests may pass a runner with a fake node registry; default is a fresh WorkflowRunner

    async def invoke(self, call: SubworkflowCall) -> SubworkflowResult:
        from .workflow_runner import WorkflowRunner

        parent = call.parent_context
        repo = parent.workflow_repository
        stored = await repo.get(call.workflow_id) if repo is not None else None
        if stored is None:
            return failed_result(call, not_found(call.workflow_id))
        workflow = stored.workflow
        name = stored.name or workflow.name or call.workflow_id

        guard = check_guards(parent, call.workflow_id, name, workflow)
        if guard is not None:
            return failed_result(call, guard, child_name=name)

        trigger = find_trigger_node(workflow)
        assert trigger is not None  # check_guards refused a child without one
        seconds = effective_timeout(call)
        run = (self._runner or WorkflowRunner()).run_subworkflow(
            workflow=workflow,
            start_node_name=trigger.name,
            input_data=list(call.items),
            parent_context=parent,
            on_event=tagged_event_callback(parent, call.parent_node, call.workflow_id),
            workflow_id=call.workflow_id,
        )
        try:
            child = await asyncio.wait_for(run, seconds)
        except asyncio.TimeoutError:
            return timeout_result(call, seconds, child_name=name)
        except RecursionLimitError as e:
            return failed_result(call, SubworkflowError(RECURSION_LIMIT, str(e)), child_name=name)
        return result_from_context(child, workflow, workflow_id=call.workflow_id, child_name=name, version_number=None)
