"""No Operation node - hands its input items on unchanged (the "Return" step convert-selection adds to a sub-workflow)."""

from __future__ import annotations

from typing import TYPE_CHECKING

from ..base import BaseNode, NodeInputDefinition, NodeOutputDefinition, NodeTypeDescription

if TYPE_CHECKING:
    from ...engine.types import ExecutionContext, NodeData, NodeDefinition, NodeExecutionResult


from ...engine import shapes as sh


class NoOpNode(BaseNode):
    """Passes items through untouched."""

    node_description = NodeTypeDescription(
        name="NoOp",
        display_name="No Operation",
        description="Pass the items on unchanged",
        icon="fa:arrow-right",
        group=["flow"],
        inputs=[NodeInputDefinition(name="main", display_name="Input")],
        outputs=[NodeOutputDefinition(name="main", display_name="Output")],
        properties=[],
    )

    @property
    def type(self) -> str:
        return "NoOp"

    @property
    def description(self) -> str:
        return "Pass the items on unchanged"

    def contract(self, params, inputs, lookups=None):
        """Derived: passthrough."""
        return sh.passthrough_contract(inputs)

    async def execute(self, context: ExecutionContext, node_definition: NodeDefinition,
                      input_data: list[NodeData]) -> NodeExecutionResult:
        return self.output(list(input_data))
