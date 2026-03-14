"""Workflow execution tool for AI agents."""

from __future__ import annotations

import json
from typing import Any, TYPE_CHECKING

from ...base import (
    NodeProperty,
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
        tool_name = self.get_parameter(node_definition, "toolName", "run_workflow")
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
            # Async executor — receives (input_data, context)
            "execute": _make_workflow_executor(workflow_id),
        }


def _make_workflow_executor(workflow_id: str):
    """Create an async executor closure that captures the workflow ID."""

    async def execute(input_data: dict[str, Any], context: ExecutionContext) -> dict[str, Any]:
        from ....engine.workflow_runner import WorkflowRunner
        from ....engine.types import NodeData, RecursionLimitError

        if not workflow_id:
            return {"error": "workflowId is not configured"}

        repo = context.workflow_repository
        if repo is None:
            return {"error": "No workflow repository available (subworkflows not supported in this execution mode)"}

        # Load the target workflow
        stored = await repo.get(workflow_id)
        if stored is None:
            return {"error": f"Workflow '{workflow_id}' not found"}

        workflow = stored.workflow

        # Find start node
        runner = WorkflowRunner()
        start_node = runner.find_start_node(workflow)
        if start_node is None:
            return {"error": f"Workflow '{workflow_id}' has no start node"}

        # Build input data
        wf_input = input_data.get("input") or {}
        sub_input = [NodeData(json=wf_input)]

        try:
            child_ctx = await runner.run_subworkflow(
                workflow=workflow,
                start_node_name=start_node,
                input_data=sub_input,
                parent_context=context,
                on_event=context.on_event,
            )
        except RecursionLimitError as e:
            return {"error": str(e)}
        except Exception as e:
            return {"error": f"Subworkflow execution failed: {e}"}

        # Check for errors
        if child_ctx.errors:
            error_msgs = [f"{e.node_name}: {e.error}" for e in child_ctx.errors]
            return {"error": "Subworkflow had errors", "details": error_msgs}

        # Collect terminal node outputs
        connected_sources = {c.source_node for c in workflow.connections}
        all_nodes = {n.name for n in workflow.nodes}
        terminal_nodes = all_nodes - connected_sources

        results: dict[str, Any] = {}
        for node_name in terminal_nodes:
            if node_name in child_ctx.node_states:
                data = child_ctx.node_states[node_name]
                results[node_name] = [d.json for d in data]

        return {
            "workflow_id": workflow_id,
            "workflow_name": workflow.name,
            "output": results,
        }

    return execute
