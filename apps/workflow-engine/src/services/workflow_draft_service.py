"""Persistence helpers for the node-ladder :class:`WorkflowDraft`.

The ladder tools in ``src/nodes/ai/tools/node_ladder.py`` are deliberately
pure: they mutate an in-memory draft and never touch the database, so an agent
run can be watched, interrupted and thrown away without leaving rows behind.
This module is the (optional) bridge to storage:

* :func:`draft_from_definition` / :func:`draft_from_stored` — resume editing an
  existing workflow with the ladder instead of starting from nothing.
* :func:`save_draft` — write the draft to the ``workflows`` table as a draft
  definition (create or update). Publishing stays a separate, human decision.

Nothing here is imported by the tools themselves; keeping storage on this side
of the seam is what lets ``test_run`` honestly promise it wrote nothing.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from ..nodes.ai.tools.node_ladder import DraftConnection, DraftNode, WorkflowDraft

if TYPE_CHECKING:  # pragma: no cover - typing only
    from sqlalchemy.ext.asyncio import AsyncSession

    from ..engine.types import StoredWorkflow


def draft_from_definition(
    definition: dict[str, Any],
    *,
    name: str | None = None,
    description: str = "",
    workflow_id: str | None = None,
) -> WorkflowDraft:
    """Build a :class:`WorkflowDraft` from a stored workflow definition dict."""
    return WorkflowDraft(
        name=name or definition.get("name") or "Untitled workflow",
        description=description or definition.get("description") or "",
        workflow_id=workflow_id or definition.get("id"),
        nodes=[
            DraftNode(
                name=n["name"],
                type=n["type"],
                parameters=dict(n.get("parameters") or {}),
                position=n.get("position"),
            )
            for n in definition.get("nodes", [])
        ],
        connections=[
            DraftConnection(
                source_node=c["source_node"],
                target_node=c["target_node"],
                source_output=c.get("source_output", "main"),
                target_input=c.get("target_input", "main"),
            )
            for c in definition.get("connections", [])
        ],
    )


def draft_from_stored(stored: StoredWorkflow) -> WorkflowDraft:
    """Build a draft from a :class:`~src.engine.types.StoredWorkflow`."""
    wf = stored.workflow
    return WorkflowDraft(
        name=stored.name,
        description=wf.description or "",
        workflow_id=stored.id,
        nodes=[
            DraftNode(
                name=n.name,
                type=n.type,
                parameters=dict(n.parameters or {}),
                position=n.position,
            )
            for n in wf.nodes
        ],
        connections=[
            DraftConnection(
                source_node=c.source_node,
                target_node=c.target_node,
                source_output=c.source_output,
                target_input=c.target_input,
            )
            for c in wf.connections
        ],
    )


async def save_draft(
    draft: WorkflowDraft,
    session: AsyncSession,
    *,
    folder_id: str | None = None,
) -> dict[str, Any]:
    """Persist the draft as a workflow draft definition (create or update).

    Returns ``{"workflow_id", "created", "name", "node_count"}``. The workflow
    is left inactive/unpublished — turning an agent's draft into a live,
    trigger-registered workflow stays a separate, human-approved step.
    """
    from ..repositories.workflow_repository import WorkflowRepository

    repo = WorkflowRepository(session)
    workflow = draft.to_workflow()

    created = True
    stored = None
    if draft.workflow_id:
        stored = await repo.update(draft.workflow_id, workflow, folder_id=folder_id)
        created = stored is None
    if stored is None:
        stored = await repo.create(workflow, folder_id=folder_id)

    draft.workflow_id = stored.id
    return {
        "workflow_id": stored.id,
        "created": created,
        "name": stored.name,
        "node_count": len(draft.nodes),
        "active": stored.active,
    }


__all__ = ["draft_from_definition", "draft_from_stored", "save_draft"]
