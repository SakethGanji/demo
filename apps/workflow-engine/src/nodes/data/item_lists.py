"""ItemLists node - sort, limit, deduplicate, and aggregate data."""

from __future__ import annotations

import json
from collections import defaultdict
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
    from ...engine.types import ExecutionContext, NodeDefinition, NodeExecutionResult

from ...engine.types import NodeData


class ItemListsNode(BaseNode):
    """ItemLists node - perform list operations like sort, limit, deduplicate, aggregate."""

    node_description = NodeTypeDescription(
        name="ItemLists",
        display_name="Item Lists",
        description="Sort, limit, remove duplicates, or aggregate items",
        icon="fa:list-ol",
        group=["transform"],
        inputs=[NodeInputDefinition(name="main", display_name="Input")],
        outputs=[
            NodeOutputDefinition(
                name="main",
                display_name="Output",
                schema={"type": "unknown"},
            ),
        ],
        properties=[
            NodeProperty(
                display_name="Operation",
                name="operation",
                type="options",
                default="sort",
                options=[
                    NodePropertyOption(
                        name="Sort",
                        value="sort",
                        description="Sort items by a field",
                    ),
                    NodePropertyOption(
                        name="Limit",
                        value="limit",
                        description="Limit the number of items",
                    ),
                    NodePropertyOption(
                        name="Remove Duplicates",
                        value="removeDuplicates",
                        description="Remove duplicate items",
                    ),
                    NodePropertyOption(
                        name="Aggregate",
                        value="aggregate",
                        description="Group and aggregate items",
                    ),
                    NodePropertyOption(
                        name="Summarize",
                        value="summarize",
                        description="Combine all items into one",
                    ),
                    NodePropertyOption(
                        name="Split Out",
                        value="splitOut",
                        description="Split array field into separate items",
                    ),
                    NodePropertyOption(
                        name="Concatenate",
                        value="concatenate",
                        description="Merge items from multiple inputs",
                    ),
                ],
            ),
            # Sort options
            NodeProperty(
                display_name="Sort By",
                name="sortBy",
                type="string",
                default="",
                placeholder="fieldName or nested.field",
                description="Field to sort by (supports dot notation)",
                display_options={"show": {"operation": ["sort"]}},
            ),
            NodeProperty(
                display_name="Order",
                name="order",
                type="options",
                default="ascending",
                options=[
                    NodePropertyOption(name="Ascending", value="ascending"),
                    NodePropertyOption(name="Descending", value="descending"),
                ],
                display_options={"show": {"operation": ["sort"]}},
            ),
            NodeProperty(
                display_name="Sort Type",
                name="sortType",
                type="options",
                default="auto",
                options=[
                    NodePropertyOption(name="Auto", value="auto", description="Detect type automatically"),
                    NodePropertyOption(name="String", value="string", description="Sort as text"),
                    NodePropertyOption(name="Number", value="number", description="Sort as numbers"),
                ],
                display_options={"show": {"operation": ["sort"]}},
            ),
            # Limit options
            NodeProperty(
                display_name="Max Items",
                name="maxItems",
                type="number",
                default=10,
                description="Maximum number of items to return",
                display_options={"show": {"operation": ["limit"]}},
            ),
            NodeProperty(
                display_name="Offset",
                name="offset",
                type="number",
                default=0,
                description="Number of items to skip (for pagination)",
                display_options={"show": {"operation": ["limit"]}},
            ),
            # Remove duplicates options
            NodeProperty(
                display_name="Compare Field",
                name="compareField",
                type="string",
                default="",
                placeholder="id or user.email",
                description="Field to compare for uniqueness (empty = compare entire object)",
                display_options={"show": {"operation": ["removeDuplicates"]}},
            ),
            NodeProperty(
                display_name="Keep",
                name="keep",
                type="options",
                default="first",
                options=[
                    NodePropertyOption(name="First", value="first", description="Keep first occurrence"),
                    NodePropertyOption(name="Last", value="last", description="Keep last occurrence"),
                ],
                display_options={"show": {"operation": ["removeDuplicates"]}},
            ),
            # Aggregate options
            NodeProperty(
                display_name="Group By",
                name="groupBy",
                type="string",
                default="",
                placeholder="category",
                description="Field to group by",
                display_options={"show": {"operation": ["aggregate"]}},
            ),
            NodeProperty(
                display_name="Aggregations",
                name="aggregations",
                type="collection",
                default=[],
                description="Aggregation operations to perform",
                display_options={"show": {"operation": ["aggregate"]}},
                type_options={"multipleValues": True},
                properties=[
                    NodeProperty(
                        display_name="Field",
                        name="field",
                        type="string",
                        default="",
                        placeholder="amount",
                        description="Field to aggregate",
                    ),
                    NodeProperty(
                        display_name="Operation",
                        name="aggOperation",
                        type="options",
                        default="sum",
                        options=[
                            NodePropertyOption(name="Sum", value="sum"),
                            NodePropertyOption(name="Average", value="avg"),
                            NodePropertyOption(name="Count", value="count"),
                            NodePropertyOption(name="Min", value="min"),
                            NodePropertyOption(name="Max", value="max"),
                            NodePropertyOption(name="First", value="first"),
                            NodePropertyOption(name="Last", value="last"),
                            NodePropertyOption(name="Collect", value="collect", description="Collect all values into array"),
                        ],
                    ),
                    NodeProperty(
                        display_name="Output Field",
                        name="outputField",
                        type="string",
                        default="",
                        placeholder="totalAmount",
                        description="Name for the output field",
                    ),
                ],
            ),
            # Summarize options
            NodeProperty(
                display_name="Output Field",
                name="summarizeField",
                type="string",
                default="items",
                description="Field name for the array of all items",
                display_options={"show": {"operation": ["summarize"]}},
            ),
            NodeProperty(
                display_name="Include Count",
                name="includeCount",
                type="boolean",
                default=True,
                description="Include a count of items",
                display_options={"show": {"operation": ["summarize"]}},
            ),
            # Split out options
            NodeProperty(
                display_name="Array Field",
                name="arrayField",
                type="string",
                default="",
                placeholder="items",
                description="Field containing the array to split",
                display_options={"show": {"operation": ["splitOut"]}},
            ),
            NodeProperty(
                display_name="Include Other Fields",
                name="includeOther",
                type="boolean",
                default=True,
                description="Include other fields from parent item",
                display_options={"show": {"operation": ["splitOut"]}},
            ),
        ],
    )

    @property
    def type(self) -> str:
        return "ItemLists"

    @property
    def description(self) -> str:
        return "Sort, limit, remove duplicates, or aggregate items"

    async def execute(
        self,
        context: ExecutionContext,
        node_definition: NodeDefinition,
        input_data: list[NodeData],
    ) -> NodeExecutionResult:
        operation = self.get_parameter(node_definition, "operation", "sort")

        if operation == "sort":
            return self._sort(node_definition, input_data)
        elif operation == "limit":
            return self._limit(node_definition, input_data)
        elif operation == "removeDuplicates":
            return self._remove_duplicates(node_definition, input_data)
        elif operation == "aggregate":
            return self._aggregate(node_definition, input_data)
        elif operation == "summarize":
            return self._summarize(node_definition, input_data)
        elif operation == "splitOut":
            return self._split_out(node_definition, input_data)
        elif operation == "concatenate":
            # Concatenate just passes through all items (useful after Merge)
            return self.output(input_data)
        else:
            return self.output(input_data)

    def _sort(self, node_def: NodeDefinition, input_data: list[NodeData]) -> NodeExecutionResult:
        """Sort items by a field."""
        sort_by = self.get_parameter(node_def, "sortBy", "")
        order = self.get_parameter(node_def, "order", "ascending")
        sort_type = self.get_parameter(node_def, "sortType", "auto")

        if not sort_by:
            return self.output(input_data)

        def get_sort_key(item: NodeData) -> Any:
            value = self._get_nested_value(item.json, sort_by)
            if sort_type == "number":
                try:
                    return float(value) if value is not None else float("inf")
                except (ValueError, TypeError):
                    return float("inf")
            elif sort_type == "string":
                return str(value) if value is not None else ""
            else:
                # Auto-detect
                if isinstance(value, (int, float)):
                    return value
                return str(value) if value is not None else ""

        reverse = order == "descending"
        sorted_items = sorted(input_data, key=get_sort_key, reverse=reverse)
        return self.output(sorted_items)

    def _limit(self, node_def: NodeDefinition, input_data: list[NodeData]) -> NodeExecutionResult:
        """Limit number of items."""
        max_items = int(self.get_parameter(node_def, "maxItems", 10))
        offset = int(self.get_parameter(node_def, "offset", 0))

        limited = input_data[offset : offset + max_items]
        return self.output(limited)

    def _remove_duplicates(self, node_def: NodeDefinition, input_data: list[NodeData]) -> NodeExecutionResult:
        """Remove duplicate items."""
        compare_field = self.get_parameter(node_def, "compareField", "")
        keep = self.get_parameter(node_def, "keep", "first")

        seen: dict[str, NodeData] = {}

        for item in input_data:
            if compare_field:
                key_value = self._get_nested_value(item.json, compare_field)
                key = json.dumps(key_value, sort_keys=True, default=str)
            else:
                key = json.dumps(item.json, sort_keys=True, default=str)

            if keep == "first":
                if key not in seen:
                    seen[key] = item
            else:  # last
                seen[key] = item

        # Preserve original order for "first", reverse for "last"
        if keep == "first":
            unique = list(seen.values())
        else:
            unique = list(reversed(list(seen.values())))

        return self.output(unique)

    def _aggregate(self, node_def: NodeDefinition, input_data: list[NodeData]) -> NodeExecutionResult:
        """Group and aggregate items."""
        group_by = self.get_parameter(node_def, "groupBy", "")
        aggregations = self.get_parameter(node_def, "aggregations", [])

        # Group items
        groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for item in input_data:
            if group_by:
                key_value = self._get_nested_value(item.json, group_by)
                key = json.dumps(key_value, sort_keys=True, default=str)
            else:
                key = "_all"
            groups[key].append(item.json)

        # Perform aggregations
        results: list[NodeData] = []
        for key, items in groups.items():
            result: dict[str, Any] = {}

            # Add group key
            if group_by:
                result[group_by] = json.loads(key)

            # Add count
            result["_count"] = len(items)

            # Perform each aggregation
            for agg in aggregations:
                field = agg.get("field", "")
                agg_op = agg.get("aggOperation", "sum")
                output_field = agg.get("outputField", "") or f"{field}_{agg_op}"

                values = [self._get_nested_value(item, field) for item in items]
                values = [v for v in values if v is not None]

                if agg_op == "sum":
                    try:
                        result[output_field] = sum(float(v) for v in values)
                    except (ValueError, TypeError):
                        result[output_field] = 0
                elif agg_op == "avg":
                    try:
                        nums = [float(v) for v in values]
                        result[output_field] = sum(nums) / len(nums) if nums else 0
                    except (ValueError, TypeError):
                        result[output_field] = 0
                elif agg_op == "count":
                    result[output_field] = len(values)
                elif agg_op == "min":
                    try:
                        nums = [float(v) for v in values]
                        result[output_field] = min(nums) if nums else None
                    except (ValueError, TypeError):
                        result[output_field] = min(values, default=None)
                elif agg_op == "max":
                    try:
                        nums = [float(v) for v in values]
                        result[output_field] = max(nums) if nums else None
                    except (ValueError, TypeError):
                        result[output_field] = max(values, default=None)
                elif agg_op == "first":
                    result[output_field] = values[0] if values else None
                elif agg_op == "last":
                    result[output_field] = values[-1] if values else None
                elif agg_op == "collect":
                    result[output_field] = values

            results.append(NodeData(json=result))

        return self.output(results)

    def _summarize(self, node_def: NodeDefinition, input_data: list[NodeData]) -> NodeExecutionResult:
        """Combine all items into a single item."""
        output_field = self.get_parameter(node_def, "summarizeField", "items")
        include_count = self.get_parameter(node_def, "includeCount", True)

        all_items = [item.json for item in input_data]
        result: dict[str, Any] = {output_field: all_items}

        if include_count:
            result["count"] = len(all_items)

        return self.output([NodeData(json=result)])

    def _split_out(self, node_def: NodeDefinition, input_data: list[NodeData]) -> NodeExecutionResult:
        """Split array field into separate items."""
        array_field = self.get_parameter(node_def, "arrayField", "")
        include_other = self.get_parameter(node_def, "includeOther", True)

        if not array_field:
            return self.output(input_data)

        results: list[NodeData] = []
        for item in input_data:
            array_value = self._get_nested_value(item.json, array_field)
            if not isinstance(array_value, list):
                # Not an array, pass through as-is
                results.append(item)
                continue

            for element in array_value:
                if include_other:
                    # Include other fields from parent
                    new_json = {k: v for k, v in item.json.items() if k != array_field}
                    if isinstance(element, dict):
                        new_json.update(element)
                    else:
                        new_json[array_field] = element
                else:
                    if isinstance(element, dict):
                        new_json = element
                    else:
                        new_json = {array_field: element}

                results.append(NodeData(json=new_json, binary=item.binary))

        return self.output(results)

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
