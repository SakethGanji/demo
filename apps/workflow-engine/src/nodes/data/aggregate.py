"""Aggregate node - aggregates/pivots data using the analytics service."""

from __future__ import annotations

import os
from typing import Any, TYPE_CHECKING

import httpx

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


class AggregateNode(BaseNode):
    """Aggregate node - group-by aggregation via the analytics service API."""

    node_description = NodeTypeDescription(
        name="Aggregate",
        display_name="Aggregate",
        description="Group-by aggregation with sum, mean, count, and more via analytics service",
        icon="fa:layer-group",
        group=["transform"],
        inputs=[NodeInputDefinition(name="main", display_name="Input")],
        outputs=[
            NodeOutputDefinition(
                name="main",
                display_name="Aggregated Data",
                schema={
                    "type": "object",
                    "properties": {
                        "success": {"type": "boolean"},
                        "original_count": {"type": "number", "description": "Original row count"},
                        "group_count": {"type": "number", "description": "Number of groups"},
                        "columns": {"type": "array", "description": "Result column names"},
                        "totals": {"type": "object", "description": "Grand totals"},
                        "result_file": {"type": "string", "description": "Filename to fetch via GET /files/samples/{filename}"},
                    },
                },
            )
        ],
        properties=[
            NodeProperty(
                display_name="Source Type",
                name="sourceType",
                type="options",
                default="input",
                required=True,
                options=[
                    NodePropertyOption(
                        name="From Previous Node",
                        value="input",
                        description="Use data from previous node's output",
                    ),
                    NodePropertyOption(
                        name="From File",
                        value="file",
                        description="Read from a file",
                    ),
                    NodePropertyOption(
                        name="From Dataset",
                        value="dataset",
                        description="Use a dataset uploaded to the analytics service",
                    ),
                ],
            ),
            NodeProperty(
                display_name="File Location",
                name="fileLocation",
                type="options",
                default="local",
                required=True,
                options=[
                    NodePropertyOption(name="Local File System", value="local", description="File on local machine"),
                    NodePropertyOption(name="S3", value="s3", description="File in S3 bucket (coming soon)"),
                ],
                display_options={"show": {"sourceType": ["file"]}},
            ),
            NodeProperty(
                display_name="File Path",
                name="filePath",
                type="filePath",
                default="",
                placeholder="/path/to/data.csv",
                description="Path to CSV, Parquet, or Excel file",
                type_options={"extensions": ".csv,.parquet,.xlsx,.xls"},
                display_options={"show": {"sourceType": ["file"], "fileLocation": ["local"]}},
            ),
            NodeProperty(
                display_name="S3 URI",
                name="s3Uri",
                type="string",
                default="",
                placeholder="s3://bucket-name/path/to/file.csv",
                description="S3 URI to the file",
                display_options={"show": {"sourceType": ["file"], "fileLocation": ["s3"]}},
            ),
            NodeProperty(
                display_name="Dataset ID",
                name="datasetId",
                type="string",
                default="",
                placeholder="dataset-uuid",
                description="ID of the dataset in the analytics service",
                display_options={"show": {"sourceType": ["dataset"]}},
            ),
            NodeProperty(
                display_name="Version",
                name="versionNumber",
                type="number",
                default=None,
                placeholder="1",
                description="Dataset version number (leave empty for latest)",
                display_options={"show": {"sourceType": ["dataset"]}},
            ),
            NodeProperty(
                display_name="Tag",
                name="tag",
                type="string",
                default="",
                placeholder="production",
                description="Version tag name (alternative to version number)",
                display_options={"show": {"sourceType": ["dataset"]}},
            ),
            NodeProperty(
                display_name="Sheet",
                name="sheet",
                type="string",
                default="",
                placeholder="Sheet1",
                description="Sheet name for multi-sheet datasets",
                display_options={"show": {"sourceType": ["dataset"]}},
            ),
            NodeProperty(
                display_name="Data Field",
                name="dataField",
                type="string",
                default="data",
                placeholder="data",
                description="Field name containing array data from previous node",
                display_options={"show": {"sourceType": ["input"]}},
            ),
            NodeProperty(
                display_name="Group By",
                name="groupBy",
                type="string",
                default="",
                required=True,
                placeholder="department, region",
                description="Comma-separated column names to group by",
            ),
            NodeProperty(
                display_name="Aggregations",
                name="aggregations",
                type="collection",
                default={},
                description="Aggregation specifications",
                type_options={"multipleValues": True},
                properties=[
                    NodeProperty(
                        display_name="Column",
                        name="column",
                        type="string",
                        default="",
                        required=True,
                        placeholder="revenue",
                        description="Column to aggregate",
                    ),
                    NodeProperty(
                        display_name="Function",
                        name="function",
                        type="options",
                        default="sum",
                        required=True,
                        options=[
                            NodePropertyOption(name="Sum", value="sum", description="Sum of values"),
                            NodePropertyOption(name="Mean", value="mean", description="Average of values"),
                            NodePropertyOption(name="Median", value="median", description="Median value"),
                            NodePropertyOption(name="Count", value="count", description="Count of non-null values"),
                            NodePropertyOption(name="Min", value="min", description="Minimum value"),
                            NodePropertyOption(name="Max", value="max", description="Maximum value"),
                            NodePropertyOption(name="Std Dev", value="std", description="Standard deviation"),
                            NodePropertyOption(name="Unique Count", value="nunique", description="Number of unique values"),
                            NodePropertyOption(name="First", value="first", description="First value in group"),
                            NodePropertyOption(name="Last", value="last", description="Last value in group"),
                        ],
                    ),
                    NodeProperty(
                        display_name="Alias",
                        name="alias",
                        type="string",
                        default="",
                        placeholder="total_revenue",
                        description="Output column name (defaults to column_function)",
                    ),
                ],
            ),
            NodeProperty(
                display_name="Sort By",
                name="sortBy",
                type="string",
                default="",
                placeholder="total_revenue",
                description="Column to sort results by",
            ),
            NodeProperty(
                display_name="Sort Order",
                name="sortOrder",
                type="options",
                default="desc",
                options=[
                    NodePropertyOption(name="Descending", value="desc", description="Highest first"),
                    NodePropertyOption(name="Ascending", value="asc", description="Lowest first"),
                ],
            ),
            NodeProperty(
                display_name="Limit",
                name="limit",
                type="number",
                default=None,
                placeholder="10",
                description="Maximum number of groups to return",
            ),
            NodeProperty(
                display_name="Filter Expression",
                name="filterExpr",
                type="string",
                default="",
                placeholder="revenue > 1000",
                description="SQL WHERE expression to filter data before aggregation",
            ),
        ],
    )

    @property
    def type(self) -> str:
        return "Aggregate"

    @property
    def description(self) -> str:
        return "Group-by aggregation with sum, mean, count, and more via analytics service"

    async def execute(
        self,
        context: ExecutionContext,
        node_definition: NodeDefinition,
        input_data: list[NodeData],
    ) -> NodeExecutionResult:
        from ...engine.types import NodeData as ND
        from ...engine.expression_engine import ExpressionEngine, expression_engine

        service_url = os.getenv("ANALYTICS_SERVICE_URL", "http://localhost:8001")

        source_type = self.get_parameter(node_definition, "sourceType", "input")
        file_location = self.get_parameter(node_definition, "fileLocation", "local")
        file_path = self.get_parameter(node_definition, "filePath", "")
        s3_uri = self.get_parameter(node_definition, "s3Uri", "")
        dataset_id = self.get_parameter(node_definition, "datasetId", "")
        version_number = self.get_parameter(node_definition, "versionNumber")
        tag = self.get_parameter(node_definition, "tag", "")
        sheet = self.get_parameter(node_definition, "sheet", "")
        data_field = self.get_parameter(node_definition, "dataField", "data")
        group_by_str = self.get_parameter(node_definition, "groupBy", "")
        aggregations_raw = self.get_parameter(node_definition, "aggregations", {})
        sort_by = self.get_parameter(node_definition, "sortBy", "")
        sort_order = self.get_parameter(node_definition, "sortOrder", "desc")
        limit = self.get_parameter(node_definition, "limit")
        filter_expr = self.get_parameter(node_definition, "filterExpr", "")

        # Parse group_by
        group_by = [c.strip() for c in group_by_str.split(",") if c.strip()]
        if not group_by:
            raise ValueError("At least one group-by column is required")

        # Extract aggregation items from collection
        agg_items: list[dict[str, Any]] = []
        if isinstance(aggregations_raw, dict):
            for key, val in aggregations_raw.items():
                if isinstance(val, list):
                    for item in val:
                        if isinstance(item, dict):
                            agg_items.append({
                                "column": item.get("column", ""),
                                "function": item.get("function", "sum"),
                                "alias": item.get("alias") or None,
                            })
                elif isinstance(val, dict):
                    agg_items.append({
                        "column": val.get("column", ""),
                        "function": val.get("function", "sum"),
                        "alias": val.get("alias") or None,
                    })
        elif isinstance(aggregations_raw, list):
            for item in aggregations_raw:
                if isinstance(item, dict):
                    agg_items.append({
                        "column": item.get("column", ""),
                        "function": item.get("function", "sum"),
                        "alias": item.get("alias") or None,
                    })

        if not agg_items:
            raise ValueError("At least one aggregation specification is required")

        payload: dict[str, Any] = {
            "group_by": group_by,
            "aggregations": agg_items,
            "return_data": False,
        }
        if sort_by:
            payload["sort_by"] = sort_by
        payload["sort_order"] = sort_order
        if limit is not None:
            payload["limit"] = int(limit)
        if filter_expr:
            payload["filter_expr"] = filter_expr

        results: list[ND] = []
        items = input_data if input_data else [ND(json={})]

        async with httpx.AsyncClient() as client:
            for idx, item in enumerate(items):
                expr_context = ExpressionEngine.create_context(
                    input_data,
                    context.node_states,
                    context.execution_id,
                    idx,
                )

                item_payload = {**payload}

                if source_type == "dataset":
                    resolved_id = expression_engine.resolve(dataset_id, expr_context)
                    if not resolved_id:
                        raise ValueError("Dataset ID is required when source type is 'dataset'")
                    item_payload["dataset_id"] = str(resolved_id)
                    if version_number is not None:
                        item_payload["version_number"] = int(version_number)
                    if tag:
                        resolved_tag = expression_engine.resolve(tag, expr_context)
                        if resolved_tag:
                            item_payload["tag"] = str(resolved_tag)
                    if sheet:
                        resolved_sheet = expression_engine.resolve(sheet, expr_context)
                        if resolved_sheet:
                            item_payload["sheet"] = str(resolved_sheet)
                elif source_type == "file":
                    if file_location == "local":
                        resolved_path = expression_engine.resolve(file_path, expr_context)
                        if not resolved_path:
                            raise ValueError("File path is required when source type is 'file'")
                        item_payload["file_path"] = str(resolved_path)
                    elif file_location == "s3":
                        raise NotImplementedError("S3 file support is not yet implemented.")
                else:
                    item_json = item.json if item.json else {}
                    data_to_agg: list[dict[str, Any]] | None = None

                    if data_field and data_field in item_json:
                        field_value = item_json[data_field]
                        if isinstance(field_value, list):
                            data_to_agg = field_value
                    elif isinstance(item_json, list):
                        data_to_agg = item_json
                    elif isinstance(item_json, dict) and "data" in item_json:
                        data_value = item_json["data"]
                        if isinstance(data_value, list):
                            data_to_agg = data_value

                    if not data_to_agg:
                        data_to_agg = [item_json] if item_json else []

                    item_payload["data"] = data_to_agg

                url = f"{service_url.rstrip('/')}/aggregate"
                response = await client.post(url, json=item_payload, timeout=60.0)
                response.raise_for_status()
                resp = response.json()

                # Return metadata only — no full data
                results.append(ND(json={
                    "success": resp.get("success"),
                    "original_count": resp.get("original_count"),
                    "group_count": resp.get("group_count"),
                    "columns": resp.get("columns", []),
                    "totals": resp.get("totals"),
                    "result_file": resp.get("result_file"),
                }))

        return self.output(results)
