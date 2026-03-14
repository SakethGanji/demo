"""Workflow repository for database persistence."""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import select

from ..db.models import (
    WorkflowModel,
    WorkflowVersionModel,
    WorkflowTagModel,
    ActiveTriggerModel,
    ExecutionModel,
    NodeOutputModel,
)

if TYPE_CHECKING:
    from ..engine.types import StoredWorkflow, Workflow


class WorkflowRepository:
    """Repository for workflow persistence."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def create(self, workflow: Workflow, folder_id: str | None = None) -> StoredWorkflow:
        """Create a new workflow."""
        from ..engine.types import StoredWorkflow, Workflow as WorkflowType

        workflow_id = workflow.id or self._generate_id()
        now = datetime.now()

        # Build definition dict from workflow
        definition = {
            "nodes": [
                {
                    "name": n.name,
                    "type": n.type,
                    "parameters": n.parameters,
                    "position": n.position,
                    **({"label": n.label} if n.label else {}),
                    **({"pinned_data": [{"json": d.json} for d in n.pinned_data]} if n.pinned_data else {}),
                    "retry_on_fail": n.retry_on_fail,
                    "retry_delay": n.retry_delay,
                    "continue_on_fail": n.continue_on_fail,
                }
                for n in workflow.nodes
            ],
            "connections": [
                {
                    "source_node": c.source_node,
                    "target_node": c.target_node,
                    "source_output": c.source_output,
                    "target_input": c.target_input,
                    **({"waypoints": c.waypoints} if c.waypoints else {}),
                }
                for c in workflow.connections
            ],
            "settings": workflow.settings,
        }

        db_workflow = WorkflowModel(
            id=workflow_id,
            name=workflow.name,
            description=workflow.description,
            folder_id=folder_id,
            active=False,
            draft_definition=definition,
            created_at=now,
            updated_at=now,
        )

        self._session.add(db_workflow)
        await self._session.commit()
        await self._session.refresh(db_workflow)

        return self._to_stored_workflow(db_workflow)

    async def get(self, workflow_id: str) -> StoredWorkflow | None:
        """Get a workflow by ID."""
        result = await self._session.get(WorkflowModel, workflow_id)
        if not result:
            return None
        return self._to_stored_workflow(result)

    async def list(self, folder_id: str | None = None) -> list[StoredWorkflow]:
        """List all workflows, optionally filtered by folder."""
        statement = select(WorkflowModel)
        if folder_id is not None:
            statement = statement.where(WorkflowModel.folder_id == folder_id)
        statement = statement.order_by(WorkflowModel.updated_at.desc())
        result = await self._session.execute(statement)
        workflows = result.scalars().all()
        return [self._to_stored_workflow(w) for w in workflows]

    async def update(self, workflow_id: str, workflow: Workflow, folder_id: str | None = None) -> StoredWorkflow | None:
        """Update an existing workflow."""
        db_workflow = await self._session.get(WorkflowModel, workflow_id)
        if not db_workflow:
            return None

        # Update fields
        if workflow.name:
            db_workflow.name = workflow.name
        if workflow.description is not None:
            db_workflow.description = workflow.description

        # Build new definition
        definition = {
            "nodes": [
                {
                    "name": n.name,
                    "type": n.type,
                    "parameters": n.parameters,
                    "position": n.position,
                    **({"label": n.label} if n.label else {}),
                    **({"pinned_data": [{"json": d.json} for d in n.pinned_data]} if n.pinned_data else {}),
                    "retry_on_fail": n.retry_on_fail,
                    "retry_delay": n.retry_delay,
                    "continue_on_fail": n.continue_on_fail,
                }
                for n in workflow.nodes
            ],
            "connections": [
                {
                    "source_node": c.source_node,
                    "target_node": c.target_node,
                    "source_output": c.source_output,
                    "target_input": c.target_input,
                    **({"waypoints": c.waypoints} if c.waypoints else {}),
                }
                for c in workflow.connections
            ],
            "settings": workflow.settings or db_workflow.draft_definition.get("settings", {}),
        }
        db_workflow.draft_definition = definition
        if folder_id is not None:
            db_workflow.folder_id = folder_id
        db_workflow.updated_at = datetime.now()

        await self._session.commit()
        await self._session.refresh(db_workflow)

        return self._to_stored_workflow(db_workflow)

    async def set_active(self, workflow_id: str, active: bool) -> StoredWorkflow | None:
        """Set workflow active state."""
        db_workflow = await self._session.get(WorkflowModel, workflow_id)
        if not db_workflow:
            return None

        db_workflow.active = active
        db_workflow.updated_at = datetime.now()

        await self._session.commit()
        await self._session.refresh(db_workflow)

        return self._to_stored_workflow(db_workflow)

    async def delete(self, workflow_id: str) -> bool:
        """Delete a workflow and all dependent records."""
        from sqlalchemy import delete as sa_delete

        db_workflow = await self._session.get(WorkflowModel, workflow_id)
        if not db_workflow:
            return False

        # node_outputs reference executions (indirect), delete first
        exec_ids_result = await self._session.execute(
            select(ExecutionModel.id).where(ExecutionModel.workflow_id == workflow_id)
        )
        exec_ids = exec_ids_result.scalars().all()
        if exec_ids:
            await self._session.execute(
                sa_delete(NodeOutputModel).where(NodeOutputModel.execution_id.in_(exec_ids))
            )

        # Delete direct dependents
        await self._session.execute(
            sa_delete(ExecutionModel).where(ExecutionModel.workflow_id == workflow_id)
        )
        await self._session.execute(
            sa_delete(ActiveTriggerModel).where(ActiveTriggerModel.workflow_id == workflow_id)
        )
        await self._session.execute(
            sa_delete(WorkflowVersionModel).where(WorkflowVersionModel.workflow_id == workflow_id)
        )
        await self._session.execute(
            sa_delete(WorkflowTagModel).where(WorkflowTagModel.workflow_id == workflow_id)
        )

        await self._session.delete(db_workflow)
        await self._session.commit()
        return True

    async def find_by_webhook_path(self, path: str) -> StoredWorkflow | None:
        """Find an active workflow by its webhook node's custom path."""
        statement = select(WorkflowModel).where(WorkflowModel.active == True)
        result = await self._session.execute(statement)
        workflows = result.scalars().all()

        for w in workflows:
            for node in w.draft_definition.get("nodes", []):
                if node["type"] == "Webhook":
                    node_path = node.get("parameters", {}).get("path", "")
                    if node_path and node_path == path:
                        return self._to_stored_workflow(w)
        return None

    def _generate_id(self) -> str:
        """Generate a unique workflow ID."""
        from ..utils.ids import workflow_id
        return workflow_id()

    def _to_stored_workflow(self, db_workflow: WorkflowModel) -> StoredWorkflow:
        """Convert database model to StoredWorkflow."""
        from ..engine.types import (
            Connection,
            NodeData,
            NodeDefinition,
            StoredWorkflow,
            Workflow as WorkflowType,
        )

        definition = db_workflow.draft_definition

        # Reconstruct nodes
        nodes = [
            NodeDefinition(
                name=n["name"],
                type=n["type"],
                parameters=n.get("parameters", {}),
                position=n.get("position"),
                label=n.get("label"),
                pinned_data=[NodeData(json=d["json"]) for d in n["pinned_data"]] if n.get("pinned_data") else None,
                retry_on_fail=n.get("retry_on_fail", 0),
                retry_delay=n.get("retry_delay", 1000),
                continue_on_fail=n.get("continue_on_fail", False),
            )
            for n in definition.get("nodes", [])
        ]

        # Reconstruct connections
        connections = [
            Connection(
                source_node=c["source_node"],
                target_node=c["target_node"],
                source_output=c.get("source_output", "main"),
                target_input=c.get("target_input", "main"),
                waypoints=c.get("waypoints"),
            )
            for c in definition.get("connections", [])
        ]

        workflow = WorkflowType(
            name=db_workflow.name,
            nodes=nodes,
            connections=connections,
            id=db_workflow.id,
            description=db_workflow.description,
            settings=definition.get("settings", {}),
        )

        return StoredWorkflow(
            id=db_workflow.id,
            name=db_workflow.name,
            workflow=workflow,
            active=db_workflow.active,
            created_at=db_workflow.created_at,
            updated_at=db_workflow.updated_at,
        )
