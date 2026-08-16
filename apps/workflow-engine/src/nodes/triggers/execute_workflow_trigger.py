"""ExecuteWorkflowTrigger node - entry point for workflows called as subworkflows."""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING

from ..base import (
    BaseNode,
    NodeTypeDescription,
    NodeInputDefinition,
    NodeOutputDefinition,
    NodeProperty,
)

if TYPE_CHECKING:
    from ...engine.types import ExecutionContext, NodeDefinition, NodeExecutionResult

from ...engine.types import NodeData


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
                display_name="Input Schema",
                name="inputSchema",
                type="json",
                default="{}",
                description="Optional: Define expected input structure (for documentation)",
                type_options={"language": "json", "rows": 6},
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

    async def execute(
        self,
        context: ExecutionContext,
        node_definition: NodeDefinition,
        input_data: list[NodeData],
    ) -> NodeExecutionResult:
        import json as json_module

        default_input_str = self.get_parameter(node_definition, "defaultInput", "{}")

        # Check if we have input data from a parent workflow
        if input_data and input_data[0].json:
            # Input was provided (from ExecuteWorkflow node or manual test)
            results = []
            for item in input_data:
                output = dict(item.json)
                output["_triggeredAt"] = datetime.now().isoformat()
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
            output["_triggeredAt"] = datetime.now().isoformat()
            output["_triggerType"] = "manual"
            output["_executionDepth"] = context.execution_depth

            return self.output([NodeData(json=output)])
