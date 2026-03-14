"""Random Number tool for AI agents."""

from __future__ import annotations

import random
from typing import Any, TYPE_CHECKING

from ...base import (
    NodeProperty,
    NodeTypeDescription,
)
from .._config_base import ConfigProvider

if TYPE_CHECKING:
    from ....engine.types import NodeDefinition


class RandomNumberToolNode(ConfigProvider):
    """Random Number tool - generate random numbers."""

    node_description = NodeTypeDescription(
        name="RandomNumberTool",
        display_name="Random Number",
        description="Generate random numbers",
        icon="fa:dice",
        group=["ai"],
        inputs=[],
        outputs=[],
        properties=[
            NodeProperty(
                display_name="Default Min",
                name="defaultMin",
                type="number",
                default=1,
                description="Default minimum value",
            ),
            NodeProperty(
                display_name="Default Max",
                name="defaultMax",
                type="number",
                default=100,
                description="Default maximum value",
            ),
        ],
    )

    def get_config(self, node_definition: NodeDefinition) -> dict[str, Any]:
        """Return random number tool configuration."""
        default_min = self.get_parameter(node_definition, "defaultMin", 1)
        default_max = self.get_parameter(node_definition, "defaultMax", 100)
        return {
            "name": "random_number",
            "description": f"Generate a random integer. Defaults to range {default_min}-{default_max} if not specified.",
            "input_schema": {
                "type": "object",
                "properties": {
                    "min": {
                        "type": "integer",
                        "description": f"Minimum value (default: {default_min})"
                    },
                    "max": {
                        "type": "integer",
                        "description": f"Maximum value (default: {default_max})"
                    }
                },
            },
            "execute": lambda input_data: self._execute(input_data, default_min, default_max),
        }

    @staticmethod
    def _execute(input_data: dict[str, Any], default_min: int, default_max: int) -> dict[str, Any]:
        """Execute the random number tool."""
        min_val = input_data.get("min", default_min)
        max_val = input_data.get("max", default_max)

        if min_val > max_val:
            min_val, max_val = max_val, min_val

        result = random.randint(int(min_val), int(max_val))
        return {
            "result": result,
            "min": min_val,
            "max": max_val,
        }
