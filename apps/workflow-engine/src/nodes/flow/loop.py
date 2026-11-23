"""Loop node - iterate over input items in batches."""

from __future__ import annotations

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
    from ...engine.types import ExecutionContext, NodeData, NodeDefinition, NodeExecutionResult


from ...engine import shapes as sh

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
            NodeProperty(
                display_name="Run at a time",
                name="concurrency",
                type="number",
                default=1,
                description=(
                    "How many items run through the loop body at once (1 = one after another). The results keep "
                    "the items' order. Bodies that pause, wait, repeat or hold another loop run one at a time."
                ),
            ),
            NodeProperty(
                display_name="If one item fails",
                name="onItemFailure",
                type="options",
                default="stop",
                options=[
                    NodePropertyOption(name="Stop the run", value="stop"),
                    NodePropertyOption(name="Skip it and keep going", value="skip"),
                ],
                description="What a failed step inside the loop body does: stop the whole run, or skip that item",
            ),
            NodeProperty(
                display_name="Stop after failures in a row",
                name="stopAfterFailures",
                type="number",
                default=0,
                description="With skip: stop the loop when this many items in a row fail (0 = never)",
            ),
        ],
    )

    @property
    def type(self) -> str:
        return "Loop"

    @property
    def description(self) -> str:
        return "Iterate over input items, optionally in batches"

    def contract(self, params, inputs, lookups=None):
        """Derived. ``loop``: the batch items (input shape; with ``maxIterations > 0`` the first input item
        plus ``i: integer``). ``done``: one item ``{_loop_completed, total_items, items: [<loop item>]}``."""
        item = sh.input_shape(inputs)
        raw = (params or {}).get("maxIterations")
        iterating = isinstance(raw, str) and "{{" in raw  # an expression: can't tell, assume it may iterate
        try:
            iterating = iterating or int(raw or 0) > 0
        except (TypeError, ValueError):
            pass
        if iterating:
            if sh.is_closed_object(item):
                item = {**item, "properties": {**item["properties"], "i": sh.scalar("integer")}}
            else:
                item = sh.unknown()
        done = sh.object_of(
            {"_loop_completed": sh.scalar("boolean"), "total_items": sh.scalar("integer"), "items": sh.array_of(item)},
            ["_loop_completed", "total_items", "items"])
        return sh.make_contract(inputs, {"loop": item, "done": done})

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

        from ...engine import loop_history as lh

        if "items" not in state:
            if max_iterations > 0:
                base = input_data[0].json if input_data else {}
                items = [{**base, "i": i} for i in range(max_iterations)]
            else:
                items = [item.json for item in input_data]
            state = {"items": items, "currentIndex": 0, "policy": loop_policy(self, node_definition, batch_size)}
            lh.start_loop(context, state_key, len(items), state["policy"])

        items = state["items"]
        current_index = state["currentIndex"]
        # the body came round again: the pass that was running is over (history only)
        prev_pass = lh.active_pass(context, state_key) if current_index > 0 else None
        if prev_pass is not None:
            lh.close_pass(context, state_key, prev_pass)

        batch_end = min(current_index + batch_size, len(items))
        batch = items[current_index:batch_end]

        if not batch:
            # All items consumed (this is the call AFTER the last batch
            # cycled through the loop body). Emit ONE "loop completed"
            # event so downstream finalizers fire exactly once. The full
            # items list rides along as metadata for callers that want it,
            # but is not exploded into N downstream invocations.
            context.node_internal_state.pop(state_key, None)
            lh.finish_loop(context, state_key)
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
        lh.open_pass(context, state_key, lh.active_pass(context, state_key), batch)
        return self.outputs({
            "loop": [ND(json=item) for item in batch],
            "done": None,
        })


# The most items a Loop runs through its body at once. A higher "Run at a time" is clamped to this (and the clamp is
# noted in the loop history); the workflow check warns about it.
MAX_LOOP_CONCURRENCY = 20


def _int(value: Any, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def loop_policy(node: BaseNode, node_definition: NodeDefinition, batch_size: int) -> dict[str, Any]:
    """The loop's settings as the runner reads them. Every one defaults to how a Loop always ran: one item after
    another, and a failed step stops the run."""
    asked = max(1, _int(node.get_parameter(node_definition, "concurrency", 1), 1))
    concurrency = min(asked, MAX_LOOP_CONCURRENCY)
    on_fail = str(node.get_parameter(node_definition, "onItemFailure", "stop") or "stop").strip().lower()
    stop_after = max(0, _int(node.get_parameter(node_definition, "stopAfterFailures", 0), 0))
    pol: dict[str, Any] = {
        "batchSize": batch_size,
        "concurrency": concurrency,
        "onItemFailure": "skip" if on_fail == "skip" else "stop",
        "stopAfterFailures": stop_after,
    }
    if asked > concurrency:  # only when clamped, so a valid setting records exactly what it always did
        pol["concurrencyClamped"] = (f"Run at a time was {asked}; it ran at most {MAX_LOOP_CONCURRENCY} "
                                     "items at a time (the limit)")
    return pol
