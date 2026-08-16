"""Execute Workflow node - executes another workflow as a subworkflow."""

from __future__ import annotations

import json
import logging
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
    from ...engine.types import ExecutionContext, ExecutionEvent, NodeData, NodeDefinition, NodeExecutionResult

logger = logging.getLogger(__name__)


class ExecuteWorkflowNode(BaseNode):
    """Execute Workflow node - executes another workflow as a subworkflow."""

    node_description = NodeTypeDescription(
        name="ExecuteWorkflow",
        display_name="Execute Workflow",
        description="Execute another workflow as a subworkflow",
        icon="fa:sitemap",
        group=["flow"],
        inputs=[NodeInputDefinition(name="main", display_name="Input")],
        outputs=[
            NodeOutputDefinition(
                name="main",
                display_name="Output",
                schema={
                    "type": "object",
                    "description": "Output from the executed subworkflow",
                },
            )
        ],
        properties=[
            NodeProperty(
                display_name="Workflow",
                name="workflowId",
                type="workflowSelector",
                required=True,
                description="The workflow to execute as a subworkflow",
            ),
            NodeProperty(
                display_name="Input Mode",
                name="inputMode",
                type="options",
                default="passThrough",
                options=[
                    NodePropertyOption(
                        name="Pass Through Input",
                        value="passThrough",
                        description="Pass the input data directly to the subworkflow",
                    ),
                    NodePropertyOption(
                        name="Custom Input",
                        value="custom",
                        description="Provide custom input data for the subworkflow",
                    ),
                ],
            ),
            NodeProperty(
                display_name="Custom Input",
                name="customInput",
                type="json",
                default="{}",
                description="Custom JSON input to pass to the subworkflow",
                display_options={"show": {"inputMode": ["custom"]}},
            ),
        ],
    )

    @property
    def type(self) -> str:
        return "ExecuteWorkflow"

    @property
    def description(self) -> str:
        return "Execute another workflow as a subworkflow"

    async def execute(
        self,
        context: ExecutionContext,
        node_definition: NodeDefinition,
        input_data: list[NodeData],
    ) -> NodeExecutionResult:
        from ...engine.types import NodeData, RecursionLimitError
        from ...engine.workflow_runner import WorkflowRunner

        # Get parameters
        workflow_id = self.get_parameter(node_definition, "workflowId")
        input_mode = self.get_parameter(node_definition, "inputMode", "passThrough")
        custom_input = self.get_parameter(node_definition, "customInput", "{}")

        if not workflow_id:
            raise ValueError("No workflow selected for execution")

        # Check recursion depth
        if context.execution_depth >= context.max_execution_depth:
            raise RecursionLimitError(
                f"Maximum subworkflow depth of {context.max_execution_depth} exceeded. "
                f"This may indicate infinite recursion."
            )

        # Get workflow repository from context
        workflow_repo = context.workflow_repository
        if not workflow_repo:
            raise ValueError(
                "Workflow repository not available in execution context. "
                "Cannot execute subworkflows."
            )

        # Load the target workflow
        stored_workflow = await workflow_repo.get(workflow_id)
        if not stored_workflow:
            raise ValueError(f"Workflow with ID '{workflow_id}' not found")

        # Prepare input data for subworkflow
        if input_mode == "custom":
            # Parse custom input JSON
            if isinstance(custom_input, str):
                try:
                    custom_data = json.loads(custom_input)
                except json.JSONDecodeError as e:
                    raise ValueError(f"Invalid custom input JSON: {e}")
            else:
                custom_data = custom_input
            subworkflow_input = [NodeData(json=custom_data)]
        else:
            # Pass through input data
            subworkflow_input = input_data if input_data else [NodeData(json={})]

        # Create runner and find start node
        runner = WorkflowRunner()
        start_node = runner.find_start_node(stored_workflow.workflow)
        if not start_node:
            raise ValueError(
                f"No start node found in workflow '{stored_workflow.name}'"
            )

        # Create a wrapper callback that tags events with parent node info
        parent_on_event = context.on_event
        tagged_on_event = None
        if parent_on_event:
            def tagged_on_event(event: 'ExecutionEvent') -> None:
                # Tag node-level events with subworkflow parent info
                if event.type.value.startswith("node:"):
                    event.subworkflow_parent_node = node_definition.name
                    event.subworkflow_id = workflow_id
                parent_on_event(event)

        # Execute subworkflow with depth tracking
        sub_context = await runner.run_subworkflow(
            workflow=stored_workflow.workflow,
            start_node_name=start_node.name,
            input_data=subworkflow_input,
            parent_context=context,
            on_event=tagged_on_event,
        )

        # Check for errors in subworkflow
        if sub_context.errors:
            error_messages = [e.error for e in sub_context.errors]
            raise RuntimeError(
                f"Subworkflow '{stored_workflow.name}' failed: {'; '.join(error_messages)}"
            )

        # Extract results from subworkflow
        # Find the last executed node's output or collect all outputs
        results: list[NodeData] = []

        if sub_context.node_states:
            # Get all node outputs and combine them
            combined_output: dict[str, Any] = {
                "_subworkflow": {
                    "id": workflow_id,
                    "name": stored_workflow.name,
                    "execution_id": sub_context.execution_id,
                }
            }

            # Find terminal nodes (nodes with no outgoing connections)
            source_nodes = {c.source_node for c in stored_workflow.workflow.connections}
            terminal_nodes = [
                n.name for n in stored_workflow.workflow.nodes
                if n.name not in source_nodes and n.type != "Start"
            ]

            # If there are terminal nodes, use their outputs
            if terminal_nodes:
                for node_name in terminal_nodes:
                    if node_name in sub_context.node_states:
                        node_output = sub_context.node_states[node_name]
                        if node_output:
                            # Use the last item's json as the primary result
                            combined_output.update(node_output[-1].json)
            else:
                # Fall back to last node state
                last_node_name = list(sub_context.node_states.keys())[-1]
                last_output = sub_context.node_states[last_node_name]
                if last_output:
                    combined_output.update(last_output[-1].json)

            results.append(NodeData(json=combined_output))
        else:
            # No output from subworkflow
            results.append(NodeData(json={
                "_subworkflow": {
                    "id": workflow_id,
                    "name": stored_workflow.name,
                    "execution_id": sub_context.execution_id,
                },
                "_empty": True,
            }))

        return self.output(results)
