"""Merge node - combine data from multiple workflow branches."""

from __future__ import annotations

from typing import Any, TYPE_CHECKING

from ..base import (
    BaseNode,
    NodeTypeDescription,
    NodeOutputDefinition,
    NodeProperty,
    NodePropertyOption,
)

if TYPE_CHECKING:
    from ...engine.types import ExecutionContext, NodeData, NodeDefinition, NodeExecutionResult


class MergeNode(BaseNode):
    """Merge node - combine data from multiple workflow branches."""

    node_description = NodeTypeDescription(
        name="Merge",
        display_name="Merge",
        description="Combine data from multiple workflow branches",
        icon="fa:compress-arrows-alt",
        group=["flow"],
        inputs="dynamic",
        input_strategy={
            "type": "dynamicFromConnections",
            "minInputs": 2,
        },
        outputs=[
            NodeOutputDefinition(
                name="main",
                display_name="Output",
                schema={
                    "type": "unknown",
                    "description": "Combined data from all inputs",
                    "passthrough": True,
                },
            )
        ],
        properties=[
            NodeProperty(
                display_name="Mode",
                name="mode",
                type="options",
                default="append",
                options=[
                    NodePropertyOption(
                        name="Append",
                        value="append",
                        description="Concatenate all inputs",
                    ),
                    NodePropertyOption(
                        name="Wait For All",
                        value="waitForAll",
                        description="Wait for all inputs, output as arrays",
                    ),
                    NodePropertyOption(
                        name="Keep Matches",
                        value="keepMatches",
                        description="Only keep items matching on a field",
                    ),
                    NodePropertyOption(
                        name="Combine Pairs",
                        value="combinePairs",
                        description="Zip inputs pairwise",
                    ),
                ],
            ),
            NodeProperty(
                display_name="Match Field",
                name="matchField",
                type="string",
                default="id",
                placeholder="id",
                description="Field to match items on (for Keep Matches mode)",
                display_options={"show": {"mode": ["keepMatches"]}},
            ),
        ],
    )

    @property
    def type(self) -> str:
        return "Merge"

    @property
    def description(self) -> str:
        return "Combine data from multiple workflow branches"

    @property
    def input_count(self) -> float:
        """Dynamic input count - determined at runtime from connections."""
        return float("inf")

    async def execute(
        self,
        context: ExecutionContext,
        node_definition: NodeDefinition,
        _input_data: list[NodeData],
    ) -> NodeExecutionResult:
        from ...engine.types import NodeData, NO_OUTPUT_SIGNAL

        mode = self.get_parameter(node_definition, "mode", "append")
        match_field = self.get_parameter(node_definition, "matchField", "id")

        # Get all pending inputs for this node
        node_key = None
        for key in context.pending_inputs:
            if key.startswith(f"{node_definition.name}:"):
                node_key = key
                break

        if not node_key:
            return self.output([])

        pending_map = context.pending_inputs.get(node_key)
        if not pending_map:
            return self.output([])

        # Collect all inputs, filtering out NO_OUTPUT signals
        all_inputs: list[list[NodeData]] = []
        for data in pending_map.values():
            if data is not NO_OUTPUT_SIGNAL and isinstance(data, list) and data:
                all_inputs.append(data)

        if not all_inputs:
            return self.output([])

        result: list[NodeData]

        if mode == "append":
            # Simple concatenation
            result = [item for inputs in all_inputs for item in inputs]

        elif mode == "waitForAll":
            # Combine into single item with arrays
            result = [
                NodeData(json={
                    "inputs": [[item.json for item in inputs] for inputs in all_inputs]
                })
            ]

        elif mode == "keepMatches":
            # Only keep items that exist in all inputs (by matchField)
            if len(all_inputs) < 2:
                result = all_inputs[0] if all_inputs else []
            else:
                first_input = all_inputs[0]
                other_inputs = all_inputs[1:]

                result = [
                    item
                    for item in first_input
                    if all(
                        any(
                            self._get_nested_value(other_item.json, match_field)
                            == self._get_nested_value(item.json, match_field)
                            for other_item in other_input
                        )
                        for other_input in other_inputs
                    )
                ]

        elif mode == "combinePairs":
            # Combine items pairwise (zip)
            max_length = max(len(inputs) for inputs in all_inputs)
            result = []
            for i in range(max_length):
                combined: dict[str, Any] = {}
                for input_index, inputs in enumerate(all_inputs):
                    if i < len(inputs):
                        combined[f"input{input_index}"] = inputs[i].json
                result.append(NodeData(json=combined))

        else:
            result = [item for inputs in all_inputs for item in inputs]

        # Clear pending inputs
        del context.pending_inputs[node_key]

        return self.output(result)

    def _get_nested_value(self, obj: dict[str, Any], path: str) -> Any:
        """Get value at nested path."""
        current: Any = obj
        for key in path.split("."):
            if isinstance(current, dict):
                current = current.get(key)
            else:
                return None
        return current
