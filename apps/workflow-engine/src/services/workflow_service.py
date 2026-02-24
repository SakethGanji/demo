"""Workflow service for business logic."""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING, Any

from ..core.exceptions import (
    WorkflowNotFoundError,
    WorkflowExecutionError,
    WorkflowInactiveError,
    ValidationError,
)
from ..engine.types import (
    Workflow,
    NodeData,
)
from ..engine.workflow_runner import WorkflowRunner
from ..schemas.workflow import (
    WorkflowCreateRequest,
    WorkflowUpdateRequest,
    WorkflowListItem,
    WorkflowDetailResponse,
    WorkflowResponse,
    WorkflowActiveResponse,
)
from ..schemas.execution import ExecutionResponse, ExecutionErrorSchema

if TYPE_CHECKING:
    from ..repositories import WorkflowRepository, ExecutionRepository
    from .node_service import NodeService


class WorkflowService:
    """Service for workflow operations."""

    def __init__(
        self,
        workflow_repo: WorkflowRepository,
        execution_repo: ExecutionRepository,
        node_service: NodeService,
        node_registry: object | None = None,
    ) -> None:
        self._workflow_repo = workflow_repo
        self._execution_repo = execution_repo
        self._node_service = node_service
        self._node_registry = node_registry

    async def list_workflows(self) -> list[WorkflowListItem]:
        """List all workflows."""
        workflows = await self._workflow_repo.list()
        return [
            WorkflowListItem(
                id=w.id,
                name=w.name,
                active=w.active,
                webhook_url=f"/webhook/{w.id}",
                node_count=len(w.workflow.nodes),
                created_at=w.created_at.isoformat(),
                updated_at=w.updated_at.isoformat(),
            )
            for w in workflows
        ]

    async def get_workflow(self, workflow_id: str) -> WorkflowDetailResponse:
        """Get a workflow by ID."""
        stored = await self._workflow_repo.get(workflow_id)
        if not stored:
            raise WorkflowNotFoundError(workflow_id)

        return WorkflowDetailResponse(
            id=stored.id,
            name=stored.name,
            active=stored.active,
            webhook_url=f"/webhook/{stored.id}",
            definition=self._workflow_to_dict(stored.workflow),
            created_at=stored.created_at.isoformat(),
            updated_at=stored.updated_at.isoformat(),
        )

    async def create_workflow(self, request: WorkflowCreateRequest) -> WorkflowResponse:
        """Create a new workflow."""
        self._validate_workflow(request)

        internal_workflow = self._request_to_workflow(request)
        stored = await self._workflow_repo.create(internal_workflow)

        return WorkflowResponse(
            id=stored.id,
            name=stored.name,
            active=stored.active,
            webhook_url=f"/webhook/{stored.id}",
            created_at=stored.created_at.isoformat(),
        )

    async def update_workflow(
        self, workflow_id: str, request: WorkflowUpdateRequest
    ) -> WorkflowDetailResponse:
        """Update an existing workflow."""
        existing = await self._workflow_repo.get(workflow_id)
        if not existing:
            raise WorkflowNotFoundError(workflow_id)

        # Validate updated nodes/connections if provided
        self._validate_update(request, existing)

        # Build updated workflow from existing + request
        internal_workflow = Workflow(
            name=request.name or existing.workflow.name,
            nodes=[n.to_dataclass() for n in request.nodes] if request.nodes else existing.workflow.nodes,
            connections=[c.to_dataclass() for c in request.connections] if request.connections else existing.workflow.connections,
            id=workflow_id,
            description=request.description or existing.workflow.description,
            settings=request.settings or existing.workflow.settings,
        )

        updated = await self._workflow_repo.update(workflow_id, internal_workflow)
        if not updated:
            raise WorkflowNotFoundError(workflow_id)

        return WorkflowDetailResponse(
            id=updated.id,
            name=updated.name,
            active=updated.active,
            webhook_url=f"/webhook/{updated.id}",
            definition=self._workflow_to_dict(updated.workflow),
            created_at=updated.created_at.isoformat(),
            updated_at=updated.updated_at.isoformat(),
        )

    async def delete_workflow(self, workflow_id: str) -> bool:
        """Delete a workflow."""
        deleted = await self._workflow_repo.delete(workflow_id)
        if not deleted:
            raise WorkflowNotFoundError(workflow_id)
        return True

    async def set_active(self, workflow_id: str, active: bool) -> WorkflowActiveResponse:
        """Set workflow active state."""
        updated = await self._workflow_repo.set_active(workflow_id, active)
        if not updated:
            raise WorkflowNotFoundError(workflow_id)

        return WorkflowActiveResponse(id=updated.id, active=updated.active)

    async def run_workflow(
        self, workflow_id: str, input_data: dict[str, Any] | None = None
    ) -> ExecutionResponse:
        """Run a saved workflow with optional input data."""
        stored = await self._workflow_repo.get(workflow_id)
        if not stored:
            raise WorkflowNotFoundError(workflow_id)

        runner = WorkflowRunner()
        start_node = runner.find_start_node(stored.workflow)

        if not start_node:
            raise WorkflowExecutionError(
                "No start node found in workflow", workflow_id=workflow_id
            )

        # Build initial data - wrap in webhook-style format for consistency
        if input_data:
            initial_data = [
                NodeData(
                    json={
                        "body": input_data,
                        "headers": {},
                        "query": {},
                        "method": "POST",
                        "triggeredAt": datetime.now().isoformat(),
                    }
                )
            ]
            mode = "webhook"
        else:
            initial_data = [
                NodeData(
                    json={
                        "triggeredAt": datetime.now().isoformat(),
                        "mode": "manual",
                    }
                )
            ]
            mode = "manual"

        context = await runner.run(
            stored.workflow,
            start_node.name,
            initial_data,
            mode,
            workflow_repository=self._workflow_repo,
        )
        await self._execution_repo.complete(context, stored.id, stored.name)

        return self._build_execution_response(context)

    async def run_adhoc_workflow(self, request: WorkflowCreateRequest) -> ExecutionResponse:
        """Run an ad-hoc workflow without saving."""
        self._validate_workflow(request)
        internal_workflow = self._request_to_workflow(request)

        runner = WorkflowRunner()
        start_node = runner.find_start_node(internal_workflow)

        if not start_node:
            raise WorkflowExecutionError("No start node found in workflow")

        # Build initial data - wrap in webhook-style format for consistency
        if request.input_data:
            initial_data = [
                NodeData(
                    json={
                        "body": request.input_data,
                        "headers": {},
                        "query": {},
                        "method": "POST",
                        "triggeredAt": datetime.now().isoformat(),
                    }
                )
            ]
            mode = "webhook"
        else:
            initial_data = [
                NodeData(
                    json={
                        "triggeredAt": datetime.now().isoformat(),
                        "mode": "manual",
                    }
                )
            ]
            mode = "manual"

        context = await runner.run(
            internal_workflow,
            start_node.name,
            initial_data,
            mode,
            workflow_repository=self._workflow_repo,
        )
        await self._execution_repo.complete(
            context, internal_workflow.id or "adhoc", internal_workflow.name
        )

        return self._build_execution_response(context)

    def _validate_workflow(self, request: WorkflowCreateRequest) -> None:
        """Validate workflow request."""
        if not request.nodes:
            raise ValidationError("Workflow must have at least one node", field="nodes")

        node_names = [n.name for n in request.nodes]
        if len(node_names) != len(set(node_names)):
            raise ValidationError("Node names must be unique", field="nodes")

        # Validate node types exist in registry
        if self._node_registry is not None:
            for node in request.nodes:
                if not self._node_registry.has(node.type):
                    raise ValidationError(
                        f'Unknown node type: "{node.type}"',
                        field="nodes",
                    )

        # Validate connections reference valid nodes
        node_name_set = set(node_names)
        for conn in request.connections:
            if conn.source_node not in node_name_set:
                raise ValidationError(
                    f"Connection references unknown source node: {conn.source_node}",
                    field="connections",
                )
            if conn.target_node not in node_name_set:
                raise ValidationError(
                    f"Connection references unknown target node: {conn.target_node}",
                    field="connections",
                )

    def _validate_update(self, request: WorkflowUpdateRequest, existing: Any) -> None:
        """Validate workflow update request."""
        nodes = request.nodes or []
        connections = request.connections or []

        # Validate node types exist in registry
        if self._node_registry is not None:
            for node in nodes:
                if not self._node_registry.has(node.type):
                    raise ValidationError(
                        f'Unknown node type: "{node.type}"',
                        field="nodes",
                    )

        # If nodes are being updated, validate names are unique
        if nodes:
            node_names = [n.name for n in nodes]
            if len(node_names) != len(set(node_names)):
                raise ValidationError("Node names must be unique", field="nodes")

            # Validate connections reference valid nodes
            node_name_set = set(node_names)
            for conn in connections:
                if conn.source_node not in node_name_set:
                    raise ValidationError(
                        f"Connection references unknown source node: {conn.source_node}",
                        field="connections",
                    )
                if conn.target_node not in node_name_set:
                    raise ValidationError(
                        f"Connection references unknown target node: {conn.target_node}",
                        field="connections",
                    )

    def _request_to_workflow(self, request: WorkflowCreateRequest) -> Workflow:
        """Convert request to internal Workflow type."""
        return request.to_workflow()

    def _workflow_to_dict(self, workflow: Workflow) -> dict[str, Any]:
        """Convert internal Workflow to dict for API response."""
        enriched_nodes = []
        for n in workflow.nodes:
            # Compute dynamic I/O based on node type and parameters
            io_data = self._node_service.compute_node_io(n.type, n.parameters or {})

            # Get subnode metadata from node registry
            node_info = self._node_service._node_registry.get_node_type_info(n.type)

            node_dict: dict[str, Any] = {
                "name": n.name,
                "type": n.type,
                "label": n.label,
                "parameters": n.parameters,
                "position": n.position,
                "retry_on_fail": n.retry_on_fail,
                "retry_delay": n.retry_delay,
                "continue_on_fail": n.continue_on_fail,
                **({"pinnedData": [{"json": d.json} for d in n.pinned_data]} if n.pinned_data else {}),
                # Enriched I/O data for frontend
                "inputs": io_data["inputs"],
                "inputCount": io_data["inputCount"],
                "outputs": io_data["outputs"],
                "outputCount": io_data["outputCount"],
                "inputStrategy": io_data["inputStrategy"],
                "outputStrategy": io_data["outputStrategy"],
                # Node group for styling
                "group": io_data["group"],
                # Icon from node registry
                **({"icon": node_info.icon} if node_info and node_info.icon else {}),
            }

            # Add subnode metadata so frontend doesn't need to guess
            if node_info:
                node_dict["isSubnode"] = node_info.is_subnode
                node_dict["subnodeType"] = node_info.subnode_type
                if node_info.subnode_slots:
                    node_dict["subnodeSlots"] = node_info.subnode_slots

            enriched_nodes.append(node_dict)

        return {
            "name": workflow.name,
            "id": workflow.id,
            "description": workflow.description,
            "nodes": enriched_nodes,
            "connections": [
                {
                    "source_node": c.source_node,
                    "target_node": c.target_node,
                    "source_output": c.source_output,
                    "target_input": c.target_input,
                    **({"connection_type": c.connection_type} if c.connection_type else {}),
                    **({"slot_name": c.slot_name} if c.slot_name else {}),
                    **({"waypoints": c.waypoints} if c.waypoints else {}),
                }
                for c in workflow.connections
            ],
            "settings": workflow.settings,
        }

    def _build_execution_response(self, context: Any) -> ExecutionResponse:
        """Build execution response from context."""
        node_data = {
            name: [{"json": d.json} for d in data]
            for name, data in context.node_states.items()
        }

        return ExecutionResponse(
            status="failed" if context.errors else "success",
            execution_id=context.execution_id,
            data=node_data,
            errors=[
                ExecutionErrorSchema(
                    node_name=e.node_name,
                    error=e.error,
                    timestamp=e.timestamp.isoformat(),
                )
                for e in context.errors
            ],
        )
