"""Variable routes."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException

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


@router.post("", response_model=VariableResponse, status_code=201)
async def create_variable(
    body: VariableCreateRequest, repo: VariableRepoDep
) -> VariableResponse:
    existing = await repo.get_by_key(body.team_id, body.key)
    if existing:
        raise HTTPException(status_code=409, detail=f"Variable '{body.key}' already exists")
    variable = await repo.create(
        key=body.key,
        value=body.value,
        team_id=body.team_id,
        type=body.type,
        description=body.description,
    )
    return _to_response(variable)


@router.get("", response_model=list[VariableListItem])
async def list_variables(repo: VariableRepoDep) -> list[VariableListItem]:
    variables = await repo.list()
    return [
        VariableListItem(
            id=v.id,
            key=v.key,
            value=v.value,
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
        value=v.value,
        type=v.type,
        description=v.description,
        created_at=str(v.created_at),
        updated_at=str(v.updated_at),
    )
