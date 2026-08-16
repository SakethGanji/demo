"""ErrorTrigger node - entry point for error handling workflows."""

from __future__ import annotations

from typing import TYPE_CHECKING

from ..base import (
    BaseNode,
    NodeTypeDescription,
    NodeOutputDefinition,
)

if TYPE_CHECKING:
    from ...engine.types import (
        ExecutionContext,
        NodeData,
        NodeDefinition,
        NodeExecutionResult,
    )


class ErrorTriggerNode(BaseNode):
    """
    ErrorTrigger node - entry point for error handling workflows.
    This node is triggered when another workflow fails.
    """

    node_description = NodeTypeDescription(
        name="ErrorTrigger",
        display_name="Error Trigger",
        icon="fa:exclamation-triangle",
        description="Trigger a workflow when another workflow fails",
        group=["trigger"],
        inputs=[],  # Trigger node - no inputs
        outputs=[
            NodeOutputDefinition(
                name="main",
                display_name="Output",
                schema={
                    "type": "object",
                    "properties": {
                        "failedWorkflow": {
                            "type": "object",
                            "description": "Information about the failed workflow",
                            "properties": {
                                "id": {"type": "string", "description": "Workflow ID"},
                                "name": {"type": "string", "description": "Workflow name"},
                                "executionId": {"type": "string", "description": "Execution ID"},
                            },
                        },
                        "errors": {
                            "type": "array",
                            "description": "List of errors that occurred",
                            "items": {
                                "type": "object",
                                "properties": {
                                    "nodeName": {"type": "string", "description": "Name of the node that failed"},
                                    "message": {"type": "string", "description": "Error message"},
                                    "timestamp": {"type": "string", "description": "ISO timestamp of error"},
                                },
                            },
                        },
                        "timestamp": {"type": "string", "description": "ISO timestamp when error workflow triggered"},
                    },
                },
            )
        ],
        properties=[],  # Error data comes from the failing workflow
    )

    @property
    def type(self) -> str:
        return "ErrorTrigger"

    @property
    def description(self) -> str:
        return "Trigger a workflow when another workflow fails"

    @property
    def input_count(self) -> int:
        return 0  # Trigger node has no inputs

    async def execute(
        self,
        context: ExecutionContext,
        node_definition: NodeDefinition,
        input_data: list[NodeData],
    ) -> NodeExecutionResult:
        from ...engine.types import NodeData as ND

        # ErrorTrigger passes through the error data
        # The input data contains error information from the failed workflow
        return self.output(input_data if input_data else [ND(json={})])
