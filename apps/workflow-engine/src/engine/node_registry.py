"""Node registry for managing workflow node types."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, TYPE_CHECKING

if TYPE_CHECKING:
    from ..nodes.base import BaseNode


@dataclass
class NodeTypeInfo:
    """Full node type information for API responses."""

    type: str
    display_name: str
    description: str
    icon: str | None = None
    group: list[str] | None = None
    input_count: int | str = 1
    output_count: int | str = 1
    properties: list[dict[str, Any]] = field(default_factory=list)
    inputs: list[dict[str, Any]] | None = None
    outputs: list[dict[str, Any]] | None = None
    input_strategy: dict[str, Any] | None = None
    output_strategy: dict[str, Any] | None = None


class NodeRegistryClass:
    """Registry for workflow node types."""

    def __init__(self) -> None:
        self._nodes: dict[str, type[BaseNode]] = {}
        self._instances: dict[str, BaseNode] = {}

    def get(self, node_type: str) -> BaseNode:
        """
        Get a cached node instance by type.

        Node instances are stateless, so we return the cached instance
        for better performance.

        Raises:
            ValueError: If node type is not registered
        """
        if node_type not in self._instances:
            raise ValueError(f'Unknown node type: "{node_type}"')
        return self._instances[node_type]

    def has(self, node_type: str) -> bool:
        """Check if node type is registered."""
        return node_type in self._nodes

    def list(self) -> list[str]:
        """List all registered node types."""
        return list(self._nodes.keys())

    def get_node_info_full(self) -> list[NodeTypeInfo]:
        """
        Get full node info with schema for UI rendering.

        This is what the frontend uses to generate configuration forms.
        """
        return [self._build_node_type_info(instance) for instance in self._instances.values()]

    def _build_node_type_info(self, instance: BaseNode) -> NodeTypeInfo:
        """Build NodeTypeInfo from a node instance."""
        desc = instance.node_description

        # Determine inputs
        input_count: int | str = 1
        inputs: list[dict[str, Any]] = [
            {"name": "main", "displayName": "Input", "type": "main"}
        ]

        if desc:
            if desc.inputs == "dynamic":
                input_count = "dynamic"
                inputs = []
            elif isinstance(desc.inputs, list):
                inputs = [
                    {
                        "name": i.name,
                        "displayName": i.display_name,
                        "type": i.type,
                    }
                    for i in desc.inputs
                ]
                input_count = len(inputs)
        elif instance.input_count == float("inf"):
            input_count = "dynamic"
            inputs = []
        elif instance.input_count > 1:
            input_count = int(instance.input_count)

        # Determine outputs
        output_count: int | str = 1
        outputs: list[dict[str, Any]] = [
            {"name": "main", "displayName": "Output", "type": "main"}
        ]

        if desc:
            if desc.outputs == "dynamic":
                output_count = "dynamic"
                outputs = []
            elif isinstance(desc.outputs, list):
                outputs = [
                    {
                        "name": o.name,
                        "displayName": o.display_name,
                        "type": o.type,
                        "schema": o.schema,
                    }
                    for o in desc.outputs
                ]
                output_count = len(outputs)

        # Convert properties to dict format
        properties = self._convert_properties(desc.properties) if desc else []

        return NodeTypeInfo(
            type=instance.type,
            display_name=desc.display_name if desc else instance.type,
            description=instance.description,
            icon=desc.icon if desc else None,
            group=desc.group if desc else None,
            input_count=input_count,
            output_count=output_count,
            properties=properties,
            inputs=inputs if input_count != "dynamic" else None,
            outputs=outputs if output_count != "dynamic" else None,
            input_strategy=desc.input_strategy if desc else None,
            output_strategy=desc.output_strategy if desc else None,
        )

    def _convert_properties(self, properties: list) -> list[dict[str, Any]]:
        """Convert properties to dict format for API responses."""
        result = []
        for prop in properties:
            prop_dict: dict[str, Any] = {
                "displayName": prop.display_name,
                "name": prop.name,
                "type": prop.type,
                "default": prop.default,
            }
            if prop.required:
                prop_dict["required"] = True
            if prop.description:
                prop_dict["description"] = prop.description
            if prop.placeholder:
                prop_dict["placeholder"] = prop.placeholder
            if prop.options:
                prop_dict["options"] = [
                    {"name": o.name, "value": o.value, "description": o.description}
                    for o in prop.options
                ]
            if prop.properties:
                # Recursively convert nested properties
                prop_dict["properties"] = self._convert_properties(prop.properties)
            if prop.display_options:
                prop_dict["displayOptions"] = prop.display_options
            if prop.type_options:
                prop_dict["typeOptions"] = prop.type_options
            result.append(prop_dict)
        return result

    def get_node_type_info(self, node_type: str) -> NodeTypeInfo | None:
        """Get full info for a specific node type."""
        instance = self._instances.get(node_type)
        if not instance:
            return None
        return self._build_node_type_info(instance)

    def register(self, node_class: type[BaseNode]) -> None:
        """Register a node class if not already registered."""
        instance = node_class()
        if instance.type not in self._nodes:
            self._nodes[instance.type] = node_class
            self._instances[instance.type] = instance


# Singleton instance
node_registry = NodeRegistryClass()


def register_all_nodes() -> None:
    """Register all built-in nodes."""
    from ..nodes import (
        # Triggers
        StartNode,
        WebhookNode,
        CronNode,
        ErrorTriggerNode,
        ExecuteWorkflowTriggerNode,
        # Flow control
        IfNode,
        SwitchNode,
        MergeNode,
        WaitNode,
        LoopNode,
        PollNode,
        ExecuteWorkflowNode,
        StopAndErrorNode,
        # Data / Transform
        SetNode,
        HttpRequestNode,
        CodeNode,
        FilterNode,
        ItemListsNode,
        SampleNode,
        ProfileNode,
        AggregateNode,
        # Integrations
        SendEmailNode,
        PostgresNode,
        Neo4jNode,
        MongoDBNode,
        # AI
        LLMChatNode,
        AIAgentNode,
        # UI
        ChatInputNode,
    )

    all_node_classes: list[type[BaseNode]] = [
        # Triggers
        StartNode,
        WebhookNode,
        CronNode,
        ErrorTriggerNode,
        ExecuteWorkflowTriggerNode,
        # Flow control
        IfNode,
        SwitchNode,
        MergeNode,
        WaitNode,
        LoopNode,
        PollNode,
        ExecuteWorkflowNode,
        StopAndErrorNode,
        # Data / Transform
        SetNode,
        HttpRequestNode,
        CodeNode,
        FilterNode,
        ItemListsNode,
        SampleNode,
        ProfileNode,
        AggregateNode,
        # Integrations
        SendEmailNode,
        PostgresNode,
        Neo4jNode,
        MongoDBNode,
        # AI
        LLMChatNode,
        AIAgentNode,
        # UI
        ChatInputNode,
    ]

    for node_class in all_node_classes:
        node_registry.register(node_class)
