"""Text manipulation tool for AI agents."""

from __future__ import annotations

from typing import Any, TYPE_CHECKING

from ...base import (
    NodeTypeDescription,
)
from .._config_base import ConfigProvider

if TYPE_CHECKING:
    from ....engine.types import NodeDefinition


class TextToolNode(ConfigProvider):
    """Text tool - perform text operations."""

    node_description = NodeTypeDescription(
        name="TextTool",
        display_name="Text Utils",
        description="Text manipulation utilities",
        icon="fa:font",
        group=["ai"],
        inputs=[],
        outputs=[],
        properties=[],
    )

    def get_config(self, node_definition: NodeDefinition) -> dict[str, Any]:
        """Return text tool configuration."""
        return {
            "name": "text_utils",
            "description": "Perform text operations: count words, count characters, reverse text, uppercase, lowercase.",
            "input_schema": {
                "type": "object",
                "properties": {
                    "text": {
                        "type": "string",
                        "description": "The text to process"
                    },
                    "operation": {
                        "type": "string",
                        "description": "Operation: 'word_count', 'char_count', 'reverse', 'uppercase', 'lowercase'",
                        "enum": ["word_count", "char_count", "reverse", "uppercase", "lowercase"]
                    }
                },
                "required": ["text", "operation"]
            },
            "execute": self._execute,
        }

    @staticmethod
    def _execute(input_data: dict[str, Any]) -> dict[str, Any]:
        """Execute the text tool."""
        text = input_data.get("text", "")
        operation = input_data.get("operation", "word_count")

        if operation == "word_count":
            result = len(text.split())
            return {"word_count": result, "text": text}
        elif operation == "char_count":
            result = len(text)
            return {"char_count": result, "text": text}
        elif operation == "reverse":
            result = text[::-1]
            return {"reversed": result, "original": text}
        elif operation == "uppercase":
            result = text.upper()
            return {"uppercase": result, "original": text}
        elif operation == "lowercase":
            result = text.lower()
            return {"lowercase": result, "original": text}
        else:
            return {"error": f"Unknown operation: {operation}"}
