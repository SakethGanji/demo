"""Schemas for App Builder AI endpoints."""

from __future__ import annotations

from pydantic import BaseModel


class AppBuilderChatRequest(BaseModel):
    """Request body for POST /api/ai/app-builder/chat."""

    message: str
    app_id: str | None = None
    current_version_id: int | None = None
    api_execution_ids: list[str] = []  # Captured API test executions to attach as context
    conversation_history: list[dict[str, str]] = []  # [{role: "user", content: "..."}, {role: "assistant", content: "..."}]
