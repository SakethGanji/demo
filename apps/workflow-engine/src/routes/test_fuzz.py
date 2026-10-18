"""Variations / fuzzing (Stage 3B, CONTRACTS-STAGE3B §3). Nothing is saved."""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from ..core.dependencies import get_workflow_service
from ..core.exceptions import ValidationError, WorkflowNotFoundError
from ..engine.test_ai import TestAIError
from ..services.fuzz import DEFAULT_COUNT, MAX_COUNT, fuzz_workflow
from ..services.project_service import ProjectError
from ..services.test_suggest import AuthoringError
from ..services.workflow_service import WorkflowService

router = APIRouter()

WorkflowServiceDep = Annotated[WorkflowService, Depends(get_workflow_service)]


class FuzzRequest(BaseModel):
    definition: dict[str, Any] | None = None
    target: dict[str, Any] | None = None
    count: int = Field(DEFAULT_COUNT, ge=1, le=MAX_COUNT)
    seed: int = 1
    ai: bool = False


def _ai_error(code: str, message: str, status: int) -> HTTPException:
    return HTTPException(status_code=status, detail={"code": code, "message": message})


@router.post("/workflows/{workflow_id}/tests/fuzz")
async def fuzz_route(workflow_id: str, service: WorkflowServiceDep, body: FuzzRequest | None = None) -> dict[str, Any]:
    """Run many odd inputs through the workflow in test mode and report what breaks; never saves anything."""
    body = body or FuzzRequest()
    propose = None
    if body.ai:
        try:
            from ..services.test_ai_service import propose_variations  # type: ignore[attr-defined]
        except ImportError:
            raise _ai_error("ai_not_configured", "AI variations are not available", 503)
        propose = propose_variations
    try:
        return await fuzz_workflow(
            service._workflow_repo, workflow_id, definition=body.definition, target=body.target,
            count=body.count, seed=body.seed, ai=body.ai, propose=propose,
        )
    except WorkflowNotFoundError as e:
        raise HTTPException(status_code=404, detail=e.message)
    except AuthoringError as e:
        raise HTTPException(status_code=e.status, detail=e.message)
    except ProjectError as e:
        raise HTTPException(status_code=e.status, detail=e.detail())
    except ValidationError as e:
        raise HTTPException(status_code=400, detail=e.message)
    except TestAIError as e:
        code = getattr(e, "code", "")
        if code == "not_configured":
            raise _ai_error("ai_not_configured", str(e), 503)
        raise _ai_error("ai_failed", str(e), 502)
