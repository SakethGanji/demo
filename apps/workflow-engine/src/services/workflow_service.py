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
    WorkflowPublishResponse,
)
from ..schemas.execution import ExecutionResponse, ExecutionErrorSchema
from ..schemas.workflow import VersionListItem, VersionDetailResponse

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

    async def list_workflows(self, folder_id: str | None = None) -> list[WorkflowListItem]:
        """List all workflows, optionally filtered by folder."""
        workflows = await self._workflow_repo.list(folder_id=folder_id)
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
        stored = await self._workflow_repo.create(
            internal_workflow, folder_id=getattr(request, "folder_id", None)
        )

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

        updated = await self._workflow_repo.update(
            workflow_id, internal_workflow, folder_id=getattr(request, "folder_id", None)
        )
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

    async def list_versions(self, workflow_id: str) -> list[VersionListItem]:
        """List all versions for a workflow."""
        stored = await self._workflow_repo.get(workflow_id)
        if not stored:
            raise WorkflowNotFoundError(workflow_id)

        from ..repositories.version_repository import VersionRepository
        version_repo = VersionRepository(self._workflow_repo._session)
        versions = await version_repo.list_versions(workflow_id)
        return [
            VersionListItem(
                id=v.id,
                version_number=v.version_number,
                message=v.message,
                created_by=v.created_by,
                created_at=v.created_at.isoformat(),
            )
            for v in versions
        ]

    async def get_version(self, workflow_id: str, version_id: int) -> VersionDetailResponse:
        """Get a specific version with its definition."""
        stored = await self._workflow_repo.get(workflow_id)
        if not stored:
            raise WorkflowNotFoundError(workflow_id)

        from ..repositories.version_repository import VersionRepository
        version_repo = VersionRepository(self._workflow_repo._session)
        version = await version_repo.get(version_id)
        if not version or version.workflow_id != workflow_id:
            raise WorkflowNotFoundError(f"version {version_id}")

        return VersionDetailResponse(
            id=version.id,
            version_number=version.version_number,
            message=version.message,
            created_by=version.created_by,
            created_at=version.created_at.isoformat(),
            definition=version.definition,
        )

    async def publish(self, workflow_id: str, message: str | None = None) -> WorkflowPublishResponse:
        """Publish the current draft as a new immutable version.

        Creates a version snapshot, sets active=true, and syncs triggers.
        Can be called multiple times — each call creates a new version.
        """
        stored = await self._workflow_repo.get(workflow_id)
        if not stored:
            raise WorkflowNotFoundError(workflow_id)

        # Validate before publishing
        errors = self._validate_definition_structure(stored.workflow)
        if errors:
            raise ValidationError(
                f"Cannot publish: {errors[0]}",
                field="definition",
            )

        # Create immutable version from current draft
        await self._create_version(workflow_id, message=message)

        # Sync triggers from the definition
        from ..repositories.trigger_repository import TriggerRepository
        from ..db.models import WorkflowModel
        trigger_repo = TriggerRepository(self._workflow_repo._session)
        db_workflow = await self._workflow_repo._session.get(WorkflowModel, workflow_id)
        if db_workflow:
            await trigger_repo.sync_triggers(
                workflow_id=workflow_id,
                version_id=db_workflow.published_version_id,
                definition=db_workflow.draft_definition,
                team_id=db_workflow.team_id,
            )

        # Set active
        updated = await self._workflow_repo.set_active(workflow_id, True)
        if not updated:
            raise WorkflowNotFoundError(workflow_id)

        return WorkflowPublishResponse(
            id=updated.id,
            active=True,
            version_id=db_workflow.published_version_id,
        )

    async def unpublish(self, workflow_id: str) -> WorkflowPublishResponse:
        """Unpublish a workflow. Disables triggers, sets active=false.

        Published versions are preserved for history — this only stops execution.
        """
        stored = await self._workflow_repo.get(workflow_id)
        if not stored:
            raise WorkflowNotFoundError(workflow_id)

        # Deactivate triggers
        from ..repositories.trigger_repository import TriggerRepository
        trigger_repo = TriggerRepository(self._workflow_repo._session)
        await trigger_repo.deactivate_triggers(workflow_id)

        updated = await self._workflow_repo.set_active(workflow_id, False)
        if not updated:
            raise WorkflowNotFoundError(workflow_id)

        return WorkflowPublishResponse(id=updated.id, active=False, version_id=None)

    async def _create_version(self, workflow_id: str, message: str | None = None) -> None:
        """Create an immutable version snapshot from the current draft."""
        from ..repositories.version_repository import VersionRepository
        from ..db.models import WorkflowModel

        # Read draft_definition directly from the model
        db_workflow = await self._workflow_repo._session.get(WorkflowModel, workflow_id)
        if not db_workflow:
            raise WorkflowNotFoundError(workflow_id)

        version_repo = VersionRepository(self._workflow_repo._session)
        version = await version_repo.create_version(
            workflow_id=workflow_id,
            definition=db_workflow.draft_definition,
            message=message,
        )

        # Update published_version_id
        db_workflow.published_version_id = version.id
        await self._workflow_repo._session.commit()

    async def run_workflow(
        self, workflow_id: str, input_data: dict[str, Any] | None = None
    ) -> ExecutionResponse:
        """Run a saved workflow with optional input data.
        Uses published version if available, falls back to draft."""
        stored = await self._workflow_repo.get(workflow_id)
        if not stored:
            raise WorkflowNotFoundError(workflow_id)

        from ..db.session import async_session_factory
        runner = WorkflowRunner(db_session_factory=async_session_factory)
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

        # Pre-create execution row so node_outputs FK is satisfied during run
        from ..utils.ids import execution_id as gen_exec_id
        exec_id = gen_exec_id()
        await self._execution_repo.start(exec_id, stored.id, stored.name, mode)

        context = await runner.run(
            stored.workflow,
            start_node.name,
            initial_data,
            mode,
            workflow_repository=self._workflow_repo,
            execution_id=exec_id,
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

        # Pre-create execution row so node_outputs FK is satisfied during run
        from ..utils.ids import execution_id as gen_exec_id
        exec_id = gen_exec_id()
        wf_id = internal_workflow.id or "adhoc"
        await self._execution_repo.start(exec_id, wf_id, internal_workflow.name, mode)

        context = await runner.run(
            internal_workflow,
            start_node.name,
            initial_data,
            mode,
            workflow_repository=self._workflow_repo,
            execution_id=exec_id,
        )
        await self._execution_repo.complete(context, wf_id, internal_workflow.name)

        return self._build_execution_response(context)

    def _validate_definition_structure(self, workflow: Workflow) -> list[str]:
        """Validate workflow graph structure. Returns list of errors (empty = valid)."""
        errors = []
        node_names = {n.name for n in workflow.nodes}

        # All connection endpoints reference existing nodes
        for conn in workflow.connections:
            if conn.source_node not in node_names:
                errors.append(f"Connection references missing source node: {conn.source_node}")
            if conn.target_node not in node_names:
                errors.append(f"Connection references missing target node: {conn.target_node}")

        # At least one trigger node
        trigger_types = {"Start", "Webhook", "Cron", "ExecuteWorkflowTrigger", "ErrorTrigger"}
        triggers = [n for n in workflow.nodes if n.type in trigger_types]
        if len(triggers) == 0:
            errors.append("Workflow has no trigger node")

        # Orphan nodes (nodes with no connections, excluding triggers and sticky notes)
        connected_nodes: set[str] = set()
        for conn in workflow.connections:
            connected_nodes.add(conn.source_node)
            connected_nodes.add(conn.target_node)
        skip_types = trigger_types | {"StickyNote"}
        for node in workflow.nodes:
            if node.name not in connected_nodes and node.type not in skip_types:
                # Check if it's a subnode (connected via subnode connections)
                is_subnode = any(
                    c.target_node == node.name and c.connection_type == "subnode"
                    for c in workflow.connections
                )
                if not is_subnode:
                    errors.append(f"Orphan node: {node.name}")

        return errors

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
