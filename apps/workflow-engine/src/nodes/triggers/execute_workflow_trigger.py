"""ExecuteWorkflowTrigger node - entry point for workflows called as subworkflows."""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any
from typing import TYPE_CHECKING

from ..base import (
    BaseNode,
    NodeTypeDescription,
    NodeInputDefinition,
    NodeOutputDefinition,
    NodeProperty,
    NodePropertyOption,
)

if TYPE_CHECKING:
    from ...engine.types import ExecutionContext, NodeDefinition, NodeExecutionResult

from ...engine.types import NodeData


from ...engine import shapes as sh

FIELD_TYPES = ("string", "number", "boolean", "object", "array")


def input_source(params: dict[str, Any] | None) -> str | None:
    """``fields`` | ``jsonSchema`` | ``passthrough``, or None for a legacy node that never chose (schema = documentation).

    A node that has ``fields`` but no ``inputSource`` is a new node: ``fields``.
    """
    params = params or {}
    src = params.get("inputSource")
    if src in ("fields", "jsonSchema", "passthrough"):
        return src
    if params.get("fields") and not params.get("inputSchema"):
        return "fields"
    return None


def declared_input_shape(params: dict[str, Any] | None) -> dict[str, Any] | None:
    """The Shape the node's inputs declare, or None when nothing is declared (``passthrough``, no fields, no schema).

    ``inputSource`` is ``fields`` (new nodes), ``jsonSchema`` or ``passthrough``. A node with no ``inputSource`` is
    legacy: ``jsonSchema`` when an ``inputSchema`` is set, otherwise nothing is declared.
    """
    params = params or {}
    source = input_source(params)
    if source == "passthrough":
        return None
    if source == "fields":
        raw_fields = params.get("fields")
        if isinstance(raw_fields, str):
            try:
                raw_fields = json.loads(raw_fields)
            except (ValueError, TypeError):
                raw_fields = None
        props: dict[str, Any] = {}
        required: list[str] = []
        for f in raw_fields if isinstance(raw_fields, list) else []:
            if not isinstance(f, dict) or not str(f.get("name") or "").strip():
                continue
            name = str(f["name"]).strip()
            t = f.get("type") if f.get("type") in FIELD_TYPES else "string"
            shape = sh.scalar(t, "declared")
            if f.get("description"):
                shape["description"] = str(f["description"])
            props[name] = shape
            if f.get("required") in (True, "true", "True", 1):
                required.append(name)
        if not props:
            return None
        return sh.object_of(props, required, source="declared")
    return sh.param_shape(params, "input_schema", "inputSchema")


class ExecuteWorkflowTriggerNode(BaseNode):
    """Entry point trigger for workflows intended to be called as subworkflows."""

    node_description = NodeTypeDescription(
        name="ExecuteWorkflowTrigger",
        display_name="Execute Workflow Trigger",
        description="Entry point for workflows called by the Execute Workflow node",
        icon="fa:sign-in-alt",
        group=["trigger"],
        inputs=[],  # Trigger node has no inputs
        outputs=[
            NodeOutputDefinition(
                name="main",
                display_name="Input Data",
                schema={
                    "type": "object",
                    "description": "Data passed from the parent workflow",
                },
            )
        ],
        properties=[
            NodeProperty(
                display_name="Input Source",
                name="inputSource",
                type="options",
                default="fields",
                description="How the inputs are declared: fields below, a JSON schema, or accept any data",
                options=[
                    NodePropertyOption(name="Define using fields below", value="fields"),
                    NodePropertyOption(name="Define using JSON schema", value="jsonSchema"),
                    NodePropertyOption(name="Accept all data", value="passthrough"),
                ],
            ),
            NodeProperty(
                display_name="Fields",
                name="fields",
                type="collection",
                default=[],
                description="The inputs this workflow expects from the workflow that calls it",
                display_options={"show": {"inputSource": ["fields"]}},
                properties=[
                    NodeProperty(display_name="Name", name="name", type="string", required=True),
                    NodeProperty(
                        display_name="Type",
                        name="type",
                        type="options",
                        default="string",
                        options=[NodePropertyOption(name=t.capitalize(), value=t) for t in FIELD_TYPES],
                    ),
                    NodeProperty(display_name="Required", name="required", type="boolean", default=False),
                    NodeProperty(display_name="Description", name="description", type="string", default=""),
                ],
            ),
            NodeProperty(
                display_name="Input Schema",
                name="inputSchema",
                type="json",
                default="{}",
                description="Expected input structure (checked at run time when Input Source is JSON schema)",
                type_options={"language": "json", "rows": 6},
                display_options={"show": {"inputSource": ["jsonSchema"]}},
            ),
            NodeProperty(
                display_name="Default Input",
                name="defaultInput",
                type="json",
                default="{}",
                description="Default input data when triggered manually (for testing)",
                type_options={"language": "json", "rows": 6},
            ),
        ],
    )

    @property
    def type(self) -> str:
        return "ExecuteWorkflowTrigger"

    @property
    def description(self) -> str:
        return "Entry point for workflows called by the Execute Workflow node"

    def contract(self, params, inputs, lookups=None):
        """Declared: the ``fields`` / ``inputSchema`` the node declares (``inputSource``) plus the ``_``-prefixed fields execute() adds.

        Without a schema the parent decides the payload, so the output is UNKNOWN.
        """
        declared = declared_input_shape(params)
        if declared is None or not sh.is_closed_object(declared):
            return sh.make_contract(inputs, {"main": declared if declared is not None else sh.unknown()})
        s = sh.scalar
        extra = {
            "_triggeredAt": s("string", "declared"),
            "_triggerType": s("string", "declared"),
            "_executionDepth": s("integer", "declared"),
        }
        shape = {**declared, "properties": {**declared["properties"], **extra}}
        return sh.make_contract(inputs, {"main": shape})

    async def execute(
        self,
        context: ExecutionContext,
        node_definition: NodeDefinition,
        input_data: list[NodeData],
    ) -> NodeExecutionResult:
        import json as json_module

        default_input_str = self.get_parameter(node_definition, "defaultInput", "{}")
        now = (context.frozen_now or datetime.now()).isoformat()  # frozen in tests, so an inline child is deterministic

        # An explicit inputSource makes the declared inputs binding for calls from a parent (section 2.1). Legacy
        # nodes (no inputSource) keep treating inputSchema as documentation.
        explicit = input_source(node_definition.parameters) in ("fields", "jsonSchema")
        called = context.execution_depth > 0 or bool(input_data and input_data[0].json)
        if explicit and called and input_data:
            declared = declared_input_shape(node_definition.parameters)
            if declared is not None:
                self._validate_items(declared, input_data)

        # Check if we have input data from a parent workflow
        if input_data and (input_data[0].json or (explicit and called)):
            # Input was provided (from ExecuteWorkflow node or manual test)
            results = []
            for item in input_data:
                output = dict(item.json)
                output["_triggeredAt"] = now
                output["_triggerType"] = "subworkflow"
                output["_executionDepth"] = context.execution_depth
                results.append(NodeData(json=output))
            return self.output(results)
        else:
            # No input provided - use default input (for manual testing)
            try:
                if isinstance(default_input_str, str):
                    default_input = json_module.loads(default_input_str)
                else:
                    default_input = default_input_str
            except json_module.JSONDecodeError:
                default_input = {}

            output = dict(default_input) if isinstance(default_input, dict) else {"data": default_input}
            output["_triggeredAt"] = now
            output["_triggerType"] = "manual"
            output["_executionDepth"] = context.execution_depth

            return self.output([NodeData(json=output)])

    @staticmethod
    def _validate_items(declared: dict[str, Any], input_data: list[NodeData]) -> None:
        """Fail with ``SUBWORKFLOW_INPUT_INVALID`` naming the path: a missing required field or a wrong type."""
        from ...engine.subworkflow import SUBWORKFLOW_INPUT_INVALID, SubworkflowError

        for index, item in enumerate(input_data):
            violations = sh.validate(declared, item.json)
            if violations:
                v = violations[0]
                where = v["path"] or "input"
                prefix = f"item {index}: " if len(input_data) > 1 else ""
                raise SubworkflowError(
                    SUBWORKFLOW_INPUT_INVALID,
                    f"Sub-workflow input is invalid at '{where}': {prefix}{v['message']}.",
                    {"path": v["path"], "item_index": index, "violations": violations[:10]},
                )
