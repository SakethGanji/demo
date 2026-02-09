"""Workflow repository for database persistence."""

from __future__ import annotations

import time
import uuid
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import select

from ..db.models import WorkflowModel

if TYPE_CHECKING:
    from ..engine.types import StoredWorkflow, Workflow


class WorkflowRepository:
    """Repository for workflow persistence."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def create(self, workflow: Workflow) -> StoredWorkflow:
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
                    **({"connection_type": c.connection_type} if c.connection_type and c.connection_type != "normal" else {}),
                    **({"slot_name": c.slot_name} if c.slot_name else {}),
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
            active=False,
            definition=definition,
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

    async def list(self) -> list[StoredWorkflow]:
        """List all workflows."""
        statement = select(WorkflowModel).order_by(WorkflowModel.updated_at.desc())
        result = await self._session.execute(statement)
        workflows = result.scalars().all()
        return [self._to_stored_workflow(w) for w in workflows]

    async def update(self, workflow_id: str, workflow: Workflow) -> StoredWorkflow | None:
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
                    **({"connection_type": c.connection_type} if c.connection_type and c.connection_type != "normal" else {}),
                    **({"slot_name": c.slot_name} if c.slot_name else {}),
                    **({"waypoints": c.waypoints} if c.waypoints else {}),
                }
                for c in workflow.connections
            ],
            "settings": workflow.settings or db_workflow.definition.get("settings", {}),
        }
        db_workflow.definition = definition
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
        """Delete a workflow."""
        db_workflow = await self._session.get(WorkflowModel, workflow_id)
        if not db_workflow:
            return False

        await self._session.delete(db_workflow)
        await self._session.commit()
        return True

    def _generate_id(self) -> str:
        """Generate a unique workflow ID."""
        return f"wf_{int(time.time() * 1000)}_{uuid.uuid4().hex[:7]}"

    def _to_stored_workflow(self, db_workflow: WorkflowModel) -> StoredWorkflow:
        """Convert database model to StoredWorkflow."""
        from ..engine.types import (
            Connection,
            NodeData,
            NodeDefinition,
            StoredWorkflow,
            Workflow as WorkflowType,
        )

        definition = db_workflow.definition

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
                connection_type=c.get("connection_type", "normal"),
                slot_name=c.get("slot_name"),
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
