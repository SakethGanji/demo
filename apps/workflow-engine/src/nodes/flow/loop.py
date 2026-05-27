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
            NodeProperty(
                display_name="Max Iterations",
                name="maxIterations",
                type="number",
                default=0,
                description=(
                    "When > 0, iterate exactly N times instead of consuming "
                    "an input array. Each iteration ticket merges the first "
                    "input item with {i: <index>} so the body can read both "
                    "the upstream config and the iteration index. Accepts "
                    "expressions, e.g. \"{{ $json.max_iterations or 5 }}\". "
                    "Default 0 = legacy array-iteration behavior."
                ),
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

        from ...engine.expression_engine import ExpressionEngine, expression_engine

        batch_size = max(1, int(self.get_parameter(node_definition, "batchSize", 1)))

        # maxIterations: when > 0, synthesize N tickets from input[0] instead
        # of consuming an input array. Lets a single upstream payload (e.g. a
        # webhook config) drive an N-times loop without an upstream fanOut
        # Code node. Accepts expressions like "{{ $json.max_iterations or 5 }}";
        # the workflow runner skips $json during pre-resolution, so we resolve
        # here against input[0] (the first item is the natural reference for
        # how-many-times-to-iterate).
        raw_max = self.get_parameter(node_definition, "maxIterations", 0)
        if isinstance(raw_max, str) and "{{" in raw_max:
            expr_context = ExpressionEngine.create_context(
                input_data,
                context.node_states,
                context.execution_id,
                item_index=0,
            )
            raw_max = expression_engine.resolve(raw_max, expr_context)
        try:
            max_iterations = int(raw_max or 0)
        except (TypeError, ValueError):
            max_iterations = 0
        max_iterations = max(0, max_iterations)

        state_key = node_definition.name
        state: dict[str, Any] = context.node_internal_state.get(state_key, {})

        if "items" not in state:
            if max_iterations > 0:
                base = input_data[0].json if input_data else {}
                items = [{**base, "i": i} for i in range(max_iterations)]
            else:
                items = [item.json for item in input_data]
            state = {"items": items, "currentIndex": 0}

        items = state["items"]
        current_index = state["currentIndex"]

        batch_end = min(current_index + batch_size, len(items))
        batch = items[current_index:batch_end]

        if not batch:
            # All items consumed (this is the call AFTER the last batch
            # cycled through the loop body). Emit ONE "loop completed"
            # event so downstream finalizers fire exactly once. The full
            # items list rides along as metadata for callers that want it,
            # but is not exploded into N downstream invocations.
            context.node_internal_state.pop(state_key, None)
            return self.outputs({
                "loop": None,
                "done": [ND(json={
                    "_loop_completed": True,
                    "total_items": len(items),
                    "items": items,
                })],
            })

        # Non-empty batch — always emit on "loop", regardless of whether
        # this is the last batch. The body cycles back and the NEXT call
        # finds the empty batch and emits "done". This costs one extra
        # round-trip on completion but ensures every item enters the body.
        state["currentIndex"] = batch_end
        context.node_internal_state[state_key] = state
        return self.outputs({
            "loop": [ND(json=item) for item in batch],
            "done": None,
        })
