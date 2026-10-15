"""Agent tool: call one node-as-function through the broker (phase 4, BACKEND-DESIGN §3.4).

Inline tool spec: ``{"type": "studioFunction", "parameters": {"function": "http_request"}}``.
The executor is async, so it receives the ExecutionContext and mints a run-scoped
broker token for the run's (project, environment). Unsandboxed: runs in the engine.
"""

from __future__ import annotations

from typing import Any, TYPE_CHECKING

from ...base import NodeProperty, NodeTypeDescription
from .._config_base import ConfigProvider

if TYPE_CHECKING:
    from ....engine.types import NodeDefinition


class StudioFunctionToolNode(ConfigProvider):
    node_description = NodeTypeDescription(
        name="StudioFunctionTool",
        display_name="Studio Function",
        description="Call a workflow node as a function (via the credential broker)",
        icon="fa:cubes",
        group=["ai"],
        inputs=[],
        outputs=[],
        properties=[
            NodeProperty(display_name="Function", name="function", type="string", default="", required=True,
                         description="Function name from GET /api/sdk/functions, e.g. http_request"),
        ],
    )

    def get_config(self, node_definition: NodeDefinition) -> dict[str, Any]:
        from ....sdk.generate import catalogue

        fn = self.get_parameter(node_definition, "function", "")
        entry = next((c for c in catalogue() if c["function"] == fn), None)
        if entry is None:
            raise ValueError(f"unknown studio function '{fn}'")
        schema = {
            "type": "object",
            "properties": {
                "parameters": {**entry["parameters"], "description": "Node parameters"},
                "items": {"type": "array", "items": {"type": "object"}, "description": "Input items (optional)"},
            },
            "required": ["parameters"],
        }

        async def execute(input_data: dict[str, Any], context: Any = None) -> dict[str, Any]:
            return await run_studio_function(entry["node_type"], input_data, context)

        return {"name": f"studio_{fn}", "description": entry["description"], "input_schema": schema, "execute": execute}


async def run_studio_function(node_type: str, input_data: dict[str, Any], context: Any) -> dict[str, Any]:
    from ....broker.service import BrokerService, mint_for_context

    if context is None:
        return {"error": "studio functions need an execution context"}
    token = mint_for_context(context, "agent")
    status, body = await BrokerService().call_with_token(
        token, None, node_type, input_data.get("parameters") or {}, input_data.get("items"))
    if not body.get("ok"):
        return {"error": body.get("error")}
    return {"items": body.get("items"), "metadata": body.get("metadata")}
