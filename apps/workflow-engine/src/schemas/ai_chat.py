"""Schemas for AI chat endpoint."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel


class AIChatMessage(BaseModel):
    """A single message in the conversation history."""

    role: Literal["user", "assistant"]
    content: str


class WorkflowContext(BaseModel):
    """Current workflow state sent from the frontend."""

    name: str
    nodes: list[dict[str, Any]]
    connections: list[dict[str, Any]]


class AIChatRequest(BaseModel):
    """Request body for POST /api/ai/chat (DESIGN-STAGE6 sections 4.13 and 8).

    ``workflow_id`` names the saved workflow that is open (a change is proposed against its stored draft, so the studio saves pending
    edits first); ``project_id`` is needed to build a new workflow and optional when ``workflow_id`` is given. ``actor`` (or the
    ``X-Actor-Name`` header) says who is asking; it is needed whenever a proposal is made. ``workflow_context`` is the editor's
    snapshot and is kept for older clients: the server reads the stored draft, never this. ``test_input`` is ignored.
    """

    message: str
    session_id: str | None = None
    project_id: str | None = None
    workflow_id: str | None = None
    selected_node: str | None = None
    actor: str | None = None
    workflow_context: WorkflowContext | None = None
    conversation_history: list[AIChatMessage] = []
    mode_hint: Literal["auto", "generate", "modify", "explain", "fix"] = "auto"
    test_input: dict[str, Any] | None = None
