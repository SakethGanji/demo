"""Switch node - route items to different outputs based on conditions."""

from __future__ import annotations

import re
from typing import Any, TYPE_CHECKING

from ..base import (
    BaseNode,
    NodeTypeDescription,
    NodeInputDefinition,
    NodeOutputDefinition,
    NodeProperty,
    NodePropertyOption,
)
from ...engine.expression_engine import expression_engine, ExpressionEngine

if TYPE_CHECKING:
    from ...engine.types import ExecutionContext, NodeData, NodeDefinition, NodeExecutionResult


class SwitchNode(BaseNode):
    """Switch node - route items to different outputs based on conditions."""

    node_description = NodeTypeDescription(
        name="Switch",
        display_name="Switch",
        description="Route items to different outputs based on conditions",
        icon="fa:random",
        group=["flow"],
        inputs=[NodeInputDefinition(name="main", display_name="Input")],
        # Explicit outputs - frontend will show/hide based on numberOfOutputs
        outputs=[
            NodeOutputDefinition(name="output0", display_name="Output 0"),
            NodeOutputDefinition(name="output1", display_name="Output 1"),
            NodeOutputDefinition(name="output2", display_name="Output 2"),
            NodeOutputDefinition(name="output3", display_name="Output 3"),
            NodeOutputDefinition(name="output4", display_name="Output 4"),
            NodeOutputDefinition(name="output5", display_name="Output 5"),
            NodeOutputDefinition(name="output6", display_name="Output 6"),
            NodeOutputDefinition(name="output7", display_name="Output 7"),
            NodeOutputDefinition(name="output8", display_name="Output 8"),
            NodeOutputDefinition(name="output9", display_name="Output 9"),
            NodeOutputDefinition(name="output10", display_name="Output 10"),
            NodeOutputDefinition(name="output11", display_name="Output 11"),
            NodeOutputDefinition(name="output12", display_name="Output 12"),
            NodeOutputDefinition(name="output13", display_name="Output 13"),
            NodeOutputDefinition(name="output14", display_name="Output 14"),
            NodeOutputDefinition(name="fallback", display_name="Fallback"),
        ],
        output_strategy={
            "type": "dynamicFromParameter",
            "parameter": "numberOfOutputs",
            "addFallback": True,
        },
        properties=[
            NodeProperty(
                display_name="Number of Outputs",
                name="numberOfOutputs",
                type="number",
                default=2,
                type_options={"minValue": 1, "maxValue": 15},
                description="How many output branches to create (plus fallback)",
            ),
            NodeProperty(
                display_name="Mode",
                name="mode",
                type="options",
                default="rules",
                options=[
                    NodePropertyOption(
                        name="Rules",
                        value="rules",
                        description="Evaluate conditions against each item",
                    ),
                    NodePropertyOption(
                        name="Expression",
                        value="expression",
                        description="Use expression to determine output",
                    ),
                ],
            ),
            NodeProperty(
                display_name="Rules",
                name="rules",
                type="collection",
                default=[],
                type_options={"multipleValues": True},
                display_options={"show": {"mode": ["rules"]}},
                properties=[
                    NodeProperty(
                        display_name="Route to Output",
                        name="output",
                        type="number",
                        default=0,
                        type_options={"minValue": 0, "maxValue": 14},
                        description="Which output to route matching items to (0-14)",
                    ),
                    NodeProperty(
                        display_name="Field",
                        name="field",
                        type="string",
                        default="",
                        placeholder="status",
                        description="Field path (e.g., 'status') or expression (e.g., '{{ $json.status }}')",
                    ),
                    NodeProperty(
                        display_name="Operation",
                        name="operation",
                        type="options",
                        default="equals",
                        options=[
                            NodePropertyOption(name="Equals", value="equals"),
                            NodePropertyOption(name="Not Equals", value="notEquals"),
                            NodePropertyOption(name="Contains", value="contains"),
                            NodePropertyOption(name="Not Contains", value="notContains"),
                            NodePropertyOption(name="Starts With", value="startsWith"),
                            NodePropertyOption(name="Ends With", value="endsWith"),
                            NodePropertyOption(name="Greater Than", value="gt"),
                            NodePropertyOption(name="Greater or Equal", value="gte"),
                            NodePropertyOption(name="Less Than", value="lt"),
                            NodePropertyOption(name="Less or Equal", value="lte"),
                            NodePropertyOption(name="Is Empty", value="isEmpty"),
                            NodePropertyOption(name="Is Not Empty", value="isNotEmpty"),
                            NodePropertyOption(name="Regex Match", value="regex"),
                            NodePropertyOption(name="Is True", value="isTrue"),
                            NodePropertyOption(name="Is False", value="isFalse"),
                        ],
                    ),
                    NodeProperty(
                        display_name="Value",
                        name="value",
                        type="string",
                        default="",
                        description="Value to compare against. Supports expressions.",
                        display_options={"hide": {"operation": ["isEmpty", "isNotEmpty", "isTrue", "isFalse"]}},
                    ),
                ],
            ),
            NodeProperty(
                display_name="Output Index",
                name="outputIndex",
                type="number",
                default=0,
                type_options={"minValue": 0, "maxValue": 14},
                description="Output index to route all items to (0-based)",
                display_options={"show": {"mode": ["expression"]}},
            ),
        ],
    )

    @property
    def type(self) -> str:
        return "Switch"

    @property
    def description(self) -> str:
        return "Route items to different outputs based on conditions"

    async def execute(
        self,
        context: ExecutionContext,
        node_definition: NodeDefinition,
        input_data: list[NodeData],
    ) -> NodeExecutionResult:
        mode = self.get_parameter(node_definition, "mode", "rules")
        num_outputs = self.get_parameter(node_definition, "numberOfOutputs", 2)
        rules = self.get_parameter(node_definition, "rules", [])

        # Initialize all output buckets (output0, output1, etc. + fallback)
        outputs: dict[str, list[NodeData]] = {}
        for i in range(num_outputs):
            outputs[f"output{i}"] = []
        outputs["fallback"] = []

        if mode == "expression":
            # Expression mode: route all items to a single output index
            output_index = self.get_parameter(node_definition, "outputIndex", 0)
            # Clamp to valid range
            output_index = max(0, min(output_index, num_outputs - 1))
            key = f"output{output_index}"
            for item in input_data:
                outputs[key].append(item)
        else:
            # Rules mode: evaluate each rule against each item
            for idx, item in enumerate(input_data):
                matched = False
                # Create expression context for this item (for $json resolution)
                expr_context = ExpressionEngine.create_context(
                    input_data,
                    context.node_states,
                    context.execution_id,
                    item_index=idx,
                )
                for rule in rules:
                    if self._evaluate_rule(rule, item.json, expr_context):
                        output_idx = rule.get("output", 0)
                        # Clamp to valid range
                        output_idx = max(0, min(output_idx, num_outputs - 1))
                        key = f"output{output_idx}"
                        outputs[key].append(item)
                        matched = True
                        break

                if not matched:
                    outputs["fallback"].append(item)

        # Convert empty lists to None for NO_OUTPUT signal
        result: dict[str, list[NodeData] | None] = {}
        active_outputs: list[str] = []
        for key, data in outputs.items():
            if data:
                result[key] = data
                active_outputs.append(key)
            else:
                result[key] = None

        return self.outputs(
            result,
            metadata={
                "branchDecision": ", ".join(active_outputs) if active_outputs else "none",
                "activeOutputs": active_outputs,
            },
        )

    def _evaluate_rule(self, rule: dict[str, Any], json_data: dict[str, Any], expr_context: Any) -> bool:
        """Evaluate a single rule against JSON data."""
        field_raw = rule.get("field", "")
        rule_value_raw = rule.get("value")
        operation = rule.get("operation", "equals")

        # Resolve $json expressions in field (e.g., {{ $json.status }})
        if field_raw and "{{" in str(field_raw):
            field_value = expression_engine.resolve(field_raw, expr_context)
        else:
            # Simple field path lookup (e.g., "status" or "user.name")
            field_value = self._get_nested_value(json_data, field_raw)

        # Resolve $json expressions in value
        if rule_value_raw and "{{" in str(rule_value_raw):
            rule_value = expression_engine.resolve(rule_value_raw, expr_context)
        else:
            rule_value = rule_value_raw

        if operation == "equals":
            return field_value == rule_value
        elif operation == "notEquals":
            return field_value != rule_value
        elif operation == "contains":
            return str(rule_value) in str(field_value)
        elif operation == "notContains":
            return str(rule_value) not in str(field_value)
        elif operation == "startsWith":
            return str(field_value).startswith(str(rule_value))
        elif operation == "endsWith":
            return str(field_value).endswith(str(rule_value))
        elif operation == "gt":
            try:
                return float(field_value) > float(rule_value)
            except (ValueError, TypeError):
                return False
        elif operation == "gte":
            try:
                return float(field_value) >= float(rule_value)
            except (ValueError, TypeError):
                return False
        elif operation == "lt":
            try:
                return float(field_value) < float(rule_value)
            except (ValueError, TypeError):
                return False
        elif operation == "lte":
            try:
                return float(field_value) <= float(rule_value)
            except (ValueError, TypeError):
                return False
        elif operation == "isEmpty":
            return field_value is None or field_value == "" or field_value == []
        elif operation == "isNotEmpty":
            return field_value is not None and field_value != "" and field_value != []
        elif operation == "regex":
            try:
                return bool(re.search(str(rule_value), str(field_value)))
            except re.error:
                return False
        elif operation == "isTrue":
            return field_value is True or field_value == "true" or field_value == 1
        elif operation == "isFalse":
            return field_value is False or field_value == "false" or field_value == 0
        else:
            return False

    def _get_nested_value(self, obj: dict[str, Any], path: str) -> Any:
        """Get value at nested path."""
        if not path:
            return obj
        current: Any = obj
        for key in path.split("."):
            if isinstance(current, dict):
                current = current.get(key)
            else:
                return None
        return current
