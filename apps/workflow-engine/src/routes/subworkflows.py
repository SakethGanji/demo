"""Sub-workflow routes (Stage 8, DESIGN-STAGE8-REUSE section 11).

    GET  /projects/{project}/subworkflows          the picker: callable workflows with typed inputs and output fields
    GET  /workflows/{id}/io                         what a workflow takes and gives as a sub-workflow
    GET  /workflows/{id}/used-by                    who calls it (draft, published, release)
    POST /workflows/{id}/extract-subworkflow        convert a selection of nodes into a sub-workflow (dry run by default)
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession

from ..core.dependencies import get_db_session, get_workflow_service
from ..core.exceptions import WorkflowNotFoundError
from ..schemas.subworkflow import ExtractRequest, ExtractResponse, SubworkflowIO, SubworkflowListItem, UsedByRow
from ..services.project_service import ProjectError, ProjectService
from ..services.subworkflow_api import SubworkflowApi, SubworkflowApiError, parse_version
from ..services.workflow_service import WorkflowService
from .projects import raise_project_error

router = APIRouter()


def _http(e: Exception) -> HTTPException:
    if isinstance(e, WorkflowNotFoundError):
        return HTTPException(status_code=404, detail=e.message)
    if isinstance(e, SubworkflowApiError):
        return HTTPException(status_code=e.status, detail=e.detail)
    raise e


@router.get("/projects/{project}/subworkflows", response_model=list[SubworkflowListItem])
async def list_subworkflows(
    project: str,
    q: str | None = Query(None, description="Filter by name or description"),
    callable_only: bool = Query(False, description="Leave out workflows that can't be called"),
    session: AsyncSession = Depends(get_db_session),
):
    try:
        p = await ProjectService(session).require(project)
    except ProjectError as e:
        raise_project_error(e)
    return await SubworkflowApi(session).list_for_project(p, q, callable_only)


@router.get("/workflows/{workflow_id}/io", response_model=SubworkflowIO)
async def workflow_io(
    workflow_id: str,
    version: str | None = Query(None, description="'draft' | 'latest' | a version number. Default: published, else draft"),
    session: AsyncSession = Depends(get_db_session),
):
    try:
        return await SubworkflowApi(session).workflow_io(workflow_id, parse_version(version))
    except (WorkflowNotFoundError, SubworkflowApiError) as e:
        raise _http(e)


@router.get("/workflows/{workflow_id}/used-by", response_model=list[UsedByRow])
async def used_by(workflow_id: str, session: AsyncSession = Depends(get_db_session)):
    try:
        return await SubworkflowApi(session).used_by(workflow_id)
    except WorkflowNotFoundError as e:
        raise _http(e)


@router.post("/workflows/{workflow_id}/extract-subworkflow", response_model=ExtractResponse, response_model_exclude_none=False)
async def extract_subworkflow(
    workflow_id: str,
    body: ExtractRequest,
    service: Annotated[WorkflowService, Depends(get_workflow_service)],
    session: AsyncSession = Depends(get_db_session),
):
    try:
        return await SubworkflowApi(session).extract(
            workflow_id, body.nodes, body.name, body.dry_run, body.definition, service)
    except (WorkflowNotFoundError, SubworkflowApiError) as e:
        raise _http(e)
