"""Set node - create, update, or delete fields on items."""

from __future__ import annotations

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


class SetNode(BaseNode):
    """Set node - set, rename, or delete fields on items."""

    node_description = NodeTypeDescription(
        name="Set",
        display_name="Set",
        description="Set, rename, or delete fields on items",
        icon="fa:edit",
        group=["transform"],
        inputs=[NodeInputDefinition(name="main", display_name="Input")],
        outputs=[
            NodeOutputDefinition(
                name="main",
                display_name="Output",
                schema={
                    "type": "unknown",
                    "description": "Modified data with fields set/renamed/deleted",
                },
            )
        ],
        properties=[
            NodeProperty(
                display_name="Mode",
                name="mode",
                type="options",
                default="manual",
                options=[
                    NodePropertyOption(
                        name="Manual",
                        value="manual",
                        description="Define fields individually",
                    ),
                    NodePropertyOption(
                        name="JSON",
                        value="json",
                        description="Merge a JSON object",
                    ),
                ],
            ),
            NodeProperty(
                display_name="Fields to Set",
                name="fields",
                type="collection",
                default=[],
                type_options={"multipleValues": True},
                display_options={"show": {"mode": ["manual"]}},
                properties=[
                    NodeProperty(
                        display_name="Field Name",
                        name="name",
                        type="string",
                        default="",
                        placeholder="fieldName",
                        description="Supports dot notation for nested fields",
                    ),
                    NodeProperty(
                        display_name="Value",
                        name="value",
                        type="string",
                        default="",
                        description="Supports expressions: {{ $json.existingField }}",
                    ),
                ],
            ),
            NodeProperty(
                display_name="JSON Data",
                name="jsonData",
                type="json",
                default="{}",
                type_options={"language": "json", "rows": 8},
                description="JSON object to merge into each item",
                display_options={"show": {"mode": ["json"]}},
            ),
            NodeProperty(
                display_name="Keep Only Set",
                name="keepOnlySet",
                type="boolean",
                default=False,
                description="If true, removes all existing fields and only keeps new ones",
            ),
            NodeProperty(
                display_name="Fields to Delete",
                name="deleteFields",
                type="collection",
                default=[],
                type_options={"multipleValues": True},
                properties=[
                    NodeProperty(
                        display_name="Field Path",
                        name="path",
                        type="string",
                        default="",
                        placeholder="user.tempData",
                        description="Path to field to delete (supports dot notation)",
                    ),
                ],
            ),
            NodeProperty(
                display_name="Fields to Rename",
                name="renameFields",
                type="collection",
                default=[],
                type_options={"multipleValues": True},
                properties=[
                    NodeProperty(
                        display_name="From",
                        name="from",
                        type="string",
                        default="",
                        placeholder="oldFieldName",
                    ),
                    NodeProperty(
                        display_name="To",
                        name="to",
                        type="string",
                        default="",
                        placeholder="newFieldName",
                    ),
                ],
            ),
        ],
    )

    @property
    def type(self) -> str:
        return "Set"

    @property
    def description(self) -> str:
        return "Set, rename, or delete fields on items"

    async def execute(
        self,
        context: ExecutionContext,
        node_definition: NodeDefinition,
        input_data: list[NodeData],
    ) -> NodeExecutionResult:
        from ...engine.types import NodeData
        from ...engine.expression_engine import expression_engine, ExpressionEngine

        mode = self.get_parameter(node_definition, "mode", "manual")
        keep_only_set = self.get_parameter(node_definition, "keepOnlySet", False)

        results: list[NodeData] = []
        items = input_data if input_data else [NodeData(json={})]

        for idx, item in enumerate(items):
            if keep_only_set:
                new_json: dict[str, Any] = {}
            else:
                new_json = dict(item.json)

            # Build expression context for this item
            expr_context = ExpressionEngine.create_context(
                current_data=input_data,
                node_states=context.node_states,
                execution_id=context.execution_id,
                item_index=idx,
            )

            if mode == "manual":
                # Manual mode: explicit field definitions
                fields = self.get_parameter(node_definition, "fields", [])
                for field in fields:
                    if field.get("name"):
                        # Evaluate expression in value
                        raw_value = field.get("value", "")
                        resolved_value = expression_engine.resolve(raw_value, expr_context)
                        self._set_nested_value(new_json, field["name"], resolved_value)
            elif mode == "json":
                # JSON mode: merge entire JSON object
                json_data = self.get_parameter(node_definition, "jsonData", {})
                if isinstance(json_data, str):
                    # First resolve any expressions in the string
                    json_data = expression_engine.resolve(json_data, expr_context)
                    if isinstance(json_data, str):
                        import json
                        try:
                            json_data = json.loads(json_data)
                        except json.JSONDecodeError:
                            json_data = {}
                # Resolve expressions in nested values
                json_data = expression_engine.resolve(json_data, expr_context)
                new_json.update(json_data)

            # Handle field deletions
            delete_fields = self.get_parameter(node_definition, "deleteFields", [])
            for field in delete_fields:
                field_path = field.get("path") if isinstance(field, dict) else field
                if field_path:
                    self._delete_nested_value(new_json, field_path)

            # Handle field renames
            rename_fields = self.get_parameter(node_definition, "renameFields", [])
            for rename in rename_fields:
                from_path = rename.get("from", "")
                to_path = rename.get("to", "")
                if from_path and to_path:
                    value = self._get_nested_value(new_json, from_path)
                    if value is not None:
                        self._delete_nested_value(new_json, from_path)
                        self._set_nested_value(new_json, to_path, value)

            results.append(NodeData(json=new_json, binary=item.binary))

        return self.output(results)

    def _get_nested_value(self, obj: dict[str, Any], path: str) -> Any:
        """Get value at nested path."""
        current: Any = obj
        for key in path.split("."):
            if isinstance(current, dict):
                current = current.get(key)
            else:
                return None
        return current

    def _set_nested_value(self, obj: dict[str, Any], path: str, value: Any) -> None:
        """Set value at nested path, creating intermediate objects as needed."""
        keys = path.split(".")
        current = obj

        for key in keys[:-1]:
            if key not in current or not isinstance(current[key], dict):
                current[key] = {}
            current = current[key]

        current[keys[-1]] = value

    def _delete_nested_value(self, obj: dict[str, Any], path: str) -> None:
        """Delete value at nested path."""
        keys = path.split(".")
        current = obj

        for key in keys[:-1]:
            if key not in current or not isinstance(current[key], dict):
                return  # Path doesn't exist
            current = current[key]

        current.pop(keys[-1], None)
