"""Workflow routes."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException

from ..core.exceptions import (
    ConflictError,
    ExecutionNotFoundError,
    MissingNodeDataError,
    NodeNotFoundInWorkflowError,
    WorkflowNotFoundError,
    WorkflowExecutionError,
    ValidationError,
    WorkflowHasErrorsError,
    ApprovedTestsFailError,
)
from ..core.dependencies import get_workflow_service
from ..services.project_service import ProjectError
from ..services.workflow_service import WorkflowService
from ..services.workflow_toolkit import OpError
from ..schemas.workflow import (
    WorkflowCreateRequest,
    WorkflowUpdateRequest,
    WorkflowResponse,
    WorkflowListItem,
    WorkflowDetailResponse,
    PublishRequest,
    CheckRequest,
    WorkflowPublishResponse,
    WorkflowPublishResult,
    VersionListItem,
    VersionDetailResponse,
    ContractLearnRequest,
    ContractLearnResponse,
    ContractApproveRequest,
    TestApproveRequest,
    TestUnlockRequest,
    TestRunRequest,
    ScoreRequest,
    ApplyOpsRequest,
)
from ..engine.test_mode import LockedTestsError, TestNotFoundError
from ..schemas.execution import ExecutionResponse, NodeTestRequest, RunFromRequest, RunWorkflowRequest
from ..schemas.common import SuccessResponse

router = APIRouter(prefix="/workflows")


# Type alias for dependency injection
WorkflowServiceDep = Annotated[WorkflowService, Depends(get_workflow_service)]


@router.get("", response_model=list[WorkflowListItem])
async def list_workflows(
    service: WorkflowServiceDep,
    folder_id: str | None = None,
    project_id: str | None = None,
) -> list[WorkflowListItem]:
    """List all workflows, optionally filtered by folder / project (id)."""
    return await service.list_workflows(folder_id=folder_id, project_id=project_id)


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
    except ConflictError as e:
        raise HTTPException(status_code=409, detail={"code": e.code, "message": e.message})
    except LockedTestsError as e:
        raise HTTPException(status_code=409, detail={"message": e.message, "test_ids": e.test_ids})
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
    except ConflictError as e:
        detail = {"code": e.code, "message": e.message}
        if "used_by" in e.details:  # WORKFLOW_IN_USE: who still calls it (section 10.3), so the studio can list them
            detail["used_by"] = e.details["used_by"]
        raise HTTPException(status_code=409, detail=detail)


@router.post("/{workflow_id}/publish", response_model=WorkflowPublishResult)
async def publish_workflow(
    workflow_id: str,
    body: PublishRequest | None = None,
    service: WorkflowServiceDep = None,
) -> WorkflowPublishResult:
    """Publish the current draft as a new immutable version. Enables triggers."""
    try:
        message = body.message if body else None
        return await service.publish(workflow_id, message=message)
    except WorkflowNotFoundError as e:
        raise HTTPException(status_code=404, detail=e.message)
    except WorkflowHasErrorsError as e:
        raise HTTPException(status_code=422, detail={"message": e.message, "findings": e.findings})
    except ApprovedTestsFailError as e:
        raise HTTPException(status_code=422, detail={"message": e.message, "failed_tests": e.failed_tests})
    except ConflictError as e:   # Stage 5: INTERFACE_SLUG_TAKEN
        raise HTTPException(status_code=409, detail={"code": e.code, "message": e.message})
    except ValidationError as e:
        raise HTTPException(status_code=400, detail=e.message)


@router.post("/{workflow_id}/check")
async def check_workflow_route(
    workflow_id: str,
    service: WorkflowServiceDep,
    body: CheckRequest | None = None,
) -> dict:
    """Run the mistake checker on the given definition or the stored draft. Never saves."""
    try:
        return await service.check(workflow_id, body.definition if body else None, body.environment if body else None)
    except WorkflowNotFoundError as e:
        raise HTTPException(status_code=404, detail=e.message)


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
        environment = body.environment if body else "default"
        return await service.run_workflow(workflow_id, input_data, environment=environment)
    except ProjectError as e:
        raise HTTPException(status_code=e.status, detail=e.detail())
    except WorkflowNotFoundError as e:
        raise HTTPException(status_code=404, detail=e.message)
    except WorkflowExecutionError as e:
        raise HTTPException(status_code=400, detail=e.message)


def _partial_run_http_error(e: Exception) -> HTTPException:
    """Map the errors of the partial-run endpoints onto HTTP."""
    if isinstance(e, ProjectError):
        return HTTPException(status_code=e.status, detail=e.detail())
    if isinstance(e, MissingNodeDataError):
        return HTTPException(status_code=409, detail={"message": e.message, "missing_nodes": e.missing_nodes})
    if isinstance(e, (WorkflowNotFoundError, NodeNotFoundInWorkflowError, ExecutionNotFoundError)):
        return HTTPException(status_code=404, detail=e.message)
    return HTTPException(status_code=400, detail=getattr(e, "message", str(e)))


_PARTIAL_RUN_ERRORS = (
    ProjectError, MissingNodeDataError, WorkflowNotFoundError, NodeNotFoundInWorkflowError,
    ExecutionNotFoundError, WorkflowExecutionError, ValidationError,
)


@router.post("/{workflow_id}/nodes/{node_name}/test", response_model=ExecutionResponse)
async def test_node(
    workflow_id: str,
    node_name: str,
    body: NodeTestRequest,
    service: WorkflowServiceDep,
) -> ExecutionResponse:
    """Run only one node with typed, last-run or pinned input."""
    try:
        return await service.test_node(workflow_id, node_name, body)
    except _PARTIAL_RUN_ERRORS as e:
        raise _partial_run_http_error(e)


@router.post("/{workflow_id}/run-to/{node_name}", response_model=ExecutionResponse)
async def run_to_node(
    workflow_id: str,
    node_name: str,
    service: WorkflowServiceDep,
    body: RunWorkflowRequest | None = None,
) -> ExecutionResponse:
    """Run from the trigger and stop after the given node."""
    try:
        return await service.run_to(
            workflow_id, node_name, body.input_data if body else None,
            environment=body.environment if body else "default")
    except _PARTIAL_RUN_ERRORS as e:
        raise _partial_run_http_error(e)


@router.post("/{workflow_id}/run-from/{node_name}", response_model=ExecutionResponse)
async def run_from_node(
    workflow_id: str,
    node_name: str,
    service: WorkflowServiceDep,
    body: RunFromRequest | None = None,
) -> ExecutionResponse:
    """Re-run the given node and everything after it, reusing a past execution's upstream outputs."""
    try:
        return await service.run_from(
            workflow_id, node_name, body.execution_id if body else None,
            environment=body.environment if body else "default")
    except _PARTIAL_RUN_ERRORS as e:
        raise _partial_run_http_error(e)


@router.post("/{workflow_id}/nodes/{node_name}/contract/learn", response_model=ContractLearnResponse)
async def learn_node_contract(
    workflow_id: str,
    node_name: str,
    service: WorkflowServiceDep,
    body: ContractLearnRequest | None = None,
) -> ContractLearnResponse:
    """Infer the node's output shape from a saved execution. Does not save anything."""
    try:
        return ContractLearnResponse(**await service.learn_contract(workflow_id, node_name, body.execution_id if body else None))
    except _PARTIAL_RUN_ERRORS as e:
        raise _partial_run_http_error(e)


@router.post("/{workflow_id}/nodes/{node_name}/contract/approve", response_model=WorkflowDetailResponse)
async def approve_node_contract(
    workflow_id: str,
    node_name: str,
    body: ContractApproveRequest,
    service: WorkflowServiceDep,
) -> WorkflowDetailResponse:
    """Write an approved output contract onto the node in the draft."""
    try:
        return await service.approve_contract(workflow_id, node_name, body)
    except _PARTIAL_RUN_ERRORS as e:
        raise _partial_run_http_error(e)


@router.delete("/{workflow_id}/nodes/{node_name}/contract", response_model=WorkflowDetailResponse)
async def delete_node_contract(
    workflow_id: str,
    node_name: str,
    service: WorkflowServiceDep,
) -> WorkflowDetailResponse:
    """Remove the node's approved output contract from the draft."""
    try:
        return await service.delete_contract(workflow_id, node_name)
    except _PARTIAL_RUN_ERRORS as e:
        raise _partial_run_http_error(e)


@router.post("/{workflow_id}/versions/{version_id}/restore", response_model=WorkflowDetailResponse)
async def restore_version(
    workflow_id: str,
    version_id: int,
    service: WorkflowServiceDep,
) -> WorkflowDetailResponse:
    """Copy a version's definition into the draft and return the workflow."""
    try:
        return await service.restore_version(workflow_id, version_id)
    except WorkflowNotFoundError as e:
        raise HTTPException(status_code=404, detail=e.message)


@router.post("/{workflow_id}/tests/{test_id}/approve", response_model=WorkflowDetailResponse)
async def approve_test(
    workflow_id: str,
    test_id: str,
    body: TestApproveRequest,
    service: WorkflowServiceDep,
) -> WorkflowDetailResponse:
    """Approve a test: it becomes locked until someone unlocks it."""
    try:
        return await service.approve_test(workflow_id, test_id, body.approved_by)
    except (WorkflowNotFoundError, TestNotFoundError) as e:
        raise HTTPException(status_code=404, detail=e.message)


@router.post("/{workflow_id}/tests/{test_id}/unlock", response_model=WorkflowDetailResponse)
async def unlock_test(
    workflow_id: str,
    test_id: str,
    body: TestUnlockRequest,
    service: WorkflowServiceDep,
) -> WorkflowDetailResponse:
    """Unlock an approved test: back to a draft."""
    try:
        return await service.unlock_test(workflow_id, test_id, body.by)
    except (WorkflowNotFoundError, TestNotFoundError) as e:
        raise HTTPException(status_code=404, detail=e.message)


@router.post("/{workflow_id}/tests/run")
async def run_tests_route(
    workflow_id: str,
    service: WorkflowServiceDep,
    body: TestRunRequest | None = None,
) -> dict:
    """Run the workflow's tests (some, or all) against the stored draft or the unsaved ``definition``. Never saves the workflow."""
    try:
        return await service.run_tests(workflow_id, body.ids if body else None, body.definition if body else None)
    except (WorkflowNotFoundError, TestNotFoundError) as e:
        raise HTTPException(status_code=404, detail=e.message)
    except ProjectError as e:
        raise HTTPException(status_code=e.status, detail=e.detail())
    except ValidationError as e:
        raise HTTPException(status_code=400, detail=e.message)


@router.post("/{workflow_id}/score")
async def score_route(
    workflow_id: str,
    service: WorkflowServiceDep,
    body: ScoreRequest | None = None,
) -> dict:
    """The scorecard: checker, tests, branch coverage, planted bugs, repeatability, gaps. Never saves."""
    body = body or ScoreRequest()
    try:
        return await service.score(workflow_id, body.definition, body.mutations, body.repeat)
    except WorkflowNotFoundError as e:
        raise HTTPException(status_code=404, detail=e.message)
    except ProjectError as e:
        raise HTTPException(status_code=e.status, detail=e.detail())
    except ValidationError as e:
        raise HTTPException(status_code=400, detail=e.message)


@router.post("/{workflow_id}/apply-ops")
async def apply_ops_route(
    workflow_id: str,
    body: ApplyOpsRequest,
    service: WorkflowServiceDep,
) -> dict:
    """Apply toolkit ops to the draft. ``dry_run`` returns the result without saving."""
    try:
        return await service.apply_ops(workflow_id, body.ops, body.dry_run)
    except OpError as e:
        raise HTTPException(status_code=400, detail=e.detail())
    except WorkflowNotFoundError as e:
        raise HTTPException(status_code=404, detail=e.message)
    except ProjectError as e:
        raise HTTPException(status_code=e.status, detail=e.detail())


@router.post("/run-adhoc", response_model=ExecutionResponse)
async def run_adhoc_workflow(
    workflow: WorkflowCreateRequest,
    service: WorkflowServiceDep,
) -> ExecutionResponse:
    """Run an ad-hoc workflow without saving."""
    try:
        return await service.run_adhoc_workflow(workflow)
    except ProjectError as e:
        raise HTTPException(status_code=e.status, detail=e.detail())
    except ValidationError as e:
        raise HTTPException(status_code=400, detail=e.message)
    except WorkflowExecutionError as e:
        raise HTTPException(status_code=400, detail=e.message)
