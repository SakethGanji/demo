"""Folder routes."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException

from ..core.dependencies import get_folder_repository
from ..repositories.folder_repository import FolderRepository
from ..schemas.folder import (
    FolderCreateRequest,
    FolderUpdateRequest,
    FolderResponse,
    FolderListItem,
)
from ..schemas.common import SuccessResponse
from ..utils.ids import folder_id

router = APIRouter(prefix="/folders")

FolderRepoDep = Annotated[FolderRepository, Depends(get_folder_repository)]


@router.post("", response_model=FolderResponse, status_code=201)
async def create_folder(
    body: FolderCreateRequest, repo: FolderRepoDep
) -> FolderResponse:
    folder = await repo.create(
        id=folder_id(),
        name=body.name,
        team_id=body.team_id,
        parent_folder_id=body.parent_folder_id,
    )
    return FolderResponse(
        id=folder.id,
        name=folder.name,
        parent_folder_id=folder.parent_folder_id,
        team_id=folder.team_id,
        created_at=str(folder.created_at),
    )


@router.get("", response_model=list[FolderListItem])
async def list_folders(repo: FolderRepoDep) -> list[FolderListItem]:
    folders = await repo.list()
    return [
        FolderListItem(
            id=f.id,
            name=f.name,
            parent_folder_id=f.parent_folder_id,
            created_at=str(f.created_at),
        )
        for f in folders
    ]


@router.get("/{folder_id}", response_model=FolderResponse)
async def get_folder(folder_id: str, repo: FolderRepoDep) -> FolderResponse:
    folder = await repo.get(folder_id)
    if not folder:
        raise HTTPException(status_code=404, detail="Folder not found")
    return FolderResponse(
        id=folder.id,
        name=folder.name,
        parent_folder_id=folder.parent_folder_id,
        team_id=folder.team_id,
        created_at=str(folder.created_at),
    )


@router.put("/{folder_id}", response_model=FolderResponse)
async def update_folder(
    folder_id: str, body: FolderUpdateRequest, repo: FolderRepoDep
) -> FolderResponse:
    folder = await repo.update(
        folder_id,
        name=body.name,
        parent_folder_id=body.parent_folder_id,
    )
    if not folder:
        raise HTTPException(status_code=404, detail="Folder not found")
    return FolderResponse(
        id=folder.id,
        name=folder.name,
        parent_folder_id=folder.parent_folder_id,
        team_id=folder.team_id,
        created_at=str(folder.created_at),
    )


@router.delete("/{folder_id}", response_model=SuccessResponse)
async def delete_folder(folder_id: str, repo: FolderRepoDep) -> SuccessResponse:
    deleted = await repo.delete(folder_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Folder not found")
    return SuccessResponse(message="Folder deleted")
