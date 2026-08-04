"""Discovery API — column search, facets, favorites, sheet metadata, usage.

Registered BEFORE the data_accelerator router so the static ``/datasets/facets``
path wins over the ``/datasets/{dataset_id}`` item route.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from app.api.pagination import Page, PageParams, pagination
from app.features.auth.deps import Principal, ensure_dataset_permission, get_principal
from app.features.auth.permissions import Permission
from app.shared.data_io import normalize_sheet_key

from . import repo

router = APIRouter()


def _scope(principal: Principal) -> list[str] | None:
    return None if principal.is_superuser else principal.team_ids


class ColumnHit(BaseModel):
    dataset_id: str
    dataset_name: str
    domain: str | None = None
    sheet_name: str
    sheet_key: str
    column_name: str
    normalized_name: str
    dtype: str
    position: int | None = None


class FacetsResponse(BaseModel):
    classification: dict[str, int] = Field(default_factory=dict)
    domain: dict[str, int] = Field(default_factory=dict)
    source_system: dict[str, int] = Field(default_factory=dict)
    deprecated: dict[str, int] = Field(default_factory=dict)


class SheetMetadataIn(BaseModel):
    grain: str | None = Field(default=None, max_length=500,
                              description='e.g. "one row per customer per day"')
    primary_key_columns: list[str] | None = Field(
        default=None, description="Normalized column names forming the logical key")
    description: str | None = Field(default=None, max_length=2000)


class SheetMetadataOut(SheetMetadataIn):
    id: str
    dataset_id: str
    sheet_key: str
    updated_by: str | None = None
    updated_at: str


class UsageResponse(BaseModel):
    dataset_id: str
    downloads: int
    writes: int
    total_events: int
    last_activity_at: str | None = None


@router.get("/datasets/facets", response_model=FacetsResponse, tags=["discovery"])
async def dataset_facets(principal: Principal = Depends(get_principal)) -> FacetsResponse:
    """Dataset counts by classification/domain/source_system, team-scoped."""
    return FacetsResponse(**await repo.facets(_scope(principal)))


@router.get("/search/columns", response_model=Page[ColumnHit], tags=["discovery"])
async def search_columns(
    q: str = Query(..., min_length=1, description="Column name fragment"),
    page: PageParams = Depends(pagination),
    principal: Principal = Depends(get_principal),
) -> Page[ColumnHit]:
    """Search columns across current versions of datasets you can access.

    Served entirely from captured schemas in Postgres — no file I/O.
    """
    rows, total = await repo.search_columns(q, _scope(principal),
                                            limit=page.limit, offset=page.offset)
    return Page.of([ColumnHit(**r) for r in rows], total, page)


@router.put("/datasets/{dataset_id}/favorite", status_code=204, tags=["discovery"])
async def favorite(dataset_id: str, principal: Principal = Depends(get_principal)) -> None:
    """Star a dataset for the current user."""
    await ensure_dataset_permission(principal, dataset_id, Permission.DATASET_READ)
    await repo.set_favorite(principal.user_id, dataset_id)


@router.delete("/datasets/{dataset_id}/favorite", status_code=204, tags=["discovery"])
async def unfavorite(dataset_id: str, principal: Principal = Depends(get_principal)) -> None:
    """Unstar a dataset."""
    await ensure_dataset_permission(principal, dataset_id, Permission.DATASET_READ)
    if not await repo.unset_favorite(principal.user_id, dataset_id):
        raise HTTPException(404, "Dataset is not in your favorites")


@router.put("/datasets/{dataset_id}/sheet-metadata/{sheet_key}",
            response_model=SheetMetadataOut, tags=["discovery"])
async def put_sheet_metadata(
    dataset_id: str, sheet_key: str, body: SheetMetadataIn,
    principal: Principal = Depends(get_principal),
) -> SheetMetadataOut:
    """Set grain / primary key / description for a logical sheet (by sheet_key)."""
    await ensure_dataset_permission(principal, dataset_id, Permission.DATASET_WRITE)
    row = await repo.upsert_sheet_metadata(
        dataset_id, normalize_sheet_key(sheet_key), body.model_dump(),
        updated_by=principal.user_id,
    )
    return SheetMetadataOut(**row)


@router.get("/datasets/{dataset_id}/sheet-metadata",
            response_model=Page[SheetMetadataOut], tags=["discovery"])
async def list_sheet_metadata(
    dataset_id: str, principal: Principal = Depends(get_principal),
) -> Page[SheetMetadataOut]:
    """Semantic metadata recorded for this dataset's logical sheets."""
    await ensure_dataset_permission(principal, dataset_id, Permission.DATASET_READ)
    rows = await repo.list_sheet_metadata(dataset_id)
    items = [SheetMetadataOut(**r) for r in rows]
    return Page(items=items, total=len(items), limit=len(items), offset=0)


@router.get("/datasets/{dataset_id}/usage", response_model=UsageResponse, tags=["discovery"])
async def dataset_usage(
    dataset_id: str, principal: Principal = Depends(get_principal),
) -> UsageResponse:
    """Usage counts for a dataset, derived from the audit trail."""
    await ensure_dataset_permission(principal, dataset_id, Permission.DATASET_READ)
    stats: dict[str, Any] = await repo.dataset_usage(dataset_id)
    return UsageResponse(dataset_id=dataset_id, **stats)
