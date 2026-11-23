"""FormTrigger - a trigger fed by a published form (Stage 5, DESIGN-STAGE5-UIS section 4.1).

The contract IS ``params.input_schema`` (a Shape): the public submit route validates every submission
against it before a run starts. The node emits one item with the validated fields at the top level
(like n8n's Form Trigger, so ``{{ $json.amount }}`` works) plus ``_submission {id, submitted_at, mode}``.
"""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING

from ...engine import shapes as sh
from ..base import BaseNode, NodeOutputDefinition, NodeProperty, NodeTypeDescription

if TYPE_CHECKING:
    from ...engine.types import ExecutionContext, NodeData, NodeDefinition, NodeExecutionResult


def _default_schema() -> dict:
    return {"type": "object", "properties": {}, "required": []}


class FormTriggerNode(BaseNode):
    """Form trigger - starts a run when someone submits the workflow's form."""

    node_description = NodeTypeDescription(
        name="FormTrigger",
        display_name="Form Trigger",
        description="Start the workflow when someone submits a form",
        icon="fa:wpforms",
        group=["trigger", "ui"],
        inputs=[],
        outputs=[NodeOutputDefinition(
            name="main", display_name="Submission",
            schema={"type": "object", "properties": {"_submission": {"type": "object", "description": "Submission info"}}},
        )],
        properties=[
            NodeProperty(display_name="Input Schema", name="input_schema", type="json", default=_default_schema(),
                         description="The contract of the form: a Shape (JSON Schema subset). Each property is a field."),
            NodeProperty(display_name="Title", name="title", type="string", default="", description="Form heading (default: the workflow name)"),
            NodeProperty(display_name="Description", name="description", type="string", default="", type_options={"rows": 3}),
            NodeProperty(display_name="Submit Label", name="submit_label", type="string", default="Submit"),
            NodeProperty(display_name="Field Order", name="field_order", type="json", default=[],
                         description="Field names in display order (default: schema order)"),
            NodeProperty(display_name="Fields UI", name="fields_ui", type="json", default={},
                         description="Per-field {label, help, placeholder, widget}"),
            NodeProperty(display_name="Success Message", name="success_message", type="string", default="Submitted",
                         description="Shown when the run has no result node"),
        ],
    )

    @property
    def type(self) -> str:
        return "FormTrigger"

    @property
    def description(self) -> str:
        return "Start the workflow when someone submits a form"

    def contract(self, params, inputs, lookups=None):
        """Declared: the item is ``input_schema`` plus an open ``_submission`` object."""
        declared = sh.param_shape(params, "input_schema", "inputSchema")
        base = declared if declared is not None and declared.get("type") == "object" else sh.object_of({}, [], "declared")
        props = dict(base.get("properties") or {})
        props["_submission"] = sh.scalar("object", "declared")
        required = [*(base.get("required") or []), "_submission"]
        return sh.make_contract(inputs, {"main": sh.object_of(props, required, "declared")})

    async def execute(
        self,
        context: ExecutionContext,
        node_definition: NodeDefinition,
        input_data: list[NodeData],
    ) -> NodeExecutionResult:
        from ...engine.types import NodeData

        now = datetime.now().isoformat()
        if input_data and input_data[0].json:
            data = dict(input_data[0].json)
            # A studio / API manual run wraps its payload webhook-style; the form fields are the body.
            if isinstance(data.get("body"), dict) and "method" in data and "headers" in data:
                data = dict(data["body"])
            data.setdefault("_submission", {"id": None, "submitted_at": now, "mode": "manual"})
            return self.output([NodeData(json=data)])
        return self.output([NodeData(json={"_submission": {"id": None, "submitted_at": now, "mode": "manual"}})])
