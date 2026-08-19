"""HTTP surface for the workflow SDK — the same core the agent's
``build_workflow`` tool calls, exposed so the studio's Script → Workflow page
(and any human) can drive it directly. One path for humans and agents means
the UI is always exercising exactly what the agent experiences.

Scripts execute in the subprocess sandbox, never in-process.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from ..core.dependencies import get_db_session
from ..engine.workflow_sdk import EXCLUDED_TYPES, signature_reference
from ..engine.workflow_sdk_sandbox import (
    execute_script_isolated,
    workflow_from_payload,
)
from ..services.workflow_sdk_tool_service import _auto_layout

router = APIRouter(prefix="/workflow-sdk")


class SdkExecuteRequest(BaseModel):
    script: str = Field(..., min_length=1)
    name: str | None = Field(None, description="Workflow name when persisting")
    persist: bool = Field(
        False, description="Save the workflow on success (default: dry build only)"
    )


class SdkExecuteResponse(BaseModel):
    ok: bool
    error: str | None = None
    problems: list[str] = Field(default_factory=list)
    workflow: dict[str, Any]
    node_meta: dict[str, Any] = Field(default_factory=dict)
    results: list[Any] = Field(default_factory=list)
    persisted: bool = False
    workflow_id: str | None = None


@router.get("/reference")
async def get_reference() -> dict[str, Any]:
    """The generated SDK reference — the exact text injected into an agent's
    system prompt, so the page shows what the model sees."""
    from ..engine.workflow_sdk import _sdk_types

    return {
        "reference": signature_reference(),
        "types": _sdk_types(),
        "excluded": sorted(EXCLUDED_TYPES),
    }


@router.post("/execute", response_model=SdkExecuteResponse)
async def execute_script(
    request: SdkExecuteRequest, session=Depends(get_db_session)
) -> SdkExecuteResponse:
    """Run a script through the sandbox; optionally persist the result.

    Never 4xxs on a bad script — script errors and validation problems ARE the
    response, exactly as the agent tool receives them.
    """
    workflow_name = (request.name or "").strip() or "sdk_workflow"
    payload = await execute_script_isolated(request.script, workflow_name=workflow_name)

    # Lay out even partial/dry graphs so the canvas always has coordinates.
    workflow = workflow_from_payload(payload)
    _auto_layout(workflow)
    positions = {n.name: n.position for n in workflow.nodes}
    for node in payload["workflow"]["nodes"]:
        node["position"] = positions.get(node["name"])

    persisted = False
    workflow_id: str | None = None
    if payload["ok"] and request.persist:
        from ..repositories.workflow_repository import WorkflowRepository

        stored = await WorkflowRepository(session).create(workflow)
        persisted = True
        workflow_id = stored.id

    return SdkExecuteResponse(
        ok=payload["ok"],
        error=payload["error"],
        problems=payload["problems"],
        workflow=payload["workflow"],
        node_meta=payload["node_meta"],
        results=payload["results"],
        persisted=persisted,
        workflow_id=workflow_id,
    )
