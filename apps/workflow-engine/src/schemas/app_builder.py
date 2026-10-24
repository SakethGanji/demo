"""Schemas for App Builder AI endpoints."""

from __future__ import annotations

from pydantic import BaseModel


class ChatSelection(BaseModel):
    """The element the user selected in the live preview: where it is and its JSX source."""

    file: str
    line: int
    name: str = ""
    snippet: str = ""


class AppBuilderChatRequest(BaseModel):
    """Request body for POST /api/ai/app-builder/chat."""

    message: str
    app_id: str | None = None
    current_version_id: int | None = None
    # Optimistic concurrency: the version the client's working copy is based on; a stale base is refused with 409
    base_version_id: int | None = None
    # Short summaries of hand edits made in the visual editor since the last AI turn (kept by the AI unless asked otherwise)
    visual_edits: list[str] = []
    # The element selected in the preview when the message was sent (optional context)
    selection: ChatSelection | None = None
    api_execution_ids: list[str] = []  # Captured API test executions to attach as context
    project_id: str | None = None  # phase 6: project whose API spec the app builds against
    plan_app: str | None = None  # key of the approved plan's app this chat builds (else matched by app id / name)
    route_ids: list[str] = []  # phase 6: focus routes ("generate UI for this API")
    conversation_history: list[dict[str, str]] = []  # [{role: "user", content: "..."}, {role: "assistant", content: "..."}]
