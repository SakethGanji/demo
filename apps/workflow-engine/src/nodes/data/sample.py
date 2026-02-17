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
                        "original_count": {"type": "number", "description": "Original row count"},
                        "sampled_count": {"type": "number", "description": "Sampled row count"},
                        "method": {"type": "string", "description": "Sampling method used"},
                        "data": {"type": "array", "description": "Sampled data rows"},
                        "rounds_completed": {"type": "number", "description": "Number of sampling rounds completed"},
                        "round_counts": {"type": "array", "description": "Per-round row counts", "items": {"type": "number"}},
                        "clusters_selected": {"type": "array", "description": "Selected cluster names", "items": {"type": "string"}},
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
                description="Path to CSV or Parquet file",
                type_options={
                    "extensions": ".csv,.parquet",
                },
                display_options={"show": {"sourceType": ["file"], "fileLocation": ["local"]}},
            ),
            NodeProperty(
                display_name="S3 URI",
                name="s3Uri",
                type="string",
                default="",
                placeholder="s3://bucket-name/path/to/file.csv",
                description="S3 URI to the file (e.g., s3://my-bucket/data/file.csv)",
                display_options={"show": {"sourceType": ["file"], "fileLocation": ["s3"]}},
            ),
            NodeProperty(
                display_name="Data Field",
                name="dataField",
                type="string",
                default="data",
                placeholder="data",
                description="Field name containing array data from previous node (e.g., 'data', 'items', 'rows')",
                display_options={"show": {"sourceType": ["input"]}},
            ),
            NodeProperty(
                display_name="Sampling Method",
                name="method",
                type="options",
                default="random",
                required=True,
                options=[
                    NodePropertyOption(
                        name="Random",
                        value="random",
                        description="Simple random sampling",
                    ),
                    NodePropertyOption(
                        name="Stratified",
                        value="stratified",
                        description="Proportional sampling by a column",
                    ),
                    NodePropertyOption(
                        name="Systematic",
                        value="systematic",
                        description="Every nth row",
                    ),
                    NodePropertyOption(
                        name="Cluster",
                        value="cluster",
                        description="Select whole clusters randomly",
                    ),
                    NodePropertyOption(
                        name="First N",
                        value="first_n",
                        description="First N rows (head)",
                    ),
                    NodePropertyOption(
                        name="Last N",
                        value="last_n",
                        description="Last N rows (tail)",
                    ),
                ],
            ),
            NodeProperty(
                display_name="Sample Size",
                name="sampleSize",
                type="number",
                default=100,
                description="Number of rows to sample (takes precedence over fraction)",
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
                display_name="Random Seed",
                name="seed",
                type="number",
                default=None,
                placeholder="42",
                description="Random seed for reproducibility",
                display_options={"show": {"method": ["random", "stratified", "cluster"]}},
            ),
            NodeProperty(
                display_name="Sampling Rounds",
                name="rounds",
                type="number",
                default=1,
                description="Number of sampling rounds (each round draws from remaining rows)",
                display_options={"show": {"method": ["random", "stratified", "systematic"]}},
            ),
            NodeProperty(
                display_name="Per-Round Sample Size",
                name="roundSampleSize",
                type="number",
                default=None,
                placeholder="10",
                description="Number of rows per round (overrides sample size when rounds > 1)",
                display_options={"show": {"method": ["random", "stratified", "systematic"]}},
            ),
            NodeProperty(
                display_name="Per-Round Fraction",
                name="roundSampleFraction",
                type="number",
                default=None,
                placeholder="0.1",
                description="Fraction of rows per round",
                display_options={"show": {"method": ["random", "stratified", "systematic"]}},
            ),
            NodeProperty(
                display_name="Output Path",
                name="outputPath",
                type="string",
                default="",
                placeholder="/output/sampled.csv",
                description="Save sampled data to this file path (leave empty to skip file export)",
            ),
            NodeProperty(
                display_name="Output Format",
                name="outputFormat",
                type="options",
                default="csv",
                options=[
                    NodePropertyOption(name="CSV", value="csv", description="Comma-separated values"),
                    NodePropertyOption(name="Excel", value="xlsx", description="Excel spreadsheet"),
                    NodePropertyOption(name="Parquet", value="parquet", description="Apache Parquet"),
                ],
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
        data_field = self.get_parameter(node_definition, "dataField", "data")
        method = self.get_parameter(node_definition, "method", "random")
        sample_size = self.get_parameter(node_definition, "sampleSize")
        sample_fraction = self.get_parameter(node_definition, "sampleFraction")
        replace = self.get_parameter(node_definition, "replace", False)
        stratify_column = self.get_parameter(node_definition, "stratifyColumn", "")
        cluster_column = self.get_parameter(node_definition, "clusterColumn", "")
        num_clusters = self.get_parameter(node_definition, "numClusters")
        seed = self.get_parameter(node_definition, "seed")
        rounds = self.get_parameter(node_definition, "rounds", 1)
        round_sample_size = self.get_parameter(node_definition, "roundSampleSize")
        round_sample_fraction = self.get_parameter(node_definition, "roundSampleFraction")
        output_path = self.get_parameter(node_definition, "outputPath", "")
        output_format = self.get_parameter(node_definition, "outputFormat", "csv")

        # Build request payload
        payload: dict[str, Any] = {"method": method}

        if sample_size is not None:
            payload["sample_size"] = int(sample_size)
        elif sample_fraction is not None:
            payload["sample_fraction"] = float(sample_fraction)

        if method in ("random", "stratified"):
            payload["replace"] = bool(replace)

        if method == "stratified" and stratify_column:
            payload["stratify_column"] = stratify_column

        if method == "cluster":
            if cluster_column:
                payload["cluster_column"] = cluster_column
            if num_clusters is not None:
                payload["num_clusters"] = int(num_clusters)

        if seed is not None:
            payload["seed"] = int(seed)

        if rounds is not None and int(rounds) > 1:
            payload["rounds"] = int(rounds)
            if round_sample_size is not None:
                payload["round_sample_size"] = int(round_sample_size)
            if round_sample_fraction is not None:
                payload["round_sample_fraction"] = float(round_sample_fraction)

        results: list[ND] = []
        items = input_data if input_data else [ND(json={})]

        async def make_request(client: httpx.AsyncClient, request_payload: dict[str, Any]) -> dict[str, Any]:
            url = f"{service_url.rstrip('/')}/sample"
            response = await client.post(url, json=request_payload, timeout=60.0)
            response.raise_for_status()
            return response.json()

        async with httpx.AsyncClient() as client:
            for idx, item in enumerate(items):
                # Create expression context for this item
                expr_context = ExpressionEngine.create_context(
                    input_data,
                    context.node_states,
                    context.execution_id,
                    idx,
                )

                item_payload = payload.copy()

                if source_type == "file":
                    if file_location == "local":
                        # Resolve file path expressions for local files
                        resolved_path = expression_engine.resolve(file_path, expr_context)
                        if not resolved_path:
                            raise ValueError("File path is required when source type is 'file'")
                        item_payload["file_path"] = str(resolved_path)
                    elif file_location == "s3":
                        # S3 support - not yet implemented
                        resolved_uri = expression_engine.resolve(s3_uri, expr_context)
                        if not resolved_uri:
                            raise ValueError("S3 URI is required when file location is 's3'")
                        raise NotImplementedError(
                            "S3 file support is not yet implemented. Please use local file system."
                        )
                else:
                    # Use data from input
                    item_json = item.json if item.json else {}

                    # Try to get data from the specified field, or use the whole item
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
                        # If no array data found, wrap the item in a list
                        data_to_sample = [item_json] if item_json else []

                    item_payload["data"] = data_to_sample

                if output_path:
                    resolved_op = expression_engine.resolve(output_path, expr_context)
                    if resolved_op:
                        item_payload["output_path"] = str(resolved_op)
                        item_payload["output_format"] = output_format or "csv"

                response_data = await make_request(client, item_payload)
                results.append(ND(json=response_data))

        return self.output(results)
