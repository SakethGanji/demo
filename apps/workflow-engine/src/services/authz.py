"""Authorization seam (Stage 8, DESIGN-STAGE8-REUSE section 13). A no-op today.

There is no login, no role and no audit log in this stage. Every new route that creates or reads reuse data calls
``authorize(request, action, resource)`` before it does anything, so that when identity exists the check lands in
exactly one place and nothing else has to be reshaped. The answer is always True.

Actions used so far:

    subworkflow.call      who may depend on a child (checked when a call is saved)
    workflow.extract      convert a selection to a sub-workflow
    template.install      install a template into a project
    template.export       export a workflow as a template bundle
    workflow.delete.force delete a workflow that is still in use
    knowledge.read        read the capability graph (graph, nodes, matrix, memory, live understanding)
    knowledge.write       rebuild the graph, accept or reject a proposed concept
"""

from __future__ import annotations

from typing import Any

ACTIONS = (
    "subworkflow.call",
    "workflow.extract",
    "template.install",
    "template.export",
    "workflow.delete.force",
    "knowledge.read",
    "knowledge.write",
)


def authorize(request: Any, action: str, resource: Any = None) -> bool:
    """Is the caller of ``request`` allowed to do ``action`` on ``resource``? Always True until login exists.

    ``request`` is the FastAPI request (or None for a service-to-service call); ``resource`` is whatever the action is
    about (a dict such as ``{"project_id": ..., "slug": ...}``, a model or an id)."""
    return True


def actor_of(request: Any) -> str | None:
    """The optional ``X-User-Id`` of the request (recorded in ``installed_by``; never used to decide anything)."""
    headers = getattr(request, "headers", None)
    if headers is None:
        return None
    value = (headers.get("x-user-id") or "").strip()
    return value[:255] or None
