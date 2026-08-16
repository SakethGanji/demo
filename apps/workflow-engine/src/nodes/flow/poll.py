"""Poll node - repeat a branch on interval until condition is met."""

from __future__ import annotations

import asyncio
import os
import time
from typing import Any, TYPE_CHECKING

from ..base import (
    BaseNode,
    NodeTypeDescription,
    NodeInputDefinition,
    NodeOutputDefinition,
    NodeProperty,
    NodePropertyOption,
)
from ...engine.expression_engine import ExpressionEngine, ExpressionContext

if TYPE_CHECKING:
    from ...engine.types import ExecutionContext, NodeData, NodeDefinition, NodeExecutionResult


class PollNode(BaseNode):
    """Poll node - re-run a downstream branch on interval until condition is met or timeout."""

    node_description = NodeTypeDescription(
        name="Poll",
        display_name="Poll",
        description="Repeat a branch on interval until condition is met",
        icon="fa:hourglass-half",
        group=["flow"],
        inputs=[NodeInputDefinition(name="main", display_name="Input")],
        outputs=[
            NodeOutputDefinition(
                name="loop",
                display_name="Loop",
                schema={"type": "unknown", "description": "Re-check branch"},
            ),
            NodeOutputDefinition(
                name="done",
                display_name="Done",
                schema={"type": "unknown", "description": "Condition met"},
            ),
            NodeOutputDefinition(
                name="timeout",
                display_name="Timeout",
                schema={"type": "unknown", "description": "Timed out waiting"},
            ),
        ],
        properties=[
            NodeProperty(
                display_name="Condition",
                name="condition",
                type="string",
                default="",
                required=True,
                placeholder="{{ $json.status == 'complete' }}",
                description="Expression that evaluates to true when polling should stop",
            ),
            NodeProperty(
                display_name="Interval",
                name="interval",
                type="number",
                default=5,
                description="Seconds to wait between checks",
            ),
            NodeProperty(
                display_name="Timeout",
                name="timeout",
                type="number",
                default=120,
                description="Max seconds to poll before giving up (0 = no timeout)",
            ),
        ],
    )

    @property
    def type(self) -> str:
        return "Poll"

    @property
    def description(self) -> str:
        return "Repeat a branch on interval until condition is met"

    async def execute(
        self,
        context: ExecutionContext,
        node_definition: NodeDefinition,
        input_data: list[NodeData],
    ) -> NodeExecutionResult:
        from ...engine.types import NodeData as ND

        condition = self.get_parameter(node_definition, "condition", "")
        interval = max(1, int(self.get_parameter(node_definition, "interval", 5)))
        timeout = int(self.get_parameter(node_definition, "timeout", 120))

        state_key = node_definition.name
        state: dict[str, Any] = context.node_internal_state.get(state_key, {})

        if "started_at" not in state:
            state = {"started_at": time.time(), "attempts": 0}

        state["attempts"] += 1
        context.node_internal_state[state_key] = state

        # Check timeout
        elapsed = time.time() - state["started_at"]
        if timeout > 0 and elapsed >= timeout:
            context.node_internal_state.pop(state_key, None)
            return self.outputs({
                "loop": None,
                "done": None,
                "timeout": input_data,
            })

        # Evaluate condition
        condition_met = self._evaluate_condition(condition, input_data, context, node_definition)

        if condition_met:
            context.node_internal_state.pop(state_key, None)
            return self.outputs({
                "loop": None,
                "done": input_data,
                "timeout": None,
            })

        # Wait before next check
        await asyncio.sleep(interval)

        return self.outputs({
            "loop": input_data,
            "done": None,
            "timeout": None,
        })

    def _evaluate_condition(
        self,
        condition: str,
        input_data: list[NodeData],
        context: ExecutionContext,
        node_definition: NodeDefinition,
    ) -> bool:
        from ...engine.types import NodeData as ND

        if not condition or not condition.strip():
            return False

        try:
            expression_engine = ExpressionEngine()
            json_data = input_data[0].json if input_data else {}

            node_data_dict = {}
            for node_name, node_state in context.node_states.items():
                if node_state:
                    node_data_dict[node_name] = {"json": node_state[0].json if node_state else {}}

            expr_context = ExpressionContext(
                json_data=json_data,
                input_data=input_data,
                node_data=node_data_dict,
                env=dict(os.environ),
                execution={"id": context.execution_id, "mode": context.mode},
                item_index=0,
            )

            result = expression_engine.resolve(condition, expr_context)

            if isinstance(result, bool):
                return result
            elif isinstance(result, (int, float)):
                return bool(result)
            elif result == condition:
                return False
            else:
                return str(result).strip().lower() in ("true", "1", "yes")
        except Exception as e:
            print(f"[Poll] Warning: Could not evaluate condition '{condition}': {e}")
            return False
