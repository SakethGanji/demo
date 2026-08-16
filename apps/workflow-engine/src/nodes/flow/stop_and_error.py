"""StopAndError node - terminate workflow execution with custom error."""

from __future__ import annotations

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
    from ...engine.types import ExecutionContext, NodeData, NodeDefinition, NodeExecutionResult

from ...engine.types import WorkflowStopSignal
from ...engine.expression_engine import expression_engine, ExpressionEngine


class StopAndErrorNode(BaseNode):
    """Stop workflow execution with a custom error message."""

    node_description = NodeTypeDescription(
        name="StopAndError",
        display_name="Stop and Error",
        description="Stop workflow execution with a custom error message",
        icon="fa:stop-circle",
        group=["flow"],
        inputs=[NodeInputDefinition(name="main", display_name="Input")],
        outputs=[
            NodeOutputDefinition(
                name="main",
                display_name="Output",
                schema={"type": "unknown", "passthrough": True},
            ),
        ],
        properties=[
            NodeProperty(
                display_name="Error Type",
                name="errorType",
                type="options",
                default="error",
                options=[
                    NodePropertyOption(
                        name="Error",
                        value="error",
                        description="Stop workflow and mark as failed",
                    ),
                    NodePropertyOption(
                        name="Warning",
                        value="warning",
                        description="Log warning but continue to next node",
                    ),
                ],
                description="Whether to stop execution or just log a warning",
            ),
            NodeProperty(
                display_name="Error Message",
                name="message",
                type="string",
                default="Workflow stopped",
                placeholder="Enter error message or expression",
                description="Error message to display. Supports expressions like {{ $json.errorReason }}",
            ),
        ],
    )

    @property
    def type(self) -> str:
        return "StopAndError"

    @property
    def description(self) -> str:
        return "Stop workflow execution with a custom error message"

    async def execute(
        self,
        context: ExecutionContext,
        node_definition: NodeDefinition,
        input_data: list[NodeData],
    ) -> NodeExecutionResult:
        error_type = self.get_parameter(node_definition, "errorType", "error")
        message_template = self.get_parameter(node_definition, "message", "Workflow stopped")

        # Resolve message expression if needed
        if input_data:
            expr_context = ExpressionEngine.create_context(
                input_data,
                context.node_states,
                context.execution_id,
                item_index=0,
            )
            message = expression_engine.resolve(message_template, expr_context)
        else:
            message = message_template

        if error_type == "error":
            # Raise stop signal to halt workflow execution
            raise WorkflowStopSignal(message=str(message), error_type="error")
        else:
            # Warning mode: log and continue (pass through input data)
            # The warning will be captured in the node output
            results = []
            for item in input_data:
                new_json = dict(item.json)
                new_json["_warning"] = str(message)
                results.append(NodeData(json=new_json, binary=item.binary))

            return self.output(results if results else [NodeData(json={"_warning": str(message)})])
