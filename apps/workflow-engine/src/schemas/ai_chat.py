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
    """Request body for POST /api/ai/chat."""

    message: str
    session_id: str | None = None
    workflow_context: WorkflowContext | None = None
    conversation_history: list[AIChatMessage] = []
    mode_hint: Literal["auto", "generate", "modify", "explain", "fix"] = "auto"
    test_input: dict[str, Any] | None = None
