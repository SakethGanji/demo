"""Loop node - iterate over input items in batches."""

from __future__ import annotations

from typing import Any, TYPE_CHECKING

from ..base import (
    BaseNode,
    NodeTypeDescription,
    NodeInputDefinition,
    NodeOutputDefinition,
    NodeProperty,
)

if TYPE_CHECKING:
    from ...engine.types import ExecutionContext, NodeData, NodeDefinition, NodeExecutionResult


class LoopNode(BaseNode):
    """Loop node - iterate over input items, optionally in batches."""

    node_description = NodeTypeDescription(
        name="Loop",
        display_name="Loop",
        description="Iterate over input items, optionally in batches",
        icon="fa:sync",
        group=["flow"],
        inputs=[NodeInputDefinition(name="main", display_name="Input")],
        outputs=[
            NodeOutputDefinition(
                name="loop",
                display_name="Loop",
                schema={"type": "unknown", "description": "Current batch of items"},
            ),
            NodeOutputDefinition(
                name="done",
                display_name="Done",
                schema={"type": "unknown", "description": "All items after processing"},
            ),
        ],
        properties=[
            NodeProperty(
                display_name="Batch Size",
                name="batchSize",
                type="number",
                default=1,
                description="Number of items per iteration (1 = one item at a time)",
            ),
        ],
    )

    @property
    def type(self) -> str:
        return "Loop"

    @property
    def description(self) -> str:
        return "Iterate over input items, optionally in batches"

    async def execute(
        self,
        context: ExecutionContext,
        node_definition: NodeDefinition,
        input_data: list[NodeData],
    ) -> NodeExecutionResult:
        from ...engine.types import NodeData as ND

        batch_size = max(1, int(self.get_parameter(node_definition, "batchSize", 1)))

        state_key = node_definition.name
        state: dict[str, Any] = context.node_internal_state.get(state_key, {})

        if "items" not in state:
            state = {
                "items": [item.json for item in input_data],
                "currentIndex": 0,
            }

        items = state["items"]
        current_index = state["currentIndex"]

        batch_end = min(current_index + batch_size, len(items))
        batch = items[current_index:batch_end]

        if not batch:
            context.node_internal_state.pop(state_key, None)
            return self.outputs({
                "loop": None,
                "done": [ND(json=item) for item in items],
            })

        state["currentIndex"] = batch_end
        context.node_internal_state[state_key] = state

        has_more = batch_end < len(items)

        if has_more:
            return self.outputs({
                "loop": [ND(json=item) for item in batch],
                "done": None,
            })
        else:
            context.node_internal_state.pop(state_key, None)
            return self.outputs({
                "loop": None,
                "done": [ND(json=item) for item in batch],
            })
