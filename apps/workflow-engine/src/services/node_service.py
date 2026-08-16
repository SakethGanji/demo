"""Node service for business logic."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from ..core.exceptions import NodeNotFoundError

if TYPE_CHECKING:
    from ..engine.node_registry import NodeRegistry


class NodeService:
    """Service for node operations."""

    def __init__(self, node_registry: NodeRegistry) -> None:
        self._node_registry = node_registry

    def compute_node_io(
        self, node_type: str, parameters: dict[str, Any]
    ) -> dict[str, Any]:
        """
        Compute the actual inputs/outputs for a node based on its type and parameters.

        This handles dynamic I/O strategies (e.g., Switch node with numberOfOutputs).

        Args:
            node_type: The node type (e.g., "Switch")
            parameters: The node's saved parameters

        Returns:
            Dict with inputs, inputCount, outputs, outputCount, and strategies
        """
        info = self._node_registry.get_node_type_info(node_type)
        if not info:
            # Fallback for unknown nodes
            return {
                "inputs": [{"name": "main", "displayName": "Input"}],
                "inputCount": 1,
                "outputs": [{"name": "main", "displayName": "Output"}],
                "outputCount": 1,
                "inputStrategy": None,
                "outputStrategy": None,
                "group": None,
            }

        # Start with base schema
        base_inputs = info.inputs or [{"name": "main", "displayName": "Input"}]
        base_outputs = info.outputs or [{"name": "main", "displayName": "Output"}]

        # Compute dynamic outputs based on strategy
        computed_outputs = base_outputs
        output_count = len(base_outputs)

        if info.output_strategy:
            strategy_type = info.output_strategy.get("type")
            if strategy_type == "dynamicFromParameter":
                param_name = info.output_strategy.get("parameter")
                add_fallback = info.output_strategy.get("addFallback", False)

                if param_name and param_name in parameters:
                    num_outputs = int(parameters[param_name])
                else:
                    # Use default from properties
                    num_outputs = self._get_property_default(info.properties, param_name) or 1

                # Generate output0, output1, ..., outputN-1
                computed_outputs = [
                    {"name": f"output{i}", "displayName": f"Output {i}"}
                    for i in range(num_outputs)
                ]

                # Add fallback if strategy requires it
                if add_fallback:
                    computed_outputs.append({"name": "fallback", "displayName": "Fallback"})

                output_count = len(computed_outputs)

        # Compute dynamic inputs based on strategy (if applicable)
        computed_inputs = base_inputs
        input_count = len(base_inputs)

        if info.input_strategy:
            strategy_type = info.input_strategy.get("type")
            if strategy_type == "dynamicFromParameter":
                param_name = info.input_strategy.get("parameter")

                if param_name and param_name in parameters:
                    num_inputs = int(parameters[param_name])
                else:
                    num_inputs = self._get_property_default(info.properties, param_name) or 1

                computed_inputs = [
                    {"name": f"input{i}", "displayName": f"Input {i}"}
                    for i in range(num_inputs)
                ]
                input_count = len(computed_inputs)

        return {
            "inputs": computed_inputs,
            "inputCount": input_count,
            "outputs": computed_outputs,
            "outputCount": output_count,
            "inputStrategy": info.input_strategy,
            "outputStrategy": info.output_strategy,
            "group": info.group,
        }

    def _get_property_default(
        self, properties: list[dict[str, Any]], prop_name: str | None
    ) -> Any:
        """Get the default value for a property by name."""
        if not prop_name:
            return None
        for prop in properties:
            if prop.get("name") == prop_name:
                return prop.get("default")
        return None

    def _build_node_response(self, node_type: str) -> dict[str, Any] | None:
        """
        Build a node response with computed default I/O.

        This ensures /api/nodes returns outputs computed from property defaults,
        not all possible outputs. Frontend can use these directly without
        needing to recalculate.
        """
        info = self._node_registry.get_node_type_info(node_type)
        if not info:
            return None

        # Compute I/O using defaults (empty parameters = use property defaults)
        io_data = self.compute_node_io(node_type, {})

        return {
            "type": info.type,
            "displayName": info.display_name,
            "description": info.description,
            "icon": info.icon,
            "group": info.group,
            # Use computed I/O (based on property defaults)
            "inputCount": io_data["inputCount"],
            "outputCount": io_data["outputCount"],
            "inputs": io_data["inputs"],
            "outputs": io_data["outputs"],
            # Include strategies for frontend dynamic updates
            "inputStrategy": io_data["inputStrategy"],
            "outputStrategy": io_data["outputStrategy"],
            # Properties (frontend uses these for forms and dynamic recalculation)
            "properties": info.properties,
        }

    def list_nodes(self) -> list[dict[str, Any]]:
        """List all available node types with schemas and computed default I/O."""
        nodes = self._node_registry.get_node_info_full()
        return [
            node_response
            for n in nodes
            if (node_response := self._build_node_response(n.type)) is not None
        ]

    def get_node(self, node_type: str) -> dict[str, Any]:
        """Get schema for a specific node type with computed default I/O."""
        result = self._build_node_response(node_type)
        if not result:
            raise NodeNotFoundError(node_type)
        return result

    def get_nodes_by_group(self, group: str) -> list[dict[str, Any]]:
        """Get nodes filtered by group."""
        all_nodes = self.list_nodes()
        return [n for n in all_nodes if group in n.get("group", [])]
