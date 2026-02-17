"""Report node - generates HTML/Markdown data reports via the analytics service."""

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


class ReportNode(BaseNode):
    """Report node - generates data reports via the analytics service API."""

    node_description = NodeTypeDescription(
        name="Report",
        display_name="Report",
        description="Generate HTML data reports with profiling, distributions, and aggregation",
        icon="fa:file-alt",
        group=["transform"],
        inputs=[NodeInputDefinition(name="main", display_name="Input")],
        outputs=[
            NodeOutputDefinition(
                name="main",
                display_name="Report Output",
                schema={
                    "type": "object",
                    "properties": {
                        "html": {"type": "string", "description": "Generated HTML report"},
                        "_renderAs": {"type": "string", "description": "Render hint for UI"},
                        "row_count": {"type": "number", "description": "Total row count"},
                        "column_count": {"type": "number", "description": "Total column count"},
                        "output_path": {"type": "string", "description": "File path where report was saved"},
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
                ],
            ),
            NodeProperty(
                display_name="File Location",
                name="fileLocation",
                type="options",
                default="local",
                required=True,
                options=[
                    NodePropertyOption(
                        name="Local File System",
                        value="local",
                        description="File on local machine",
                    ),
                    NodePropertyOption(
                        name="S3",
                        value="s3",
                        description="File in S3 bucket (coming soon)",
                    ),
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
                type_options={
                    "extensions": ".csv,.parquet,.xlsx,.xls",
                },
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
                display_name="Data Field",
                name="dataField",
                type="string",
                default="data",
                placeholder="data",
                description="Field name containing array data from previous node",
                display_options={"show": {"sourceType": ["input"]}},
            ),
            NodeProperty(
                display_name="Report Title",
                name="title",
                type="string",
                default="Data Report",
                placeholder="Monthly Sales Report",
                description="Title for the generated report",
            ),
            NodeProperty(
                display_name="Preview Rows",
                name="previewRows",
                type="number",
                default=10,
                description="Number of rows to show in the data preview section",
            ),
            NodeProperty(
                display_name="Top N Values",
                name="topN",
                type="number",
                default=10,
                description="Number of top frequent values per column",
            ),
            NodeProperty(
                display_name="Show Overview",
                name="showOverview",
                type="boolean",
                default=True,
                description="Include overview cards (row count, columns, duplicates, memory)",
            ),
            NodeProperty(
                display_name="Show Column Stats",
                name="showColumnStats",
                type="boolean",
                default=True,
                description="Include column statistics table",
            ),
            NodeProperty(
                display_name="Show Distributions",
                name="showDistributions",
                type="boolean",
                default=True,
                description="Include distribution histograms for numeric columns",
            ),
            NodeProperty(
                display_name="Show Top Values",
                name="showTopValues",
                type="boolean",
                default=True,
                description="Include top values per column",
            ),
            NodeProperty(
                display_name="Show Correlations",
                name="showCorrelations",
                type="boolean",
                default=False,
                description="Include correlation matrix for numeric columns",
            ),
            NodeProperty(
                display_name="Show Data Preview",
                name="showDataPreview",
                type="boolean",
                default=True,
                description="Include a preview of the first N rows",
            ),
            NodeProperty(
                display_name="Group By",
                name="groupBy",
                type="string",
                default="",
                placeholder="department, region",
                description="Comma-separated columns for aggregation section (leave empty to skip)",
            ),
            NodeProperty(
                display_name="Aggregations",
                name="aggregations",
                type="collection",
                default={},
                description="Aggregation specifications for the report",
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
                            NodePropertyOption(name="Mean", value="mean", description="Average"),
                            NodePropertyOption(name="Median", value="median", description="Median"),
                            NodePropertyOption(name="Count", value="count", description="Count"),
                            NodePropertyOption(name="Min", value="min", description="Minimum"),
                            NodePropertyOption(name="Max", value="max", description="Maximum"),
                            NodePropertyOption(name="Std Dev", value="std", description="Standard deviation"),
                            NodePropertyOption(name="Unique Count", value="nunique", description="Unique values"),
                        ],
                    ),
                    NodeProperty(
                        display_name="Alias",
                        name="alias",
                        type="string",
                        default="",
                        placeholder="total_revenue",
                        description="Output column name",
                    ),
                ],
            ),
            NodeProperty(
                display_name="Output Path",
                name="outputPath",
                type="string",
                default="",
                placeholder="/output/report.html",
                description="Save report to this file path (leave empty to skip file export)",
            ),
            NodeProperty(
                display_name="Output Format",
                name="outputFormat",
                type="options",
                default="html",
                options=[
                    NodePropertyOption(name="HTML", value="html", description="HTML report"),
                    NodePropertyOption(name="Markdown", value="markdown", description="Markdown report"),
                    NodePropertyOption(name="PDF", value="pdf", description="PDF document"),
                ],
            ),
        ],
    )

    @property
    def type(self) -> str:
        return "Report"

    @property
    def description(self) -> str:
        return "Generate HTML data reports with profiling, distributions, and aggregation"

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
        data_field = self.get_parameter(node_definition, "dataField", "data")
        title = self.get_parameter(node_definition, "title", "Data Report")
        preview_rows = self.get_parameter(node_definition, "previewRows", 10)
        top_n = self.get_parameter(node_definition, "topN", 10)
        show_overview = self.get_parameter(node_definition, "showOverview", True)
        show_column_stats = self.get_parameter(node_definition, "showColumnStats", True)
        show_distributions = self.get_parameter(node_definition, "showDistributions", True)
        show_top_values = self.get_parameter(node_definition, "showTopValues", True)
        show_correlations = self.get_parameter(node_definition, "showCorrelations", False)
        show_data_preview = self.get_parameter(node_definition, "showDataPreview", True)
        group_by_str = self.get_parameter(node_definition, "groupBy", "")
        aggregations_raw = self.get_parameter(node_definition, "aggregations", {})
        output_path = self.get_parameter(node_definition, "outputPath", "")
        output_format = self.get_parameter(node_definition, "outputFormat", "html")

        # Parse group_by
        group_by = [c.strip() for c in group_by_str.split(",") if c.strip()] if group_by_str else None

        # Extract aggregation items from collection
        agg_items: list[dict[str, Any]] | None = None
        raw_items: list[dict[str, Any]] = []
        if isinstance(aggregations_raw, dict):
            for _key, val in aggregations_raw.items():
                if isinstance(val, list):
                    for item in val:
                        if isinstance(item, dict):
                            raw_items.append({
                                "column": item.get("column", ""),
                                "function": item.get("function", "sum"),
                                "alias": item.get("alias") or None,
                            })
                elif isinstance(val, dict):
                    raw_items.append({
                        "column": val.get("column", ""),
                        "function": val.get("function", "sum"),
                        "alias": val.get("alias") or None,
                    })
        elif isinstance(aggregations_raw, list):
            for item in aggregations_raw:
                if isinstance(item, dict):
                    raw_items.append({
                        "column": item.get("column", ""),
                        "function": item.get("function", "sum"),
                        "alias": item.get("alias") or None,
                    })
        if raw_items:
            agg_items = raw_items

        # Build sections config
        sections = {
            "overview": bool(show_overview),
            "column_stats": bool(show_column_stats),
            "distributions": bool(show_distributions),
            "top_values": bool(show_top_values),
            "correlations": bool(show_correlations),
            "data_preview": bool(show_data_preview),
            "aggregation": bool(group_by and agg_items),
        }

        payload: dict[str, Any] = {
            "title": title or "Data Report",
            "sections": sections,
            "top_n": int(top_n) if top_n is not None else 10,
            "preview_rows": int(preview_rows) if preview_rows is not None else 10,
            "output_format": output_format or "html",
        }
        if group_by:
            payload["group_by"] = group_by
        if agg_items:
            payload["aggregations"] = agg_items

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

                item_payload = payload.copy()

                if source_type == "file":
                    if file_location == "local":
                        resolved_path = expression_engine.resolve(file_path, expr_context)
                        if not resolved_path:
                            raise ValueError("File path is required when source type is 'file'")
                        item_payload["file_path"] = str(resolved_path)
                    elif file_location == "s3":
                        resolved_uri = expression_engine.resolve(s3_uri, expr_context)
                        if not resolved_uri:
                            raise ValueError("S3 URI is required when file location is 's3'")
                        raise NotImplementedError("S3 file support is not yet implemented.")
                else:
                    item_json = item.json if item.json else {}
                    data_to_report: list[dict[str, Any]] | None = None

                    if data_field and data_field in item_json:
                        field_value = item_json[data_field]
                        if isinstance(field_value, list):
                            data_to_report = field_value
                    elif isinstance(item_json, list):
                        data_to_report = item_json
                    elif isinstance(item_json, dict) and "data" in item_json:
                        data_value = item_json["data"]
                        if isinstance(data_value, list):
                            data_to_report = data_value

                    if not data_to_report:
                        data_to_report = [item_json] if item_json else []

                    item_payload["data"] = data_to_report

                if output_path:
                    resolved_op = expression_engine.resolve(output_path, expr_context)
                    if resolved_op:
                        item_payload["output_path"] = str(resolved_op)

                url = f"{service_url.rstrip('/')}/report"
                response = await client.post(url, json=item_payload, timeout=120.0)
                response.raise_for_status()
                resp_data = response.json()

                # Add _renderAs hint so the UI can display the HTML
                output_data: dict[str, Any] = {
                    "row_count": resp_data.get("row_count"),
                    "column_count": resp_data.get("column_count"),
                    "output_path": resp_data.get("output_path"),
                }
                if resp_data.get("html"):
                    output_data["html"] = resp_data["html"]
                    output_data["_renderAs"] = "html"
                if resp_data.get("markdown"):
                    output_data["markdown"] = resp_data["markdown"]
                if resp_data.get("pdf_base64"):
                    output_data["pdf_base64"] = resp_data["pdf_base64"]
                    output_data["_renderAs"] = "pdf"

                results.append(ND(json=output_data))

        return self.output(results)
