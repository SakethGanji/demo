"""Schemas for App Builder AI endpoints."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel


class AppBuilderChatRequest(BaseModel):
    """Request body for POST /api/ai/app-builder/chat."""

    message: str
    app_id: str | None = None
    current_version_id: int | None = None
    workflow_ids: list[str] = []
    conversation_history: list[dict[str, str]] = []  # [{role: "user", content: "..."}, {role: "assistant", content: "..."}]


class NodeSchema(BaseModel):
    """Schema inferred from a single node's execution output."""

    node_name: str
    node_type: str
    parameters: dict[str, Any] = {}
    output_schema: dict[str, Any]
    sample_data: Any = None
    field_catalog: list[dict[str, Any]] | None = None  # from schema analyzer


class WorkflowSchemaResponse(BaseModel):
    """Schema extracted from a workflow + its latest execution."""

    workflow_id: str
    workflow_name: str
    input_schema: dict[str, Any]
    node_schemas: list[NodeSchema]
    webhook_path: str | None = None
    webhook_response_mode: str | None = None
    webhook_body_schema: dict[str, Any] | None = None
    webhook_body_sample: Any = None
