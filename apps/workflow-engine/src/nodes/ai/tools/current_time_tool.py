"""Current Time tool for AI agents."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, TYPE_CHECKING

from ...base import (
    NodeProperty,
    NodePropertyOption,
    NodeTypeDescription,
)
from .._config_base import ConfigProvider

if TYPE_CHECKING:
    from ....engine.types import NodeDefinition


class CurrentTimeToolNode(ConfigProvider):
    """Current Time tool - get the current date and time."""

    node_description = NodeTypeDescription(
        name="CurrentTimeTool",
        display_name="Current Time",
        description="Get current date and time",
        icon="fa:clock",
        group=["ai"],
        inputs=[],
        outputs=[],
        properties=[
            NodeProperty(
                display_name="Timezone",
                name="timezone",
                type="options",
                default="UTC",
                options=[
                    NodePropertyOption(name="UTC", value="UTC"),
                    NodePropertyOption(name="Local", value="local"),
                ],
                description="Timezone for the time",
            ),
        ],
    )

    def get_config(self, node_definition: NodeDefinition) -> dict[str, Any]:
        """Return current time tool configuration."""
        tz = self.get_parameter(node_definition, "timezone", "UTC")
        return {
            "name": "current_time",
            "description": f"Get the current date and time in {tz} timezone. No input required.",
            "input_schema": {
                "type": "object",
                "properties": {},
            },
            "execute": lambda _: self._execute(tz),
        }

    @staticmethod
    def _execute(tz: str) -> dict[str, Any]:
        """Execute the current time tool."""
        if tz == "UTC":
            now = datetime.now(timezone.utc)
        else:
            now = datetime.now()

        return {
            "datetime": now.isoformat(),
            "date": now.strftime("%Y-%m-%d"),
            "time": now.strftime("%H:%M:%S"),
            "day_of_week": now.strftime("%A"),
            "timezone": tz,
        }
