"""Workflow execution tool for AI agents."""

from __future__ import annotations

import math

import json
from typing import Any, TYPE_CHECKING

from ...base import (
    NodeProperty,
    NodePropertyOption,
    NodeTypeDescription,
)
from .._config_base import ConfigProvider

if TYPE_CHECKING:
    from ....engine.types import ExecutionContext, NodeDefinition


class WorkflowToolNode(ConfigProvider):
    """Workflow tool - trigger a saved workflow as an agent tool action.

    Enables hierarchical agent systems where one agent can delegate
    tasks to entire workflows (which may themselves contain agents).
    """

    node_description = NodeTypeDescription(
        name="WorkflowTool",
        display_name="Workflow Tool",
        description="Execute a saved workflow as an agent tool",
        icon="fa:project-diagram",
        group=["ai"],
        inputs=[],
        outputs=[],
        properties=[
            NodeProperty(
                display_name="Workflow ID",
                name="workflowId",
                type="string",
                default="",
                required=True,
                description="ID of the workflow to execute as a tool",
            ),
            NodeProperty(
                display_name="Version",
                name="version",
                type="options",
                default="latest",
                description="Which version of the workflow the agent runs: its latest published version, its draft, or a version number",
                options=[
                    NodePropertyOption(name="Latest published", value="latest"),
                    NodePropertyOption(name="Draft", value="draft"),
                ],
            ),
            NodeProperty(
                display_name="Timeout (seconds)",
                name="timeoutSeconds",
                type="number",
                default=300,
                description="How long one run of the workflow may take (at most 3600)",
            ),
            NodeProperty(
                display_name="Tool Name",
                name="toolName",
                type="string",
                default="run_workflow",
                description="Name the LLM will use to call this tool",
            ),
            NodeProperty(
                display_name="Description",
                name="description",
                type="string",
                default="Execute a workflow with the given input data. Pass a JSON object as input.",
                description="Description shown to the AI model",
                type_options={"rows": 3},
            ),
        ],
    )

    def get_config(self, node_definition: NodeDefinition) -> dict[str, Any]:
        """Return workflow tool configuration."""
        workflow_id = self.get_parameter(node_definition, "workflowId", "")
        version = self.get_parameter(node_definition, "version", "latest") or "latest"
        timeout = self.get_parameter(node_definition, "timeoutSeconds", 300)
        tool_name = self.get_parameter(node_definition, "toolName", "run_workflow")
        owner = self.get_parameter(node_definition, "_owner", "")
        # the name this call is pinned and logged under: per (agent node, tool), so two tools never share a release pin
        parent_node = f"{owner}/{tool_name}" if owner else "WorkflowTool"
        description = self.get_parameter(
            node_definition,
            "description",
            "Execute a workflow with the given input data.",
        )

        return {
            "name": tool_name,
            "description": description,
            "input_schema": {
                "type": "object",
                "properties": {
                    "input": {
                        "type": "object",
                        "description": "Input data to pass to the workflow",
                    },
                },
                "required": ["input"],
            },
            # Store workflow_id so the async executor can load it at runtime
            "_workflow_id": workflow_id,
            "_version": version,
            # Async executor — receives (input_data, context)
            "execute": _make_workflow_executor(workflow_id, version, timeout, parent_node),
        }


def _make_workflow_executor(
    workflow_id: str, version: str | int = "latest", timeout_seconds: Any = 300, parent_node: str = "WorkflowTool"
):
    """Create an async executor closure that captures the workflow ID and the pinned version.

    The call goes through ``context.subworkflow_invoker`` (the in-memory invoker when there is none), so the guards,
    the timeout, version pinning and the child execution row are the same as for the ExecuteWorkflow node (G9).
    """

    async def execute(input_data: dict[str, Any], context: ExecutionContext) -> dict[str, Any]:
        from ....engine.subworkflow import (
            DEFAULT_TIMEOUT_SECONDS,
            MAX_TIMEOUT_SECONDS,
            InMemoryInvoker,
            SubworkflowCall,
        )
        from ....engine.types import NodeData
        from ...flow.execute_workflow import _parse_version

        if not workflow_id:
            return {"error": "workflowId is not configured"}

        try:
            pinned = _parse_version(version)
        except ValueError as e:
            return {"error": str(e)}
        try:
            seconds = min(float(timeout_seconds), MAX_TIMEOUT_SECONDS)
            if not math.isfinite(seconds) or seconds <= 0:
                seconds = DEFAULT_TIMEOUT_SECONDS
        except (TypeError, ValueError):
            seconds = DEFAULT_TIMEOUT_SECONDS

        wf_input = input_data.get("input") or {}
        if not isinstance(wf_input, dict):
            wf_input = {"data": wf_input}
        call = SubworkflowCall(
            parent_context=context,
            parent_node=parent_node,
            workflow_id=workflow_id,
            version=pinned,
            items=[NodeData(json=wf_input)],
            timeout_seconds=seconds,
        )
        invoker = context.subworkflow_invoker or InMemoryInvoker()
        try:
            result = await invoker.invoke(call)
        except Exception as e:
            return {"error": f"Subworkflow execution failed: {e}"}

        if result.status != "success":
            err = result.error
            out: dict[str, Any] = {
                "error": err.error if err else f"Subworkflow '{workflow_id}' failed",
                "code": err.error_code if err else None,
            }
            if result.child_execution_id:
                out["child_execution_id"] = result.child_execution_id
            return out

        return {
            "workflow_id": workflow_id,
            "workflow_name": result.child_name or workflow_id,
            "version": result.child_version_number if result.child_version_number is not None else pinned,
            "child_execution_id": result.child_execution_id,
            "output": [item.json for item in result.items],
        }

    return execute
