"""Cron node - scheduled execution trigger."""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING

from ..base import (
    BaseNode,
    NodeTypeDescription,
    NodeOutputDefinition,
    NodeProperty,
    NodePropertyOption,
)

if TYPE_CHECKING:
    from ...engine.types import ExecutionContext, NodeData, NodeDefinition, NodeExecutionResult


from ...engine import shapes as sh

class CronNode(BaseNode):
    """Cron trigger node - executes on a schedule."""

    node_description = NodeTypeDescription(
        name="Cron",
        display_name="Cron",
        description="Trigger workflow on a schedule",
        icon="fa:clock",
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
                        "mode": {"type": "string", "description": "Always 'cron'"},
                        "schedule": {"type": "string", "description": "Cron expression"},
                    },
                },
            )
        ],
        properties=[
            NodeProperty(
                display_name="Mode",
                name="mode",
                type="options",
                default="interval",
                options=[
                    NodePropertyOption(
                        name="Interval",
                        value="interval",
                        description="Run at fixed intervals",
                    ),
                    NodePropertyOption(
                        name="Cron Expression",
                        value="cron",
                        description="Use cron expression",
                    ),
                ],
            ),
            NodeProperty(
                display_name="Interval (seconds)",
                name="interval",
                type="number",
                default=60,
                description="Interval in seconds between executions",
                display_options={"show": {"mode": ["interval"]}},
            ),
            NodeProperty(
                display_name="Cron Expression",
                name="cronExpression",
                type="string",
                default="0 * * * *",
                placeholder="0 * * * *",
                description="Standard cron expression (minute hour day month weekday)",
                display_options={"show": {"mode": ["cron"]}},
            ),
        ],
    )

    @property
    def type(self) -> str:
        return "Cron"

    @property
    def description(self) -> str:
        return "Trigger workflow on a schedule"

    def contract(self, params, inputs, lookups=None):
        """Declared: what ``execute`` really emits (``timestamp`` in the design doc is ``triggeredAt`` here)."""
        shape = sh.object_of(
            {
                "triggeredAt": sh.scalar("string", "declared"),
                "mode": sh.scalar("string", "declared"),
                "schedule": sh.scalar("string", "declared"),
            },
            ["triggeredAt", "mode", "schedule"], "declared")
        return sh.make_contract(inputs, {"main": shape})

    async def execute(
        self,
        context: ExecutionContext,
        node_definition: NodeDefinition,
        input_data: list[NodeData],
    ) -> NodeExecutionResult:
        from ...engine.types import NodeData

        mode = self.get_parameter(node_definition, "mode", "interval")

        if mode == "interval":
            schedule = f"every {self.get_parameter(node_definition, 'interval', 60)} seconds"
        else:
            schedule = self.get_parameter(node_definition, "cronExpression", "0 * * * *")

        return self.output([
            NodeData(json={
                "triggeredAt": datetime.now().isoformat(),
                "mode": "cron",
                "schedule": schedule,
            })
        ])
