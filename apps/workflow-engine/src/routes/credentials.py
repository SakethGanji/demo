"""Credential routes."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException

from ..core.dependencies import get_credential_service
from ..services.credential_service import CredentialService
from ..schemas.credential import (
    CredentialCreateRequest,
    CredentialUpdateRequest,
    CredentialResponse,
    CredentialListItem,
)
from ..schemas.common import SuccessResponse

router = APIRouter(prefix="/credentials")

CredentialServiceDep = Annotated[CredentialService, Depends(get_credential_service)]


@router.post("", response_model=CredentialResponse, status_code=201)
async def create_credential(
    body: CredentialCreateRequest,
    service: CredentialServiceDep,
) -> CredentialResponse:
    try:
        result = await service.create(
            name=body.name, type=body.type, data=body.data, team_id=body.team_id
        )
        return CredentialResponse(**result)
    except RuntimeError as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("", response_model=list[CredentialListItem])
async def list_credentials(service: CredentialServiceDep) -> list[CredentialListItem]:
    results = await service.list()
    return [CredentialListItem(**r) for r in results]


@router.get("/{credential_id}", response_model=CredentialResponse)
async def get_credential(
    credential_id: str, service: CredentialServiceDep
) -> CredentialResponse:
    result = await service.get(credential_id)
    if not result:
        raise HTTPException(status_code=404, detail="Credential not found")
    return CredentialResponse(**result)


@router.put("/{credential_id}", response_model=CredentialResponse)
async def update_credential(
    credential_id: str,
    body: CredentialUpdateRequest,
    service: CredentialServiceDep,
) -> CredentialResponse:
    try:
        result = await service.update(credential_id, data=body.data, name=body.name, type=body.type)
    except RuntimeError as e:
        raise HTTPException(status_code=500, detail=str(e))
    if not result:
        raise HTTPException(status_code=404, detail="Credential not found")
    return CredentialResponse(
        id=result["id"], name=result["name"], type=result["type"],
        created_at=result.get("created_at", result.get("updated_at", "")),
    )


@router.delete("/{credential_id}", response_model=SuccessResponse)
async def delete_credential(
    credential_id: str, service: CredentialServiceDep
) -> SuccessResponse:
    deleted = await service.delete(credential_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Credential not found")
    return SuccessResponse(message="Credential deleted")
