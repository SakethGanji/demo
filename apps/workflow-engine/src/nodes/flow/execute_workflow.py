"""Execute Workflow node - runs another workflow as a typed sub-workflow (Stage 8, DESIGN-STAGE8-REUSE).

The node maps its input into the child's trigger input, asks ``context.subworkflow_invoker`` to run the child (draft,
latest published or a pinned version), and emits the child's return-node items. A failed child is a node error
(``SUBWORKFLOW_FAILED`` or a guard / timeout code), so retry and ``on_error`` apply the way they do for every node.

A node without a ``version`` parameter is a *legacy* node: it keeps today's behaviour (the draft, a single merged
output item). That decision lives in ``normalize_params``.
"""

from __future__ import annotations

import asyncio
import copy
import json
import logging
import math
import time
from typing import Any, TYPE_CHECKING

from ..base import (
    BaseNode,
    NodeTypeDescription,
    NodeInputDefinition,
    NodeOutputDefinition,
    NodeProperty,
    NodePropertyOption,
)

if TYPE_CHECKING:
    from ...engine.subworkflow import SubworkflowResult
    from ...engine.types import ExecutionContext, NodeData, NodeDefinition, NodeExecutionResult

logger = logging.getLogger(__name__)


from ...engine import shapes as sh  # noqa: E402,F401  (kept for contract work in the checker)

DEFAULT_TIMEOUT_SECONDS = 300.0
MAX_TIMEOUT_SECONDS = 3600.0
_INPUT_MODES = ("map", "passThrough", "custom")


def _parse_version(raw: Any) -> str | int:
    if isinstance(raw, bool):
        raise ValueError(f"Invalid sub-workflow version: {raw!r}")
    if isinstance(raw, (int, float)) and float(raw).is_integer() and raw >= 1:
        return int(raw)
    if isinstance(raw, str):
        v = raw.strip().lower()
        if v in ("draft", "latest"):
            return v
        if v.isdigit() and int(v) >= 1:
            return int(v)
    raise ValueError(f"Invalid sub-workflow version: {raw!r}. Use 'draft', 'latest' or a version number.")


def _as_bool(raw: Any) -> bool:
    if isinstance(raw, str):
        return raw.strip().lower() in ("true", "1", "yes")
    return bool(raw)


def _as_dict(raw: Any) -> dict[str, Any]:
    if isinstance(raw, str):
        if not raw.strip():
            return {}
        try:
            raw = json.loads(raw)
        except json.JSONDecodeError as e:
            raise ValueError(f"Invalid inputs JSON: {e}")
    return dict(raw) if isinstance(raw, dict) else {}


def normalize_params(params: dict[str, Any] | None) -> dict[str, Any]:
    """The one place that decides what an ExecuteWorkflow node's parameters mean (section 2.2).

    A node with no ``version`` is *legacy*: ``version: "draft"``, ``inputMode`` as stored (default ``passThrough``) and
    ``outputMode: "merged"``, so published versions keep their exact behaviour. A new node defaults to
    ``version: "latest"``, ``inputMode: "map"``, ``outputMode: "items"``. Returns a plain dict with ``legacy`` set.
    """
    p = params or {}
    raw_version = p.get("version")
    legacy = raw_version is None or raw_version == ""
    if legacy:
        version: str | int = "draft"
        default_input_mode, default_output_mode = "passThrough", "merged"
    else:
        version = _parse_version(raw_version)
        default_input_mode, default_output_mode = "map", "items"

    input_mode = p.get("inputMode") or default_input_mode
    if input_mode not in _INPUT_MODES:
        input_mode = default_input_mode
    mode = p.get("mode") or "once"
    if mode not in ("once", "each"):
        mode = "once"
    output_mode = p.get("outputMode") or default_output_mode
    if output_mode not in ("items", "merged"):
        output_mode = default_output_mode

    try:
        timeout = float(p.get("timeoutSeconds")) if p.get("timeoutSeconds") not in (None, "") else DEFAULT_TIMEOUT_SECONDS
    except (TypeError, ValueError):
        timeout = DEFAULT_TIMEOUT_SECONDS
    if not math.isfinite(timeout) or timeout <= 0:  # NaN would make asyncio.wait_for never fire; inf/negative are not timeouts
        timeout = DEFAULT_TIMEOUT_SECONDS
    timeout = min(timeout, MAX_TIMEOUT_SECONDS)

    return {
        "legacy": legacy,
        "workflowId": p.get("workflowId"),
        "version": version,
        "inputMode": input_mode,
        "inputs": p.get("inputs"),
        "includeItem": _as_bool(p.get("includeItem", False)),
        "customInput": p.get("customInput", "{}"),
        "mode": mode,
        "timeoutSeconds": timeout,
        "outputMode": output_mode,
    }


class ExecuteWorkflowNode(BaseNode):
    """Execute Workflow node - executes another workflow as a subworkflow."""

    node_description = NodeTypeDescription(
        name="ExecuteWorkflow",
        display_name="Execute Workflow",
        description="Execute another workflow as a subworkflow",
        icon="fa:sitemap",
        group=["flow"],
        inputs=[NodeInputDefinition(name="main", display_name="Input")],
        outputs=[
            NodeOutputDefinition(
                name="main",
                display_name="Output",
                schema={
                    "type": "object",
                    "description": "Output from the executed subworkflow",
                },
            )
        ],
        properties=[
            NodeProperty(
                display_name="Workflow",
                name="workflowId",
                type="workflowSelector",
                required=True,
                description="The workflow to execute as a subworkflow",
            ),
            NodeProperty(
                display_name="Version",
                name="version",
                type="options",
                default="latest",
                description="Which version of the sub-workflow runs: its draft, its latest published version, or a version number",
                options=[
                    NodePropertyOption(name="Latest published", value="latest"),
                    NodePropertyOption(name="Draft", value="draft"),
                ],
            ),
            NodeProperty(
                display_name="Input Mode",
                name="inputMode",
                type="options",
                default="map",
                options=[
                    NodePropertyOption(
                        name="Map Inputs",
                        value="map",
                        description="One row per input of the sub-workflow",
                    ),
                    NodePropertyOption(
                        name="Pass Through Input",
                        value="passThrough",
                        description="Pass the input data directly to the subworkflow",
                    ),
                    NodePropertyOption(
                        name="Custom Input",
                        value="custom",
                        description="Provide custom input data for the subworkflow",
                    ),
                ],
            ),
            NodeProperty(
                display_name="Inputs",
                name="inputs",
                type="json",
                default="{}",
                description="Input field -> expression, for the sub-workflow's declared inputs",
                display_options={"show": {"inputMode": ["map"]}},
            ),
            NodeProperty(
                display_name="Include Incoming Item",
                name="includeItem",
                type="boolean",
                default=False,
                description="Start from the incoming item and overlay the mapped fields",
                display_options={"show": {"inputMode": ["map"]}},
            ),
            NodeProperty(
                display_name="Custom Input",
                name="customInput",
                type="json",
                default="{}",
                description="Custom JSON input to pass to the subworkflow",
                display_options={"show": {"inputMode": ["custom"]}},
            ),
            NodeProperty(
                display_name="Mode",
                name="mode",
                type="options",
                default="once",
                options=[
                    NodePropertyOption(name="Run once with all items", value="once"),
                    NodePropertyOption(name="Run once for each item", value="each"),
                ],
            ),
            NodeProperty(
                display_name="Timeout (seconds)",
                name="timeoutSeconds",
                type="number",
                default=300,
                description="How long one sub-workflow run may take (at most 3600)",
            ),
            NodeProperty(
                display_name="Output",
                name="outputMode",
                type="options",
                default="items",
                options=[
                    NodePropertyOption(name="Sub-workflow's items", value="items"),
                    NodePropertyOption(name="Merged single item (legacy)", value="merged"),
                ],
            ),
        ],
    )

    @property
    def type(self) -> str:
        return "ExecuteWorkflow"

    @property
    def description(self) -> str:
        return "Execute another workflow as a subworkflow"

    # ExecuteWorkflow: the output is the child's return-node shape, derived through ``lookups["workflow_io"]`` (section 2.4).
    # Without that lookup, or when the child can't be resolved, the output stays UNKNOWN (an approved instance contract
    # still replaces it, see engine/contracts.py). A legacy node (merged single item) keeps its UNKNOWN output.

    def contract(self, params, inputs, lookups=None):
        """Typed from the child: ``inputs`` = the child's input contract, ``main`` = its output, ``error`` = input + error details.

        ``lookups`` is ``{"workflow_io": fn(workflow_id, version) -> WorkflowIO | None}`` (supplied by the checker).
        """
        base = sh.make_contract(inputs, {"main": sh.unknown()})
        fn = lookups.get("workflow_io") if isinstance(lookups, dict) else None
        if not callable(fn):
            return base
        try:
            p = normalize_params(params)
        except ValueError:
            return base
        wid = p["workflowId"]
        if isinstance(wid, dict):  # an n8n-style resource locator
            wid = wid.get("value")
        if not isinstance(wid, str) or not wid.strip() or "{{" in wid or wid.lstrip().startswith("="):
            return base
        io = fn(wid.strip(), p["version"])
        if io is None:
            return base

        child_in = getattr(io, "input", None)
        child_out = getattr(io, "output", None)
        out = sh.unknown()
        if p["outputMode"] == "items" and isinstance(child_out, dict):
            out = sh.with_source(child_out, "derived", only_missing=True)
        error = sh.error_shape(sh.input_shape(inputs))
        if sh.is_closed_object(error):
            error["properties"]["code"] = sh.scalar("string")
            error["properties"]["child_execution_id"] = sh.scalar("string", nullable=True)
            error["properties"]["failed_node"] = sh.scalar("string", nullable=True)
            error["required"] = [*error.get("required", []), "code"]
        return {
            "inputs": {"main": sh.with_source(child_in, "derived", only_missing=True) if isinstance(child_in, dict) else sh.unknown()},
            "outputs": {"main": out},
            "error": error,
        }

    # ── input mapping ────────────────────────────────────────────────────────────────────────────────────────────

    @staticmethod
    def _item_context(context: ExecutionContext, input_data: list[NodeData], index: int):
        from ...engine.expression_engine import ExpressionEngine

        return ExpressionEngine.create_context(
            input_data, context.node_states, context.execution_id, index, variables=context.variables
        )

    def _custom_for(self, p: dict[str, Any], context: ExecutionContext, input_data: list[NodeData], index: int) -> dict[str, Any]:
        from ...engine.expression_engine import expression_engine

        raw = p["customInput"]
        if isinstance(raw, str):
            if "{{" in raw and not p["legacy"]:
                data = expression_engine.resolve_json_template(raw, self._item_context(context, input_data, index))
            else:
                try:
                    data = json.loads(raw)
                except json.JSONDecodeError as e:
                    raise ValueError(f"Invalid custom input JSON: {e}")
        else:
            data = raw
            if not p["legacy"]:
                data = expression_engine.resolve(copy.deepcopy(raw), self._item_context(context, input_data, index))
        return data if isinstance(data, dict) else {"data": data}

    def _mapped_item(
        self, p: dict[str, Any], context: ExecutionContext, input_data: list[NodeData], index: int
    ) -> NodeData:
        """The child input for parent item ``index`` under ``map`` / ``custom``."""
        from ...engine.expression_engine import expression_engine
        from ...engine.types import NodeData

        if p["inputMode"] == "custom":
            return NodeData(json=self._custom_for(p, context, input_data, index))
        ectx = self._item_context(context, input_data, index)
        base: dict[str, Any] = {}
        if p["includeItem"] and index < len(input_data):
            base = copy.deepcopy(input_data[index].json)
        for field_name, expr in _as_dict(p["inputs"]).items():
            base[field_name] = expression_engine.resolve(copy.deepcopy(expr), ectx)
        return NodeData(json=base)

    def _plan_calls(
        self, p: dict[str, Any], context: ExecutionContext, input_data: list[NodeData]
    ) -> list[tuple[int | None, list[NodeData]]]:
        """``[(parent item index or None, child input items)]``: one entry for ``once``, one per item for ``each``."""
        from ...engine.types import NodeData

        if p["legacy"]:
            # today's behaviour, byte for byte: custom is one item, anything else passes the input through
            if p["inputMode"] == "custom":
                return [(None, [NodeData(json=self._custom_for(p, context, input_data, 0))])]
            return [(None, list(input_data) if input_data else [NodeData(json={})])]

        mode_each = p["mode"] == "each"
        if p["inputMode"] == "passThrough":
            if mode_each:
                return [(i, [item]) for i, item in enumerate(input_data)]
            return [(None, list(input_data) if input_data else [NodeData(json={})])]
        if mode_each:
            return [(i, [self._mapped_item(p, context, input_data, i)]) for i in range(len(input_data))]
        if p["inputMode"] == "custom":
            return [(None, [self._mapped_item(p, context, input_data, 0)])]
        count = max(len(input_data), 1)
        return [(None, [self._mapped_item(p, context, input_data, i) for i in range(count)])]

    # ── results ──────────────────────────────────────────────────────────────────────────────────────────────────

    @staticmethod
    def _failure(result: SubworkflowResult, node_name: str) -> Any:
        """The ``SubworkflowError`` a failed or timed-out result stands for (section 3.6)."""
        from ...engine.subworkflow import GUARD_CODES, SUBWORKFLOW_FAILED, SUBWORKFLOW_TIMEOUT, SubworkflowError

        err = result.error
        if err is not None and err.error_code == "TEST_UNMOCKED_EFFECT":
            # an inline child's outside step had no mock (section 6.3): the test-setup error, named "<node>/<child node>"
            from ...engine.test_mode import UnmockedEffectError

            return UnmockedEffectError(err.node_name, str((err.details or {}).get("effect") or "an outside system"))
        base = {
            "child_execution_id": result.child_execution_id,
            "child_workflow_id": result.child_workflow_id,
            "child_version_number": result.child_version_number,
        }
        name = result.child_name or result.child_workflow_id
        code = (err.error_code if err else None) or SUBWORKFLOW_FAILED
        if result.status == "timeout":
            return SubworkflowError(
                SUBWORKFLOW_TIMEOUT, err.error if err else f"Sub-workflow '{name}' timed out.",
                {**base, "child_node": None, "child_error_code": SUBWORKFLOW_TIMEOUT, "child_error": err.error if err else None},
            )
        if result.child_execution_id is None and code in GUARD_CODES:
            # refused before the child started: the guard's own code and message
            return SubworkflowError(code, err.error, {**(err.details or {}), **base})
        child_node = err.node_name if err else None
        label = child_node
        ctx = result.child_context
        if ctx is not None and child_node:
            nd = next((n for n in ctx.workflow.nodes if n.name == child_node), None)
            label = (nd.label if nd is not None and nd.label else child_node)
        child_error = err.error if err else "unknown error"
        return SubworkflowError(
            SUBWORKFLOW_FAILED,
            f"Sub-workflow '{name}' failed at '{label}': {child_error}",
            {**base, "child_node": child_node, "child_error_code": err.error_code if err else None, "child_error": child_error},
        )

    @staticmethod
    def _merged_item(result: SubworkflowResult, workflow_id: str):
        """The legacy single merged item (G4), identical to the old node's output."""
        from ...engine.types import NodeData

        ctx = result.child_context
        name = result.child_name or workflow_id
        execution_id = result.child_execution_id or (ctx.execution_id if ctx is not None else None)
        meta = {"id": workflow_id, "name": name, "execution_id": execution_id}
        if ctx is None:
            merged: dict[str, Any] = {"_subworkflow": meta}
            if result.items:
                merged.update(result.items[-1].json)
            else:
                merged["_empty"] = True
            return NodeData(json=merged)
        if not ctx.node_states:
            return NodeData(json={"_subworkflow": meta, "_empty": True})
        combined: dict[str, Any] = {"_subworkflow": meta}
        wf = ctx.workflow
        source_nodes = {c.source_node for c in wf.connections}
        terminal = [n.name for n in wf.nodes if n.name not in source_nodes and n.type != "Start"]
        if terminal:
            for node_name in terminal:
                out = ctx.node_states.get(node_name)
                if out:
                    combined.update(out[-1].json)
        else:
            last = ctx.node_states[list(ctx.node_states.keys())[-1]]
            if last:
                combined.update(last[-1].json)
        return NodeData(json=combined)

    # ── execution ────────────────────────────────────────────────────────────────────────────────────────────────

    async def execute(
        self,
        context: ExecutionContext,
        node_definition: NodeDefinition,
        input_data: list[NodeData],
    ) -> NodeExecutionResult:
        from ...engine.subworkflow import (
            InMemoryInvoker,
            SubworkflowCall,
            SubworkflowError,
        )
        from ...engine.types import NodeData, NodeExecutionResult

        p = normalize_params(node_definition.parameters)
        workflow_id = p["workflowId"]
        if not workflow_id:
            raise ValueError("No workflow selected for execution")
        workflow_id = str(workflow_id)

        invoker = context.subworkflow_invoker or InMemoryInvoker()
        opts = context.test_options
        test_inline = opts is not None and node_definition.name in opts.inline_subworkflows
        plan = self._plan_calls(p, context, input_data)
        on_error = node_definition.on_error
        per_item = on_error == "error_output"
        attempts = (node_definition.retry_on_fail + 1) if per_item else 1  # otherwise the runner's retry loop retries

        # Successful calls survive a runner retry of this node, so `each` never repeats a child that already ran.
        state = context.node_internal_state.setdefault(f"{node_definition.name}:subworkflow", {})
        memo = state.get("_calls")
        if memo is None or memo.get("input") is not input_data:
            memo = {"input": input_data, "done": {}}
            state["_calls"] = memo
        done: dict[int, SubworkflowResult] = memo["done"]

        failures: list[tuple[int | None, Any]] = []
        for n, (parent_index, items) in enumerate(plan):
            if n in done:
                continue
            last_failure: Any = None
            result: SubworkflowResult | None = None
            for attempt in range(attempts):
                timeout = p["timeoutSeconds"]
                if context.deadline is not None:
                    timeout = min(timeout, max(context.deadline - time.monotonic(), 0.0))
                call = SubworkflowCall(
                    parent_context=context,
                    parent_node=node_definition.name,
                    workflow_id=workflow_id,
                    version=p["version"],
                    items=items,
                    timeout_seconds=timeout,
                    test_inline=test_inline,
                )
                result = await invoker.invoke(call)
                if result.status == "success":
                    last_failure = None
                    break
                last_failure = self._failure(result, node_definition.name)
                if getattr(last_failure, "code", None) == "TEST_UNMOCKED_EFFECT":
                    raise last_failure  # a test-setup error: no retry, no error port
                if attempt + 1 < attempts and context.test_options is None:
                    await asyncio.sleep(node_definition.retry_delay / 1000)
            if last_failure is None and result is not None:
                done[n] = result
                continue
            if not per_item:
                raise last_failure
            failures.append((parent_index, last_failure))
            done[n] = result  # type: ignore[assignment]  (a failed result, routed to the error port below)

        # ── assemble the output ──
        main: list[NodeData] = []
        errors: list[NodeData] = []
        child_ids: list[str] = []
        for n, (parent_index, _items) in enumerate(plan):
            result = done[n]
            if result.child_execution_id:
                child_ids.append(result.child_execution_id)
            if result.status != "success":
                failure = next(f for (pi, f) in failures if pi == parent_index) if per_item else None
                src = input_data[parent_index] if parent_index is not None and parent_index < len(input_data) else (
                    input_data[0] if input_data else NodeData(json={})
                )
                errors.append(
                    NodeData(
                        json={
                            **copy.deepcopy(src.json),
                            "error": {"message": str(failure), "node": node_definition.name},
                            "code": failure.code,
                            "child_execution_id": result.child_execution_id,
                            "failed_node": failure.details.get("child_node"),
                        },
                        meta=self._meta(result, parent_index),
                    )
                )
                continue
            if p["outputMode"] == "merged":
                main.append(self._merged_item(result, workflow_id))
                continue
            same_count = parent_index is None and len(result.items) == len(input_data)
            for k, item in enumerate(result.items):
                idx = parent_index if parent_index is not None else (k if same_count else None)
                main.append(NodeData(json=copy.deepcopy(item.json), binary=item.binary, meta=self._meta(result, idx)))

        state.pop("_calls", None)
        metadata = {"childExecutionIds": child_ids, "subworkflowCalls": len(plan)}
        if per_item:
            return NodeExecutionResult(outputs={"main": main or None, "error": errors or None}, metadata=metadata)
        return NodeExecutionResult(outputs={"main": main or None}, metadata=metadata)

    @staticmethod
    def _meta(result: SubworkflowResult, paired_item: int | None) -> dict[str, Any]:
        meta: dict[str, Any] = {
            "child_execution_id": result.child_execution_id,
            "child_workflow_id": result.child_workflow_id,
            "child_version_number": result.child_version_number,
        }
        if paired_item is not None:
            meta["pairedItem"] = paired_item
        return meta
