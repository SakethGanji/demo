"""Profile node - profiles data using the analytics service."""

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


class ProfileNode(BaseNode):
    """Profile node - profiles data columns using the analytics service API."""

    node_description = NodeTypeDescription(
        name="Profile",
        display_name="Data Profile",
        description="Profile data columns — statistics, distributions, and data quality via analytics service",
        icon="fa:chart-bar",
        group=["transform"],
        inputs=[NodeInputDefinition(name="main", display_name="Input")],
        outputs=[
            NodeOutputDefinition(
                name="main",
                display_name="Profile Result",
                schema={
                    "type": "object",
                    "properties": {
                        "success": {"type": "boolean"},
                        "row_count": {"type": "number", "description": "Total row count"},
                        "column_count": {"type": "number", "description": "Total column count"},
                        "columns": {"type": "array", "description": "Per-column profile data"},
                        "correlations": {"type": "object", "description": "Correlation matrix (if requested)"},
                        "memory_usage_bytes": {"type": "number", "description": "Memory usage in bytes"},
                        "duplicate_row_count": {"type": "number", "description": "Number of duplicate rows"},
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
                display_name="Columns",
                name="columns",
                type="string",
                default="",
                placeholder="col1, col2, col3",
                description="Comma-separated column names to profile (empty = all columns)",
            ),
            NodeProperty(
                display_name="Include Histograms",
                name="includeHistograms",
                type="boolean",
                default=True,
                description="Include histograms for numeric columns",
            ),
            NodeProperty(
                display_name="Include Correlations",
                name="includeCorrelations",
                type="boolean",
                default=False,
                description="Include correlation matrix for numeric columns",
            ),
            NodeProperty(
                display_name="Include Duplicates",
                name="includeDuplicates",
                type="boolean",
                default=True,
                description="Count duplicate rows (can be expensive on large datasets)",
            ),
            NodeProperty(
                display_name="Top N Values",
                name="topN",
                type="number",
                default=10,
                description="Number of top frequent values to return per column",
            ),
        ],
    )

    @property
    def type(self) -> str:
        return "Profile"

    @property
    def description(self) -> str:
        return "Profile data columns — statistics, distributions, and data quality via analytics service"

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
        columns_str = self.get_parameter(node_definition, "columns", "")
        include_histograms = self.get_parameter(node_definition, "includeHistograms", True)
        include_correlations = self.get_parameter(node_definition, "includeCorrelations", False)
        include_duplicates = self.get_parameter(node_definition, "includeDuplicates", True)
        top_n = self.get_parameter(node_definition, "topN", 10)

        columns = [c.strip() for c in columns_str.split(",") if c.strip()] if columns_str else None

        payload: dict[str, Any] = {
            "include_histograms": bool(include_histograms),
            "include_correlations": bool(include_correlations),
            "include_duplicates": bool(include_duplicates),
            "top_n": int(top_n) if top_n is not None else 10,
        }
        if columns:
            payload["columns"] = columns

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
                    data_to_profile: list[dict[str, Any]] | None = None

                    if data_field and data_field in item_json:
                        field_value = item_json[data_field]
                        if isinstance(field_value, list):
                            data_to_profile = field_value
                    elif isinstance(item_json, list):
                        data_to_profile = item_json
                    elif isinstance(item_json, dict) and "data" in item_json:
                        data_value = item_json["data"]
                        if isinstance(data_value, list):
                            data_to_profile = data_value

                    if not data_to_profile:
                        data_to_profile = [item_json] if item_json else []

                    item_payload["data"] = data_to_profile

                url = f"{service_url.rstrip('/')}/profile"
                response = await client.post(url, json=item_payload, timeout=60.0)
                response.raise_for_status()
                results.append(ND(json=response.json()))

        return self.output(results)
