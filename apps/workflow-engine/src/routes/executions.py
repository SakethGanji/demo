"""Execution routes."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query

from ..core.exceptions import ExecutionNotFoundError
from ..core.dependencies import get_execution_service
from ..services.execution_service import ExecutionService
from ..schemas.execution import ExecutionListItem, ExecutionDetailResponse
from ..schemas.common import SuccessResponse

router = APIRouter(prefix="/executions")


# Type alias for dependency injection
ExecutionServiceDep = Annotated[ExecutionService, Depends(get_execution_service)]


@router.get("", response_model=list[ExecutionListItem])
async def list_executions(
    service: ExecutionServiceDep,
    workflow_id: str | None = Query(None, description="Filter by workflow ID"),
) -> list[ExecutionListItem]:
    """List execution history."""
    return await service.list_executions(workflow_id)


@router.get("/{execution_id}", response_model=ExecutionDetailResponse)
async def get_execution(
    execution_id: str,
    service: ExecutionServiceDep,
) -> ExecutionDetailResponse:
    """Get execution details."""
    try:
        return await service.get_execution(execution_id)
    except ExecutionNotFoundError as e:
        raise HTTPException(status_code=404, detail=e.message)


@router.delete("/{execution_id}", response_model=SuccessResponse)
async def delete_execution(
    execution_id: str,
    service: ExecutionServiceDep,
) -> SuccessResponse:
    """Delete an execution record."""
    try:
        await service.delete_execution(execution_id)
        return SuccessResponse(message="Execution deleted")
    except ExecutionNotFoundError as e:
        raise HTTPException(status_code=404, detail=e.message)


@router.post("/{execution_id}/cancel", response_model=SuccessResponse)
async def cancel_execution(
    execution_id: str,
    service: ExecutionServiceDep,
) -> SuccessResponse:
    """Cancel a running execution."""
    try:
        cancelled = await service.cancel_execution(execution_id)
        if not cancelled:
            raise HTTPException(status_code=400, detail="Execution is not running or not found")
        return SuccessResponse(message="Execution cancelled")
    except ExecutionNotFoundError as e:
        raise HTTPException(status_code=404, detail=e.message)


@router.post("/{execution_id}/retry", response_model=SuccessResponse)
async def retry_execution(
    execution_id: str,
    service: ExecutionServiceDep,
) -> SuccessResponse:
    """Retry a failed execution from the point of failure."""
    try:
        new_execution_id = await service.retry_from_failure(execution_id)
        return SuccessResponse(message=f"Retry started: {new_execution_id}")
    except ExecutionNotFoundError as e:
        raise HTTPException(status_code=404, detail=e.message)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.delete("", response_model=SuccessResponse)
async def clear_executions(service: ExecutionServiceDep) -> SuccessResponse:
    """Clear all execution records."""
    count = await service.clear_executions()
    return SuccessResponse(message=f"Cleared {count} execution records")
