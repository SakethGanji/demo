"""If node - route items based on a condition (true/false outputs)."""

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

if TYPE_CHECKING:
    from ...engine.types import ExecutionContext, NodeData, NodeDefinition, NodeExecutionResult

from ...engine.expression_engine import expression_engine, ExpressionEngine


class IfNode(BaseNode):
    """If node - route items based on a condition with true/false outputs."""

    node_description = NodeTypeDescription(
        name="If",
        display_name="If",
        description="Route items based on a condition (true/false outputs)",
        icon="fa:code-branch",
        group=["flow"],
        inputs=[NodeInputDefinition(name="main", display_name="Input")],
        outputs=[
            NodeOutputDefinition(
                name="true",
                display_name="True",
                schema={"type": "unknown", "passthrough": True},
            ),
            NodeOutputDefinition(
                name="false",
                display_name="False",
                schema={"type": "unknown", "passthrough": True},
            ),
        ],
        properties=[
            NodeProperty(
                display_name="Condition",
                name="condition",
                type="string",
                default="",
                placeholder="{{ $json.score >= 70 }}",
                description="Expression that evaluates to true/false. If provided, field/operation/value are ignored.",
            ),
            NodeProperty(
                display_name="Field",
                name="field",
                type="string",
                default="",
                placeholder="status",
                description="Field path to evaluate (supports dot notation). Leave empty to evaluate entire input.",
            ),
            NodeProperty(
                display_name="Operation",
                name="operation",
                type="options",
                default="isTrue",
                options=[
                    NodePropertyOption(name="Equals", value="equals"),
                    NodePropertyOption(name="Not Equals", value="notEquals"),
                    NodePropertyOption(name="Contains", value="contains"),
                    NodePropertyOption(name="Not Contains", value="notContains"),
                    NodePropertyOption(name="Greater Than", value="gt"),
                    NodePropertyOption(name="Greater or Equal", value="gte"),
                    NodePropertyOption(name="Less Than", value="lt"),
                    NodePropertyOption(name="Less or Equal", value="lte"),
                    NodePropertyOption(name="Is Empty", value="isEmpty"),
                    NodePropertyOption(name="Is Not Empty", value="isNotEmpty"),
                    NodePropertyOption(name="Is True", value="isTrue"),
                    NodePropertyOption(name="Is False", value="isFalse"),
                    NodePropertyOption(name="Regex Match", value="regex"),
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
    )

    @property
    def type(self) -> str:
        return "If"

    @property
    def description(self) -> str:
        return "Route items based on a condition (true/false outputs)"

    async def execute(
        self,
        context: ExecutionContext,
        node_definition: NodeDefinition,
        input_data: list[NodeData],
    ) -> NodeExecutionResult:
        condition = self.get_parameter(node_definition, "condition", "")
        field = self.get_parameter(node_definition, "field", "")
        operation = self.get_parameter(node_definition, "operation", "isTrue")
        value = node_definition.parameters.get("value")

        true_output: list[NodeData] = []
        false_output: list[NodeData] = []

        for idx, item in enumerate(input_data):
            # If condition expression is provided, use expression engine
            if condition:
                expr_context = ExpressionEngine.create_context(
                    input_data,
                    context.node_states,
                    context.execution_id,
                    item_index=idx,
                )
                result = expression_engine.resolve(condition, expr_context)
                # Convert to bool
                result = bool(result)
            else:
                # Use field/operation/value approach
                field_value = self._get_nested_value(item.json, field)
                result = self._evaluate(field_value, operation, value)

            if result:
                true_output.append(item)
            else:
                false_output.append(item)

        true_count = len(true_output)
        false_count = len(false_output)
        branch = "true" if true_count > 0 else "false"
        if true_count > 0 and false_count > 0:
            branch = "both"

        return self.outputs(
            {
                "true": true_output if true_output else None,
                "false": false_output if false_output else None,
            },
            metadata={
                "branchDecision": branch,
                "trueCount": true_count,
                "falseCount": false_count,
            },
        )

    def _evaluate(self, field_value: Any, operation: str, compare_value: Any) -> bool:
        """Evaluate the condition."""
        if operation == "equals":
            return field_value == compare_value
        elif operation == "notEquals":
            return field_value != compare_value
        elif operation == "contains":
            return str(compare_value) in str(field_value)
        elif operation == "notContains":
            return str(compare_value) not in str(field_value)
        elif operation == "gt":
            try:
                return float(field_value) > float(compare_value)
            except (ValueError, TypeError):
                return False
        elif operation == "gte":
            try:
                return float(field_value) >= float(compare_value)
            except (ValueError, TypeError):
                return False
        elif operation == "lt":
            try:
                return float(field_value) < float(compare_value)
            except (ValueError, TypeError):
                return False
        elif operation == "lte":
            try:
                return float(field_value) <= float(compare_value)
            except (ValueError, TypeError):
                return False
        elif operation == "isEmpty":
            return field_value is None or field_value == "" or field_value == []
        elif operation == "isNotEmpty":
            return field_value is not None and field_value != "" and field_value != []
        elif operation == "isTrue":
            return field_value is True or field_value == "true" or field_value == 1
        elif operation == "isFalse":
            return field_value is False or field_value == "false" or field_value == 0
        elif operation == "regex":
            try:
                return bool(re.search(str(compare_value), str(field_value)))
            except re.error:
                return False
        else:
            return bool(field_value)

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
