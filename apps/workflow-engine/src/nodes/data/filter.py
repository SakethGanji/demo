"""Filter node - pass or reject items without branching."""

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

from ...engine.types import NodeData
from ...engine.expression_engine import expression_engine, ExpressionEngine


class FilterNode(BaseNode):
    """Filter node - pass only items that match a condition."""

    node_description = NodeTypeDescription(
        name="Filter",
        display_name="Filter",
        description="Filter items based on a condition (single output with matching items only)",
        icon="fa:filter",
        group=["transform"],
        inputs=[NodeInputDefinition(name="main", display_name="Input")],
        outputs=[
            NodeOutputDefinition(
                name="main",
                display_name="Kept",
                schema={"type": "unknown", "passthrough": True},
            ),
        ],
        properties=[
            NodeProperty(
                display_name="Mode",
                name="mode",
                type="options",
                default="rules",
                options=[
                    NodePropertyOption(
                        name="Rules",
                        value="rules",
                        description="Use field/operation/value rules",
                    ),
                    NodePropertyOption(
                        name="Expression",
                        value="expression",
                        description="Use an expression that evaluates to true/false",
                    ),
                ],
            ),
            NodeProperty(
                display_name="Condition",
                name="condition",
                type="string",
                default="",
                placeholder="{{ $json.score >= 70 }}",
                description="Expression that evaluates to true/false",
                display_options={"show": {"mode": ["expression"]}},
            ),
            NodeProperty(
                display_name="Field",
                name="field",
                type="string",
                default="",
                placeholder="status",
                description="Field path to evaluate (supports dot notation like user.age)",
                display_options={"show": {"mode": ["rules"]}},
            ),
            NodeProperty(
                display_name="Operation",
                name="operation",
                type="options",
                default="isNotEmpty",
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
                    NodePropertyOption(name="Is True", value="isTrue"),
                    NodePropertyOption(name="Is False", value="isFalse"),
                    NodePropertyOption(name="Is Null", value="isNull"),
                    NodePropertyOption(name="Is Not Null", value="isNotNull"),
                    NodePropertyOption(name="Regex Match", value="regex"),
                ],
                display_options={"show": {"mode": ["rules"]}},
            ),
            NodeProperty(
                display_name="Value",
                name="value",
                type="string",
                default="",
                description="Value to compare against. Supports expressions.",
                display_options={
                    "show": {"mode": ["rules"]},
                    "hide": {"operation": ["isEmpty", "isNotEmpty", "isTrue", "isFalse", "isNull", "isNotNull"]},
                },
            ),
        ],
    )

    @property
    def type(self) -> str:
        return "Filter"

    @property
    def description(self) -> str:
        return "Filter items based on a condition (single output with matching items only)"

    async def execute(
        self,
        context: ExecutionContext,
        node_definition: NodeDefinition,
        input_data: list[NodeData],
    ) -> NodeExecutionResult:
        mode = self.get_parameter(node_definition, "mode", "rules")
        condition = self.get_parameter(node_definition, "condition", "")
        field = self.get_parameter(node_definition, "field", "")
        operation = self.get_parameter(node_definition, "operation", "isNotEmpty")
        value = node_definition.parameters.get("value")

        kept: list[NodeData] = []

        for idx, item in enumerate(input_data):
            if mode == "expression" and condition:
                # Use expression engine
                expr_context = ExpressionEngine.create_context(
                    input_data,
                    context.node_states,
                    context.execution_id,
                    item_index=idx,
                )
                result = expression_engine.resolve(condition, expr_context)
                matches = bool(result)
            else:
                # Use field/operation/value rules
                field_value = self._get_nested_value(item.json, field)
                matches = self._evaluate(field_value, operation, value)

            if matches:
                kept.append(item)

        # Return matching items, or None if nothing matched
        return self.output(kept if kept else [])

    def _evaluate(self, field_value: Any, operation: str, compare_value: Any) -> bool:
        """Evaluate the condition."""
        if operation == "equals":
            return field_value == compare_value
        elif operation == "notEquals":
            return field_value != compare_value
        elif operation == "contains":
            return str(compare_value) in str(field_value) if field_value is not None else False
        elif operation == "notContains":
            return str(compare_value) not in str(field_value) if field_value is not None else True
        elif operation == "startsWith":
            return str(field_value).startswith(str(compare_value)) if field_value is not None else False
        elif operation == "endsWith":
            return str(field_value).endswith(str(compare_value)) if field_value is not None else False
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
            return field_value is None or field_value == "" or field_value == [] or field_value == {}
        elif operation == "isNotEmpty":
            return field_value is not None and field_value != "" and field_value != [] and field_value != {}
        elif operation == "isTrue":
            return field_value is True or field_value == "true" or field_value == 1
        elif operation == "isFalse":
            return field_value is False or field_value == "false" or field_value == 0
        elif operation == "isNull":
            return field_value is None
        elif operation == "isNotNull":
            return field_value is not None
        elif operation == "regex":
            try:
                return bool(re.search(str(compare_value), str(field_value))) if field_value is not None else False
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
            elif isinstance(current, list) and key.isdigit():
                idx = int(key)
                current = current[idx] if 0 <= idx < len(current) else None
            else:
                return None
        return current
