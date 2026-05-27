"""Variable routes."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query

from ..core.dependencies import get_variable_repository
from ..repositories.variable_repository import VariableRepository
from ..schemas.variable import (
    VariableCreateRequest,
    VariableUpdateRequest,
    VariableResponse,
    VariableListItem,
)
from ..schemas.common import SuccessResponse

router = APIRouter(prefix="/variables")

VariableRepoDep = Annotated[VariableRepository, Depends(get_variable_repository)]


@router.get("/environments", response_model=list[str])
async def list_environments(
    repo: VariableRepoDep,
    team_id: str = Query("default"),
) -> list[str]:
    return await repo.list_environments(team_id=team_id)


@router.post("", response_model=VariableResponse, status_code=201)
async def create_variable(
    body: VariableCreateRequest, repo: VariableRepoDep
) -> VariableResponse:
    existing = await repo.get_by_key(body.team_id, body.key, environment=body.environment)
    if existing:
        raise HTTPException(
            status_code=409,
            detail=f"Variable '{body.key}' already exists in environment '{body.environment}'",
        )
    variable = await repo.create(
        key=body.key,
        value=body.value,
        team_id=body.team_id,
        environment=body.environment,
        type=body.type,
        description=body.description,
    )
    return _to_response(variable)


@router.get("", response_model=list[VariableListItem])
async def list_variables(
    repo: VariableRepoDep,
    team_id: str = Query("default"),
    environment: str | None = Query(None),
) -> list[VariableListItem]:
    variables = await repo.list(team_id=team_id, environment=environment)
    return [
        VariableListItem(
            id=v.id,
            key=v.key,
            environment=v.environment,
            value=None if v.type == "secret" else v.value,
            type=v.type,
            description=v.description,
        )
        for v in variables
    ]


@router.get("/{variable_id}", response_model=VariableResponse)
async def get_variable(variable_id: int, repo: VariableRepoDep) -> VariableResponse:
    variable = await repo.get(variable_id)
    if not variable:
        raise HTTPException(status_code=404, detail="Variable not found")
    return _to_response(variable)


@router.put("/{variable_id}", response_model=VariableResponse)
async def update_variable(
    variable_id: int, body: VariableUpdateRequest, repo: VariableRepoDep
) -> VariableResponse:
    variable = await repo.update(
        variable_id,
        value=body.value,
        description=body.description,
    )
    if not variable:
        raise HTTPException(status_code=404, detail="Variable not found")
    return _to_response(variable)


@router.delete("/{variable_id}", response_model=SuccessResponse)
async def delete_variable(variable_id: int, repo: VariableRepoDep) -> SuccessResponse:
    deleted = await repo.delete(variable_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Variable not found")
    return SuccessResponse(message="Variable deleted")


def _to_response(v) -> VariableResponse:
    return VariableResponse(
        id=v.id,
        key=v.key,
        environment=v.environment,
        value=None if v.type == "secret" else v.value,
        type=v.type,
        description=v.description,
        created_at=str(v.created_at),
        updated_at=str(v.updated_at),
    )
