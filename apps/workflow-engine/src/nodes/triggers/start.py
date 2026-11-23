"""Start node - manual trigger entry point."""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING

from ..base import (
    BaseNode,
    NodeTypeDescription,
    NodeOutputDefinition,
)

if TYPE_CHECKING:
    from ...engine.types import ExecutionContext, NodeData, NodeDefinition, NodeExecutionResult


from ...engine import shapes as sh

class StartNode(BaseNode):
    """Manual trigger node - entry point for workflow execution."""

    node_description = NodeTypeDescription(
        name="Start",
        display_name="Start",
        description="Manual trigger to start workflow execution",
        icon="fa:play",
        group=["trigger"],
        inputs=[],  # No inputs - this is a trigger
        outputs=[
            NodeOutputDefinition(
                name="main",
                display_name="Output",
                schema={
                    "type": "object",
                    "properties": {
                        "triggeredAt": {"type": "string", "description": "ISO timestamp"},
                        "mode": {"type": "string", "description": "Trigger mode"},
                    },
                },
            )
        ],
        properties=[],
    )

    @property
    def type(self) -> str:
        return "Start"

    @property
    def description(self) -> str:
        return "Manual trigger to start workflow execution"

    def contract(self, params, inputs, lookups=None):
        """Declared: ``params.input_schema`` (a Shape) is the item shape; otherwise the default below."""
        declared = sh.param_shape(params, "input_schema", "inputSchema")
        shape = declared if declared is not None else sh.object_of(
            {"triggeredAt": sh.scalar("string", "declared"), "mode": sh.scalar("string", "declared")},
            [], "declared")  # nothing is required: a run that is given input passes it through and adds neither field
        return sh.make_contract(inputs, {"main": shape})

    async def execute(
        self,
        context: ExecutionContext,
        node_definition: NodeDefinition,
        input_data: list[NodeData],
    ) -> NodeExecutionResult:
        from ...engine.types import NodeData

        # Pass through input data or create trigger data
        if input_data and input_data[0].json:
            return self.output(input_data)

        return self.output([
            NodeData(json={
                "triggeredAt": datetime.now().isoformat(),
                "mode": context.mode,
            })
        ])
