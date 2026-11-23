"""AI drafts tests for grid gaps (Stage 3B, CONTRACTS-STAGE3B §2). Nothing is saved."""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from ..core.dependencies import get_workflow_service
from ..core.exceptions import ValidationError, WorkflowNotFoundError
from ..engine.test_ai import TestAIError
from ..services.test_ai_service import draft_tests
from ..services.test_suggest import AuthoringError
from ..services.workflow_service import WorkflowService

router = APIRouter()

WorkflowServiceDep = Annotated[WorkflowService, Depends(get_workflow_service)]


class AIDraftRequest(BaseModel):
    definition: dict[str, Any] | None = None
    cells: list[dict[str, str]] | None = None
    max: int = Field(8, ge=1, le=20)


def ai_http_error(e: TestAIError) -> HTTPException:
    """``not_configured`` -> 503 ``ai_not_configured``; anything else (bad JSON, provider failure) -> 502 ``ai_failed``."""
    if e.code == "not_configured":
        return HTTPException(status_code=503, detail={"code": "ai_not_configured", "message": "AI isn't set up on this engine"})
    return HTTPException(status_code=502, detail={"code": "ai_failed", "message": e.message})


@router.post("/workflows/{workflow_id}/tests/ai-draft")
async def ai_draft_route(workflow_id: str, service: WorkflowServiceDep, body: AIDraftRequest | None = None) -> dict[str, Any]:
    """Draft tests for grid gaps (or the given cells) with the model. Drafts are sanitised, dry-run and returned; nothing is saved."""
    body = body or AIDraftRequest()
    try:
        return await draft_tests(service._workflow_repo, workflow_id, body.definition, body.cells, body.max)
    except WorkflowNotFoundError as e:
        raise HTTPException(status_code=404, detail=e.message)
    except AuthoringError as e:
        raise HTTPException(status_code=e.status, detail=e.message)
    except ValidationError as e:
        raise HTTPException(status_code=400, detail=e.message)
    except TestAIError as e:
        raise ai_http_error(e)
