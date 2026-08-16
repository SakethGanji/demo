"""Calculator tool for AI agents."""

from __future__ import annotations

from typing import Any, TYPE_CHECKING

from ...base import (
    NodeProperty,
    NodeTypeDescription,
)
from .._config_base import ConfigProvider

if TYPE_CHECKING:
    from ....engine.types import NodeDefinition


class CalculatorToolNode(ConfigProvider):
    """Calculator tool - perform math calculations."""

    node_description = NodeTypeDescription(
        name="CalculatorTool",
        display_name="Calculator",
        description="Perform mathematical calculations",
        icon="fa:calculator",
        group=["ai"],
        inputs=[],
        outputs=[],
        properties=[
            NodeProperty(
                display_name="Description",
                name="description",
                type="string",
                default="Perform mathematical calculations. Input should be a valid math expression like '2 + 2' or '15 * 7 + 23'.",
                description="Description shown to the AI model",
                type_options={"rows": 2},
            ),
        ],
    )

    def get_config(self, node_definition: NodeDefinition) -> dict[str, Any]:
        """Return calculator tool configuration."""
        return {
            "name": "calculator",
            "description": self.get_parameter(
                node_definition,
                "description",
                "Perform mathematical calculations. Input should be a valid math expression."
            ),
            "input_schema": {
                "type": "object",
                "properties": {
                    "expression": {
                        "type": "string",
                        "description": "Math expression to evaluate (e.g., '2 + 2', '15 * 7')"
                    }
                },
                "required": ["expression"]
            },
            "execute": self._execute,
        }

    @staticmethod
    def _execute(input_data: dict[str, Any]) -> dict[str, Any]:
        """Execute the calculator tool."""
        expression = input_data.get("expression", "0")
        try:
            # Safe eval for math only
            allowed_names = {"abs": abs, "round": round, "min": min, "max": max, "pow": pow}
            result = eval(expression, {"__builtins__": {}}, allowed_names)
            return {"result": result, "expression": expression}
        except Exception as e:
            return {"error": str(e), "expression": expression}
