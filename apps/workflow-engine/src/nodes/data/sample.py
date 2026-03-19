"""Sample node - samples data using the analytics service."""

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


class SampleNode(BaseNode):
    """Sample node - samples data using the analytics service API."""

    node_description = NodeTypeDescription(
        name="Sample",
        display_name="Sample",
        description="Samples data using various sampling methods via analytics service",
        icon="fa:filter",
        group=["transform"],
        inputs=[NodeInputDefinition(name="main", display_name="Input")],
        outputs=[
            NodeOutputDefinition(
                name="main",
                display_name="Sampled Data",
                schema={
                    "type": "object",
                    "properties": {
                        "success": {"type": "boolean"},
                        "original_count": {"type": "number", "description": "Original row count"},
                        "sampled_count": {"type": "number", "description": "Sampled row count"},
                        "sample_file": {"type": "string", "description": "Filename to fetch via GET /files/samples/{filename}"},
                        "columns": {"type": "array", "description": "Column summaries"},
                        "preview": {"type": "array", "description": "Preview rows"},
                        "steps_summary": {"type": "array", "description": "Per-step execution summary"},
                        "goal_validation": {"type": "object", "description": "Distribution goal validation result"},
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
                        description="Read from a CSV or Parquet file",
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
                description="Path to CSV or Parquet file",
                type_options={"extensions": ".csv,.parquet"},
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
                display_name="Target Total Volume",
                name="targetTotalVolume",
                type="number",
                default=100,
                required=True,
                description="Target total number of rows in the final sample",
            ),
            NodeProperty(
                display_name="Sampling Method",
                name="method",
                type="options",
                default="random",
                required=True,
                options=[
                    NodePropertyOption(name="Random", value="random", description="Simple random sampling"),
                    NodePropertyOption(name="Stratified", value="stratified", description="Proportional sampling by a column"),
                    NodePropertyOption(name="Systematic", value="systematic", description="Every nth row"),
                    NodePropertyOption(name="Cluster", value="cluster", description="Select whole clusters randomly"),
                    NodePropertyOption(name="Weighted", value="weighted", description="Sample weighted by a numeric column"),
                    NodePropertyOption(name="Time-Stratified", value="time_stratified", description="Equal sampling across time bins"),
                    NodePropertyOption(name="Deduplicate", value="deduplicate", description="Remove duplicate rows"),
                ],
            ),
            NodeProperty(
                display_name="Sample Size",
                name="sampleSize",
                type="number",
                default=None,
                placeholder="100",
                description="Number of rows to sample in this step (takes precedence over fraction)",
            ),
            NodeProperty(
                display_name="Sample Fraction",
                name="sampleFraction",
                type="number",
                default=None,
                placeholder="0.1",
                description="Fraction of rows to sample (0-1). Used if sample size is not set.",
            ),
            NodeProperty(
                display_name="With Replacement",
                name="replace",
                type="boolean",
                default=False,
                description="Sample with replacement (allows duplicates)",
                display_options={"show": {"method": ["random", "stratified"]}},
            ),
            NodeProperty(
                display_name="Stratify Column",
                name="stratifyColumn",
                type="string",
                default="",
                placeholder="category",
                description="Column name for stratified sampling",
                display_options={"show": {"method": ["stratified"]}},
            ),
            NodeProperty(
                display_name="Cluster Column",
                name="clusterColumn",
                type="string",
                default="",
                placeholder="region",
                description="Column name that defines clusters",
                display_options={"show": {"method": ["cluster"]}},
            ),
            NodeProperty(
                display_name="Number of Clusters",
                name="numClusters",
                type="number",
                default=None,
                placeholder="3",
                description="Number of clusters to randomly select",
                display_options={"show": {"method": ["cluster"]}},
            ),
            NodeProperty(
                display_name="Weight Column",
                name="weightColumn",
                type="string",
                default="",
                placeholder="weight",
                description="Column with numeric weights (higher = more likely)",
                display_options={"show": {"method": ["weighted"]}},
            ),
            NodeProperty(
                display_name="Time Column",
                name="timeColumn",
                type="string",
                default="",
                placeholder="created_at",
                description="Date/datetime column for time-stratified sampling",
                display_options={"show": {"method": ["time_stratified"]}},
            ),
            NodeProperty(
                display_name="Time Bins",
                name="timeBins",
                type="number",
                default=10,
                description="Number of equal time bins to stratify across",
                display_options={"show": {"method": ["time_stratified"]}},
            ),
            NodeProperty(
                display_name="Deduplicate Columns",
                name="deduplicateColumns",
                type="string",
                default="",
                placeholder="col1, col2",
                description="Columns to deduplicate on (empty = all columns)",
                display_options={"show": {"method": ["deduplicate"]}},
            ),
            NodeProperty(
                display_name="Filter Expression",
                name="filterExpr",
                type="string",
                default="",
                placeholder="region = 'US'",
                description="SQL WHERE filter applied before sampling",
            ),
            NodeProperty(
                display_name="Rounds",
                name="rounds",
                type="number",
                default=1,
                description="Number of sampling rounds (each draws from remaining pool)",
                display_options={"show": {"method": ["random", "stratified", "systematic"]}},
            ),
            NodeProperty(
                display_name="Random Seed",
                name="seed",
                type="number",
                default=None,
                placeholder="42",
                description="Random seed for reproducibility",
            ),
            NodeProperty(
                display_name="Shuffle Output",
                name="shuffle",
                type="boolean",
                default=False,
                description="Randomly shuffle the final output rows",
            ),
            NodeProperty(
                display_name="Sort By",
                name="sortBy",
                type="string",
                default="",
                placeholder="created_at",
                description="Column to sort the final output by",
            ),
            NodeProperty(
                display_name="Sort Descending",
                name="sortDescending",
                type="boolean",
                default=False,
                description="Sort in descending order",
            ),
        ],
    )

    @property
    def type(self) -> str:
        return "Sample"

    @property
    def description(self) -> str:
        return "Samples data using various sampling methods via analytics service"

    async def execute(
        self,
        context: ExecutionContext,
        node_definition: NodeDefinition,
        input_data: list[NodeData],
    ) -> NodeExecutionResult:
        from ...engine.types import NodeData as ND
        from ...engine.expression_engine import ExpressionEngine, expression_engine

        service_url = os.getenv("ANALYTICS_SERVICE_URL", "http://localhost:8001")

        # Get parameters
        source_type = self.get_parameter(node_definition, "sourceType", "input")
        file_location = self.get_parameter(node_definition, "fileLocation", "local")
        file_path = self.get_parameter(node_definition, "filePath", "")
        s3_uri = self.get_parameter(node_definition, "s3Uri", "")
        dataset_id = self.get_parameter(node_definition, "datasetId", "")
        version_number = self.get_parameter(node_definition, "versionNumber")
        tag = self.get_parameter(node_definition, "tag", "")
        sheet = self.get_parameter(node_definition, "sheet", "")
        data_field = self.get_parameter(node_definition, "dataField", "data")

        target_total_volume = self.get_parameter(node_definition, "targetTotalVolume", 100)
        method = self.get_parameter(node_definition, "method", "random")
        sample_size = self.get_parameter(node_definition, "sampleSize")
        sample_fraction = self.get_parameter(node_definition, "sampleFraction")
        replace = self.get_parameter(node_definition, "replace", False)
        stratify_column = self.get_parameter(node_definition, "stratifyColumn", "")
        cluster_column = self.get_parameter(node_definition, "clusterColumn", "")
        num_clusters = self.get_parameter(node_definition, "numClusters")
        weight_column = self.get_parameter(node_definition, "weightColumn", "")
        time_column = self.get_parameter(node_definition, "timeColumn", "")
        time_bins = self.get_parameter(node_definition, "timeBins", 10)
        deduplicate_columns_str = self.get_parameter(node_definition, "deduplicateColumns", "")
        filter_expr = self.get_parameter(node_definition, "filterExpr", "")
        rounds = self.get_parameter(node_definition, "rounds", 1)
        seed = self.get_parameter(node_definition, "seed")
        shuffle = self.get_parameter(node_definition, "shuffle", False)
        sort_by = self.get_parameter(node_definition, "sortBy", "")
        sort_descending = self.get_parameter(node_definition, "sortDescending", False)

        # Build sampling step
        step: dict[str, Any] = {"method": method}

        if sample_size is not None:
            step["sample_size"] = int(sample_size)
        elif sample_fraction is not None:
            step["sample_fraction"] = float(sample_fraction)

        if method in ("random", "stratified"):
            step["replace"] = bool(replace)
        if method == "stratified" and stratify_column:
            step["stratify_column"] = stratify_column
        if method == "cluster":
            if cluster_column:
                step["cluster_column"] = cluster_column
            if num_clusters is not None:
                step["num_clusters"] = int(num_clusters)
        if method == "weighted" and weight_column:
            step["weight_column"] = weight_column
        if method == "time_stratified":
            if time_column:
                step["time_column"] = time_column
            step["time_bins"] = int(time_bins) if time_bins else 10
        if method == "deduplicate" and deduplicate_columns_str:
            step["deduplicate_columns"] = [c.strip() for c in deduplicate_columns_str.split(",") if c.strip()]

        if filter_expr:
            step["filter_expr"] = filter_expr
        if rounds is not None and int(rounds) > 1:
            step["rounds"] = int(rounds)

        # Build request payload (matches SampleRequest schema)
        payload: dict[str, Any] = {
            "target_total_volume": int(target_total_volume),
            "sampling_steps": [step],
            "return_data": False,
        }
        if seed is not None:
            payload["seed"] = int(seed)
        if shuffle:
            payload["shuffle"] = True
        if sort_by:
            payload["sort_by"] = sort_by
            payload["sort_descending"] = bool(sort_descending)

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

                item_payload = {k: v for k, v in payload.items()}
                # Deep copy the step
                item_payload["sampling_steps"] = [{**step}]

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
                    # Input data from previous node
                    item_json = item.json if item.json else {}
                    data_to_sample: list[dict[str, Any]] | None = None

                    if data_field and data_field in item_json:
                        field_value = item_json[data_field]
                        if isinstance(field_value, list):
                            data_to_sample = field_value
                    elif isinstance(item_json, list):
                        data_to_sample = item_json
                    elif isinstance(item_json, dict) and "data" in item_json:
                        data_value = item_json["data"]
                        if isinstance(data_value, list):
                            data_to_sample = data_value

                    if not data_to_sample:
                        data_to_sample = [item_json] if item_json else []

                    item_payload["data"] = data_to_sample

                url = f"{service_url.rstrip('/')}/sample"
                response = await client.post(url, json=item_payload, timeout=60.0)
                response.raise_for_status()
                resp = response.json()

                # Return metadata only — no full data
                results.append(ND(json={
                    "success": resp.get("success"),
                    "original_count": resp.get("original_count"),
                    "sampled_count": resp.get("sampled_count"),
                    "sample_file": resp.get("sample_file"),
                    "columns": resp.get("columns", []),
                    "preview": resp.get("preview", []),
                    "steps_summary": resp.get("steps_summary", []),
                    "goal_validation": resp.get("goal_validation"),
                    "reproducibility": resp.get("reproducibility"),
                }))

        return self.output(results)
