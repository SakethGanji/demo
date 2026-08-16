"""Minimal base for config provider classes (memory/tools).

These live under nodes/ai/ and only need `get_parameter` + `node_description`.
"""

from __future__ import annotations

from typing import Any, TYPE_CHECKING

if TYPE_CHECKING:
    from ...engine.types import NodeDefinition


class ConfigProvider:
    """Base class providing get_parameter for config provider classes."""

    node_description = None

    def get_parameter(
        self,
        node_definition: NodeDefinition,
        key: str,
        default: Any = None,
    ) -> Any:
        """Get a parameter value from node definition, with type coercion."""
        value = node_definition.parameters.get(key)
        if value is None:
            if default is None and self._is_required_parameter(key):
                raise ValueError(
                    f'Missing required parameter "{key}" in node "{node_definition.name}"'
                )
            return default
        return self._coerce_parameter(key, value)

    def _is_required_parameter(self, key: str) -> bool:
        """Check if a parameter is required."""
        if not self.node_description:
            return False
        for prop in self.node_description.properties:
            if prop.name == key:
                return prop.required
        return False

    def _coerce_parameter(self, key: str, value: Any) -> Any:
        """Coerce a parameter value to its expected type based on the schema."""
        if self.node_description is None:
            return value
        prop = next(
            (p for p in self.node_description.properties if p.name == key), None
        )
        if prop is None:
            return value
        if prop.type == "number" and isinstance(value, str):
            try:
                return float(value) if "." in value else int(value)
            except (ValueError, TypeError):
                return value
        if prop.type == "boolean" and isinstance(value, str):
            return value.lower() in ("true", "1", "yes")
        return value
