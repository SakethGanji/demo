"""Base node class for all workflow nodes."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, TYPE_CHECKING

if TYPE_CHECKING:
    from ..engine.types import (
        ExecutionContext,
        NodeData,
        NodeDefinition,
        NodeExecutionResult,
        SubnodeContext,
        SubnodeSlotDefinition,
    )


@dataclass
class NodePropertyOption:
    """Option for a node property."""

    name: str
    value: str
    description: str | None = None


@dataclass
class NodeProperty:
    """Property definition for node schema."""

    display_name: str
    name: str
    type: str  # string, number, boolean, options, collection, json
    default: Any = None
    required: bool = False
    description: str | None = None
    placeholder: str | None = None
    options: list[NodePropertyOption] | None = None
    properties: list[NodeProperty] | None = None  # For collection type
    display_options: dict[str, Any] | None = None
    type_options: dict[str, Any] | None = None


@dataclass
class NodeInputDefinition:
    """Input definition for a node."""

    name: str
    display_name: str
    type: str = "main"


@dataclass
class NodeOutputDefinition:
    """Output definition for a node."""

    name: str
    display_name: str
    type: str = "main"
    schema: dict[str, Any] | None = None


@dataclass
class NodeTypeDescription:
    """Full description of a node type for UI generation."""

    name: str
    display_name: str
    description: str
    icon: str | None = None
    group: list[str] = field(default_factory=lambda: ["transform"])
    inputs: list[NodeInputDefinition] | str = field(
        default_factory=lambda: [NodeInputDefinition(name="main", display_name="Input")]
    )
    outputs: list[NodeOutputDefinition] | str = field(
        default_factory=lambda: [NodeOutputDefinition(name="main", display_name="Output")]
    )
    properties: list[NodeProperty] = field(default_factory=list)
    input_strategy: dict[str, Any] | None = None
    output_strategy: dict[str, Any] | None = None

    # Subnode support
    subnode_slots: list[SubnodeSlotDefinition] | None = None  # Slots for subnodes (parent nodes)
    is_subnode: bool = False  # True if this node is a subnode type
    subnode_type: str | None = None  # "model" | "memory" | "tool"
    provides_to_slot: str | None = None  # Which slot type this provides to


class BaseNode(ABC):
    """
    Abstract base class for all workflow nodes.

    Nodes should define a class-level `node_description` for schema-driven UI.
    """

    node_description: NodeTypeDescription | None = None

    @property
    @abstractmethod
    def type(self) -> str:
        """Node type identifier."""
        ...

    @property
    @abstractmethod
    def description(self) -> str:
        """Short description of what the node does."""
        ...

    @property
    def input_count(self) -> int | float:
        """Number of inputs this node expects. Use float('inf') for dynamic."""
        return 1

    @abstractmethod
    async def execute(
        self,
        context: ExecutionContext,
        node_definition: NodeDefinition,
        input_data: list[NodeData],
    ) -> NodeExecutionResult:
        """Execute the node logic."""
        ...

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
        prop = next((p for p in self.node_description.properties if p.name == key), None)
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

    def output(self, data: list[NodeData], metadata: dict[str, Any] | None = None) -> NodeExecutionResult:
        """Helper to create single-output result."""
        from ..engine.types import NodeExecutionResult

        return NodeExecutionResult(outputs={"main": data}, metadata=metadata or {})

    def outputs(self, outputs: dict[str, list[NodeData] | None], metadata: dict[str, Any] | None = None) -> NodeExecutionResult:
        """Helper to create multi-output result."""
        from ..engine.types import NodeExecutionResult

        return NodeExecutionResult(outputs=outputs, metadata=metadata or {})
