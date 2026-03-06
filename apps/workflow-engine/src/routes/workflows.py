"""Workflow routes."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException

from ..core.exceptions import (
    WorkflowNotFoundError,
    WorkflowExecutionError,
    ValidationError,
)
from ..core.dependencies import get_workflow_service
from ..services.workflow_service import WorkflowService
from ..schemas.workflow import (
    WorkflowCreateRequest,
    WorkflowUpdateRequest,
    WorkflowResponse,
    WorkflowListItem,
    WorkflowDetailResponse,
    PublishRequest,
    WorkflowPublishResponse,
    VersionListItem,
    VersionDetailResponse,
)
from ..schemas.execution import ExecutionResponse, RunWorkflowRequest
from ..schemas.common import SuccessResponse

router = APIRouter(prefix="/workflows")


# Type alias for dependency injection
WorkflowServiceDep = Annotated[WorkflowService, Depends(get_workflow_service)]


@router.get("", response_model=list[WorkflowListItem])
async def list_workflows(
    service: WorkflowServiceDep,
    folder_id: str | None = None,
) -> list[WorkflowListItem]:
    """List all workflows, optionally filtered by folder."""
    return await service.list_workflows(folder_id=folder_id)


@router.get("/{workflow_id}", response_model=WorkflowDetailResponse)
async def get_workflow(
    workflow_id: str,
    service: WorkflowServiceDep,
) -> WorkflowDetailResponse:
    """Get a single workflow by ID."""
    try:
        return await service.get_workflow(workflow_id)
    except WorkflowNotFoundError as e:
        raise HTTPException(status_code=404, detail=e.message)


@router.post("", response_model=WorkflowResponse, status_code=201)
async def create_workflow(
    workflow: WorkflowCreateRequest,
    service: WorkflowServiceDep,
) -> WorkflowResponse:
    """Create a new workflow."""
    try:
        return await service.create_workflow(workflow)
    except ValidationError as e:
        raise HTTPException(status_code=400, detail=e.message)


@router.put("/{workflow_id}", response_model=WorkflowDetailResponse)
async def update_workflow(
    workflow_id: str,
    workflow: WorkflowUpdateRequest,
    service: WorkflowServiceDep,
) -> WorkflowDetailResponse:
    """Update an existing workflow."""
    try:
        return await service.update_workflow(workflow_id, workflow)
    except WorkflowNotFoundError as e:
        raise HTTPException(status_code=404, detail=e.message)
    except ValidationError as e:
        raise HTTPException(status_code=400, detail=e.message)


@router.delete("/{workflow_id}", response_model=SuccessResponse)
async def delete_workflow(
    workflow_id: str,
    service: WorkflowServiceDep,
) -> SuccessResponse:
    """Delete a workflow."""
    try:
        await service.delete_workflow(workflow_id)
        return SuccessResponse(message="Workflow deleted")
    except WorkflowNotFoundError as e:
        raise HTTPException(status_code=404, detail=e.message)


@router.post("/{workflow_id}/publish", response_model=WorkflowPublishResponse)
async def publish_workflow(
    workflow_id: str,
    body: PublishRequest | None = None,
    service: WorkflowServiceDep = None,
) -> WorkflowPublishResponse:
    """Publish the current draft as a new immutable version. Enables triggers."""
    try:
        message = body.message if body else None
        return await service.publish(workflow_id, message=message)
    except WorkflowNotFoundError as e:
        raise HTTPException(status_code=404, detail=e.message)
    except ValidationError as e:
        raise HTTPException(status_code=400, detail=e.message)


@router.post("/{workflow_id}/unpublish", response_model=WorkflowPublishResponse)
async def unpublish_workflow(
    workflow_id: str,
    service: WorkflowServiceDep = None,
) -> WorkflowPublishResponse:
    """Unpublish a workflow. Disables triggers, preserves version history."""
    try:
        return await service.unpublish(workflow_id)
    except WorkflowNotFoundError as e:
        raise HTTPException(status_code=404, detail=e.message)


@router.get("/{workflow_id}/versions", response_model=list[VersionListItem])
async def list_versions(
    workflow_id: str,
    service: WorkflowServiceDep,
) -> list[VersionListItem]:
    """List all versions for a workflow."""
    try:
        return await service.list_versions(workflow_id)
    except WorkflowNotFoundError as e:
        raise HTTPException(status_code=404, detail=e.message)


@router.get("/{workflow_id}/versions/{version_id}", response_model=VersionDetailResponse)
async def get_version(
    workflow_id: str,
    version_id: int,
    service: WorkflowServiceDep,
) -> VersionDetailResponse:
    """Get a specific version with its definition."""
    try:
        return await service.get_version(workflow_id, version_id)
    except WorkflowNotFoundError as e:
        raise HTTPException(status_code=404, detail=e.message)


@router.post("/{workflow_id}/run", response_model=ExecutionResponse)
async def run_workflow(
    workflow_id: str,
    service: WorkflowServiceDep,
    body: RunWorkflowRequest | None = None,
) -> ExecutionResponse:
    """Run a saved workflow with optional input data."""
    try:
        input_data = body.input_data if body else None
        return await service.run_workflow(workflow_id, input_data)
    except WorkflowNotFoundError as e:
        raise HTTPException(status_code=404, detail=e.message)
    except WorkflowExecutionError as e:
        raise HTTPException(status_code=400, detail=e.message)


@router.post("/run-adhoc", response_model=ExecutionResponse)
async def run_adhoc_workflow(
    workflow: WorkflowCreateRequest,
    service: WorkflowServiceDep,
) -> ExecutionResponse:
    """Run an ad-hoc workflow without saving."""
    try:
        return await service.run_adhoc_workflow(workflow)
    except ValidationError as e:
        raise HTTPException(status_code=400, detail=e.message)
    except WorkflowExecutionError as e:
        raise HTTPException(status_code=400, detail=e.message)
