"""Wait node - time delay execution."""

from __future__ import annotations

import asyncio
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


class WaitNode(BaseNode):
    """Wait node - delay execution for a specified time."""

    node_description = NodeTypeDescription(
        name="Wait",
        display_name="Wait",
        description="Pause execution for a specified duration",
        icon="fa:hourglass-half",
        group=["flow"],
        inputs=[NodeInputDefinition(name="main", display_name="Input")],
        outputs=[
            NodeOutputDefinition(
                name="main",
                display_name="Output",
                schema={"type": "unknown", "passthrough": True},
            )
        ],
        properties=[
            NodeProperty(
                display_name="Wait Unit",
                name="unit",
                type="options",
                default="seconds",
                options=[
                    NodePropertyOption(name="Seconds", value="seconds"),
                    NodePropertyOption(name="Minutes", value="minutes"),
                    NodePropertyOption(name="Hours", value="hours"),
                ],
            ),
            NodeProperty(
                display_name="Duration",
                name="duration",
                type="number",
                default=1,
                description="How long to wait",
            ),
        ],
    )

    @property
    def type(self) -> str:
        return "Wait"

    @property
    def description(self) -> str:
        return "Pause execution for a specified duration"

    async def execute(
        self,
        context: ExecutionContext,
        node_definition: NodeDefinition,
        input_data: list[NodeData],
    ) -> NodeExecutionResult:
        unit = self.get_parameter(node_definition, "unit", "seconds")
        duration = self.get_parameter(node_definition, "duration", 1)

        # Convert to seconds
        if unit == "minutes":
            seconds = duration * 60
        elif unit == "hours":
            seconds = duration * 3600
        else:
            seconds = duration

        # Cap at 5 minutes for safety
        seconds = min(seconds, 300)

        await asyncio.sleep(seconds)

        return self.output(input_data)
