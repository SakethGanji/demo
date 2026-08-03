"""Data accelerator API routes — datasets, versions, tags, sheets, and analytics.

All routes are mounted under the service-wide ``/api/v1`` prefix (see app.main)
behind the global authentication guard. Item routes additionally enforce
team-scoped RBAC via ``ensure_dataset_permission``; list routes are scoped to the
caller's teams. List endpoints return the uniform ``Page`` envelope; errors are
problem+json.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query

from . import repo
from .schemas import (
    AggregateRequest,
    AggregateResponse,
    DatasetInfo,
    DatasetMetadataResponse,
    DatasetPatched,
    DatasetSearchResult,
    DeleteResponse,
    ProfileRequest,
    ProfileResponse,
    SampleRequest,
    SampleResponse,
    SetTagRequest,
    SheetMetadataResponse,
    TagInfo,
    UpdateDatasetRequest,
    VersionInfo,
)
from .services.aggregation import run_aggregation
from .services.datasets import (
    get_dataset_metadata,
    get_dataset_sheets,
    get_sheet_metadata,
)
from .services.profiling import run_profiling
from .services.sampling import run_sampling_pipeline
from app.api.pagination import Page, PageParams, pagination
from app.features.auth.deps import Principal, ensure_dataset_permission, get_principal
from app.features.auth.permissions import Permission
from app.features.files.services.management import delete_dataset_with_files

router = APIRouter()


def _collection(items: list) -> Page:
    """Wrap a small, fully-materialised sub-collection in the Page envelope."""
    return Page(items=items, total=len(items), limit=len(items), offset=0)


def _scope(principal: Principal) -> list[str] | None:
    """Team filter for list queries: None for superusers (all teams)."""
    return None if principal.is_superuser else principal.team_ids


async def _authorize_source(principal: Principal, request) -> None:
    """Analytics ops accept a dataset_id, a file_path, or inline data.

    A dataset_id must be readable by the caller (team RBAC). A raw file_path
    reads straight from the server filesystem with no team scoping, so it is
    restricted to platform superusers — otherwise any member could point it at
    another team's parquet files. Inline data is the caller's own.
    """
    if getattr(request, "file_path", None) and not principal.is_superuser:
        raise HTTPException(403, "file_path sources are restricted to platform administrators")
    if getattr(request, "dataset_id", None):
        await ensure_dataset_permission(principal, request.dataset_id, Permission.DATASET_READ)


# ---------------------------------------------------------------------------
# Datasets
# ---------------------------------------------------------------------------

@router.get("/datasets/search", response_model=Page[DatasetSearchResult], tags=["datasets"])
async def search_datasets(
    q: str = Query(..., min_length=1, description="Search query (matches name and description)"),
    page: PageParams = Depends(pagination),
    principal: Principal = Depends(get_principal),
) -> Page[DatasetSearchResult]:
    """Search datasets you can access by name/description (versions + tags inline)."""
    results, total = await repo.search_datasets(
        query=q, team_ids=_scope(principal), limit=page.limit, offset=page.offset,
    )
    return Page.of([DatasetSearchResult(**r) for r in results], total, page)


@router.get("/datasets", response_model=Page[DatasetInfo], tags=["datasets"])
async def list_datasets(
    q: str | None = Query(None, description="Filter datasets by name/description"),
    page: PageParams = Depends(pagination),
    principal: Principal = Depends(get_principal),
) -> Page[DatasetInfo]:
    """List datasets you can access, with optional search and pagination."""
    rows, total = await repo.list_datasets(
        team_ids=_scope(principal), search=q, limit=page.limit, offset=page.offset,
    )
    return Page.of([DatasetInfo(**r) for r in rows], total, page)


@router.get("/datasets/{dataset_id}", response_model=DatasetMetadataResponse, tags=["datasets"])
async def get_dataset(dataset_id: str, principal: Principal = Depends(get_principal)) -> DatasetMetadataResponse:
    """Return metadata (columns, preview, sheets) for the dataset's current version."""
    await ensure_dataset_permission(principal, dataset_id, Permission.DATASET_READ)
    return await get_dataset_metadata(dataset_id)


@router.patch("/datasets/{dataset_id}", response_model=DatasetPatched, tags=["datasets"])
async def update_dataset(
    dataset_id: str, body: UpdateDatasetRequest, principal: Principal = Depends(get_principal),
) -> DatasetPatched:
    """Update a dataset's name and/or description."""
    await ensure_dataset_permission(principal, dataset_id, Permission.DATASET_WRITE)
    if body.name is None and body.description is None and body.classification is None:
        raise HTTPException(400, "Provide at least one of: name, description, classification")
    row = await repo.update_dataset(
        dataset_id, name=body.name, description=body.description,
        classification=body.classification,
    )
    if not row:
        raise HTTPException(404, f"Dataset not found: {dataset_id}")
    return DatasetPatched(**row)


@router.delete("/datasets/{dataset_id}", response_model=DeleteResponse, tags=["datasets"])
async def delete_dataset_endpoint(dataset_id: str, principal: Principal = Depends(get_principal)) -> DeleteResponse:
    """Delete a dataset, all its versions, and associated storage files."""
    await ensure_dataset_permission(principal, dataset_id, Permission.DATASET_DELETE)
    return await delete_dataset_with_files(dataset_id)


# ---------------------------------------------------------------------------
# Versions
# ---------------------------------------------------------------------------

@router.get("/datasets/{dataset_id}/versions", response_model=Page[VersionInfo], tags=["versions"])
async def list_dataset_versions(dataset_id: str, principal: Principal = Depends(get_principal)) -> Page[VersionInfo]:
    """List all versions for a dataset, newest first, including tags."""
    await ensure_dataset_permission(principal, dataset_id, Permission.DATASET_READ)
    rows = await repo.list_versions(dataset_id)
    return _collection([VersionInfo(**r) for r in rows])


# ---------------------------------------------------------------------------
# Tags
# ---------------------------------------------------------------------------

@router.get("/datasets/{dataset_id}/tags", response_model=Page[TagInfo], tags=["tags"])
async def list_tags(dataset_id: str, principal: Principal = Depends(get_principal)) -> Page[TagInfo]:
    """List all tags for a dataset."""
    await ensure_dataset_permission(principal, dataset_id, Permission.DATASET_READ)
    rows = await repo.list_tags_for_dataset(dataset_id)
    return _collection([TagInfo(**r) for r in rows])


@router.put("/datasets/{dataset_id}/tags", response_model=TagInfo, tags=["tags"])
async def set_tag(dataset_id: str, body: SetTagRequest, principal: Principal = Depends(get_principal)) -> TagInfo:
    """Create or move a tag (e.g. 'production') to a specific version."""
    await ensure_dataset_permission(principal, dataset_id, Permission.DATASET_WRITE)

    version_id = body.version_id
    if version_id is None and body.version_number is not None:
        ver_row = await repo.get_version_by_number(dataset_id, body.version_number)
        if not ver_row:
            raise HTTPException(404, f"Version {body.version_number} not found for dataset {dataset_id}")
        version_id = str(ver_row["id"])
    elif version_id is None:
        raise HTTPException(400, "Provide either version_id or version_number")

    ver = await repo.get_version(version_id)
    if not ver or str(ver["dataset_id"]) != dataset_id:
        raise HTTPException(404, f"Version {version_id} not found for dataset {dataset_id}")

    tag_row = await repo.set_tag(dataset_id, version_id, body.tag_name, created_by=principal.user_id)
    return TagInfo(
        tag_name=tag_row["tag_name"],
        version_id=tag_row["version_id"],
        version_number=ver["version_number"],
        created_at=tag_row["created_at"],
        updated_at=tag_row["updated_at"],
    )


@router.get("/datasets/{dataset_id}/tags/{tag_name}", response_model=VersionInfo, tags=["tags"])
async def resolve_tag(dataset_id: str, tag_name: str, principal: Principal = Depends(get_principal)) -> VersionInfo:
    """Resolve a tag to its version metadata."""
    await ensure_dataset_permission(principal, dataset_id, Permission.DATASET_READ)
    ver = await repo.get_version_by_tag(dataset_id, tag_name)
    if not ver:
        raise HTTPException(404, f"Tag '{tag_name}' not found on dataset {dataset_id}")
    tags = await repo.list_tags_for_version(str(ver["id"]))
    return VersionInfo(
        id=str(ver["id"]),
        version_number=ver["version_number"],
        status=ver["status"],
        size_bytes=ver.get("size_bytes"),
        row_count=ver.get("row_count"),
        checksum=ver.get("checksum"),
        created_at=str(ver["created_at"]),
        processed_at=str(ver["processed_at"]) if ver.get("processed_at") else None,
        tags=tags,
    )


@router.delete("/datasets/{dataset_id}/tags/{tag_name}", response_model=DeleteResponse, tags=["tags"])
async def delete_tag(dataset_id: str, tag_name: str, principal: Principal = Depends(get_principal)) -> DeleteResponse:
    """Remove a tag from a dataset."""
    await ensure_dataset_permission(principal, dataset_id, Permission.DATASET_WRITE)
    deleted = await repo.delete_tag(dataset_id, tag_name)
    if not deleted:
        raise HTTPException(404, f"Tag '{tag_name}' not found on dataset {dataset_id}")
    return DeleteResponse(success=True, message=f"Tag '{tag_name}' deleted")


# ---------------------------------------------------------------------------
# Sheets
# ---------------------------------------------------------------------------

@router.get("/datasets/{dataset_id}/sheets", response_model=Page[SheetMetadataResponse], tags=["sheets"])
async def list_sheets(dataset_id: str, principal: Principal = Depends(get_principal)) -> Page[SheetMetadataResponse]:
    """List all sheets in a multi-sheet dataset with full metadata."""
    await ensure_dataset_permission(principal, dataset_id, Permission.DATASET_READ)
    sheets = await get_dataset_sheets(dataset_id)
    return _collection(sheets)


@router.get("/datasets/{dataset_id}/sheets/{sheet_name}", response_model=SheetMetadataResponse, tags=["sheets"])
async def get_sheet(dataset_id: str, sheet_name: str, principal: Principal = Depends(get_principal)) -> SheetMetadataResponse:
    """Return metadata for a single sheet."""
    await ensure_dataset_permission(principal, dataset_id, Permission.DATASET_READ)
    return await get_sheet_metadata(dataset_id, sheet_name)


# ---------------------------------------------------------------------------
# Analytics — sampling, profiling, aggregation
# ---------------------------------------------------------------------------

@router.post("/sample", response_model=SampleResponse, tags=["analytics"])
async def sample_data(request: SampleRequest, principal: Principal = Depends(get_principal)) -> SampleResponse:
    """Goal-oriented data sampling over a dataset, file, or inline data."""
    await _authorize_source(principal, request)
    return await run_sampling_pipeline(request)


@router.post("/profile", response_model=ProfileResponse, tags=["analytics"])
async def profile_data(request: ProfileRequest, principal: Principal = Depends(get_principal)) -> ProfileResponse:
    """Profile data columns — statistics, distributions, data quality."""
    await _authorize_source(principal, request)
    return await run_profiling(request)


@router.post("/aggregate", response_model=AggregateResponse, tags=["analytics"])
async def aggregate_data(request: AggregateRequest, principal: Principal = Depends(get_principal)) -> AggregateResponse:
    """Aggregate data with group-by, sort, and optional filtering."""
    await _authorize_source(principal, request)
    return await run_aggregation(request)
