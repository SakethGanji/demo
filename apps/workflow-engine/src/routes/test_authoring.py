"""Suggest tests, save a run as a test, and the node catalog (Stage 3A, CONTRACTS-STAGE3A §7). Nothing is saved."""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from ..core.dependencies import get_workflow_service
from ..core.exceptions import WorkflowNotFoundError
from ..services.node_catalog import build_node_catalog
from ..services.test_suggest import AuthoringError, suggest_tests, replay_from_execution
from ..services.workflow_service import WorkflowService

router = APIRouter()

WorkflowServiceDep = Annotated[WorkflowService, Depends(get_workflow_service)]


class SuggestRequest(BaseModel):
    node: str | None = None


class FromExecutionRequest(BaseModel):
    execution_id: str
    name: str | None = None


def _http(e: Exception) -> HTTPException:
    if isinstance(e, WorkflowNotFoundError):
        return HTTPException(status_code=404, detail=e.message)
    assert isinstance(e, AuthoringError)
    return HTTPException(status_code=e.status, detail=e.message)


@router.post("/workflows/{workflow_id}/tests/suggest")
async def suggest_route(workflow_id: str, service: WorkflowServiceDep, body: SuggestRequest | None = None) -> list[dict[str, Any]]:
    """Draft tests from the node recipes and conditions; not saved. Drafts the workflow already has are left out."""
    try:
        return await suggest_tests(service._workflow_repo, workflow_id, body.node if body else None)
    except (WorkflowNotFoundError, AuthoringError) as e:
        raise _http(e)


@router.post("/workflows/{workflow_id}/tests/from-execution")
async def from_execution_route(workflow_id: str, body: FromExecutionRequest, service: WorkflowServiceDep) -> dict[str, Any]:
    """One ``replay`` draft built from a past run of this workflow; not saved."""
    try:
        return await replay_from_execution(service._workflow_repo, service._execution_repo, workflow_id, body.execution_id, body.name)
    except (WorkflowNotFoundError, AuthoringError) as e:
        raise _http(e)


@router.get("/nodes/catalog")
async def node_catalog_route() -> list[dict[str, Any]]:
    """Every node type with its parameters, effect, contract kind, test recipe and an example node."""
    return build_node_catalog()
