"""Data accelerator API routes — datasets, tags, sampling, profiling, aggregation."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query

from . import repo
from .schemas import (
    AggregateRequest,
    AggregateResponse,
    DatasetInfo,
    DatasetListResponse,
    DatasetMetadataResponse,
    DatasetSearchResponse,
    DatasetSearchResult,
    DeleteResponse,
    ProfileRequest,
    ProfileResponse,
    SampleRequest,
    SampleResponse,
    SetTagRequest,
    SheetMetadataResponse,
    TagInfo,
    TagListResponse,
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
from app.features.files.services.management import delete_dataset_with_files

router = APIRouter()


# ---------------------------------------------------------------------------
# Dataset management (moved from files)
# ---------------------------------------------------------------------------

@router.get("/datasets/search", response_model=DatasetSearchResponse)
async def search_datasets(
    q: str = Query(..., min_length=1, description="Search query (matches name and description)"),
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
) -> DatasetSearchResponse:
    """Search datasets by name/description. Returns matching datasets with all versions + tags inline."""
    results, total = await repo.search_datasets(query=q, limit=limit, offset=offset)
    return DatasetSearchResponse(
        results=[DatasetSearchResult(**r) for r in results],
        total_count=total,
        query=q,
    )


@router.get("/datasets", response_model=DatasetListResponse)
async def list_datasets(
    search: str | None = Query(None, description="Filter datasets by name"),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
) -> DatasetListResponse:
    """List datasets with optional search and pagination."""
    rows, total = await repo.list_datasets(search=search, limit=limit, offset=offset)
    datasets = [DatasetInfo(**r) for r in rows]
    return DatasetListResponse(datasets=datasets, total_count=total)


@router.delete("/datasets/{dataset_id}", response_model=DeleteResponse)
async def delete_dataset_endpoint(dataset_id: str) -> DeleteResponse:
    """Delete a dataset, all its versions, and associated storage files."""
    return await delete_dataset_with_files(dataset_id)


@router.get("/datasets/{dataset_id}/versions", response_model=list[VersionInfo])
async def list_dataset_versions(dataset_id: str) -> list[VersionInfo]:
    """List all versions for a dataset, including tags."""
    ds = await repo.get_dataset(dataset_id)
    if not ds:
        raise HTTPException(404, f"Dataset not found: {dataset_id}")
    rows = await repo.list_versions(dataset_id)
    return [VersionInfo(**r) for r in rows]


# ---------------------------------------------------------------------------
# Tags
# ---------------------------------------------------------------------------

@router.get("/datasets/{dataset_id}/tags", response_model=TagListResponse)
async def list_tags(dataset_id: str) -> TagListResponse:
    """List all tags for a dataset."""
    ds = await repo.get_dataset(dataset_id)
    if not ds:
        raise HTTPException(404, f"Dataset not found: {dataset_id}")
    rows = await repo.list_tags_for_dataset(dataset_id)
    tags = [TagInfo(**r) for r in rows]
    return TagListResponse(tags=tags)


@router.put("/datasets/{dataset_id}/tags", response_model=TagInfo)
async def set_tag(dataset_id: str, body: SetTagRequest) -> TagInfo:
    """Create or move a tag to a specific version."""
    ds = await repo.get_dataset(dataset_id)
    if not ds:
        raise HTTPException(404, f"Dataset not found: {dataset_id}")

    # Resolve version_id from version_number if needed
    version_id = body.version_id
    if version_id is None and body.version_number is not None:
        ver_row = await repo.get_version_by_number(dataset_id, body.version_number)
        if not ver_row:
            raise HTTPException(404, f"Version {body.version_number} not found for dataset {dataset_id}")
        version_id = str(ver_row["id"])
    elif version_id is None:
        raise HTTPException(400, "Provide either version_id or version_number")

    # Verify version belongs to this dataset
    ver = await repo.get_version(version_id)
    if not ver or str(ver["dataset_id"]) != dataset_id:
        raise HTTPException(404, f"Version {version_id} not found for dataset {dataset_id}")

    tag_row = await repo.set_tag(dataset_id, version_id, body.tag_name)
    # Fetch version_number for response
    return TagInfo(
        tag_name=tag_row["tag_name"],
        version_id=tag_row["version_id"],
        version_number=ver["version_number"],
        created_at=tag_row["created_at"],
        updated_at=tag_row["updated_at"],
    )


@router.delete("/datasets/{dataset_id}/tags/{tag_name}")
async def delete_tag(dataset_id: str, tag_name: str) -> dict:
    """Remove a tag from a dataset."""
    ds = await repo.get_dataset(dataset_id)
    if not ds:
        raise HTTPException(404, f"Dataset not found: {dataset_id}")
    deleted = await repo.delete_tag(dataset_id, tag_name)
    if not deleted:
        raise HTTPException(404, f"Tag '{tag_name}' not found on dataset {dataset_id}")
    return {"success": True, "message": f"Tag '{tag_name}' deleted"}


@router.get("/datasets/{dataset_id}/tags/{tag_name}", response_model=VersionInfo)
async def resolve_tag(dataset_id: str, tag_name: str) -> VersionInfo:
    """Resolve a tag to its version metadata."""
    ds = await repo.get_dataset(dataset_id)
    if not ds:
        raise HTTPException(404, f"Dataset not found: {dataset_id}")
    ver = await repo.get_version_by_tag(dataset_id, tag_name)
    if not ver:
        raise HTTPException(404, f"Tag '{tag_name}' not found on dataset {dataset_id}")
    # Get all tags for this version
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


# ---------------------------------------------------------------------------
# Dataset metadata
# ---------------------------------------------------------------------------

@router.get("/datasets/{dataset_id}", response_model=DatasetMetadataResponse)
async def get_dataset(dataset_id: str) -> DatasetMetadataResponse:
    """Return metadata for a previously uploaded dataset."""
    return await get_dataset_metadata(dataset_id)


@router.get("/datasets/{dataset_id}/sheets", response_model=list[SheetMetadataResponse])
async def list_sheets(dataset_id: str) -> list[SheetMetadataResponse]:
    """List all sheets in a multi-sheet dataset with full metadata."""
    return await get_dataset_sheets(dataset_id)


@router.get("/datasets/{dataset_id}/sheets/{sheet_name}", response_model=SheetMetadataResponse)
async def get_sheet(dataset_id: str, sheet_name: str) -> SheetMetadataResponse:
    """Return metadata for a single sheet."""
    return await get_sheet_metadata(dataset_id, sheet_name)


# ---------------------------------------------------------------------------
# Sampling
# ---------------------------------------------------------------------------

@router.post("/sample", response_model=SampleResponse)
async def sample_data(request: SampleRequest) -> SampleResponse:
    """Goal-oriented data sampling."""
    return await run_sampling_pipeline(request)


# ---------------------------------------------------------------------------
# Profiling
# ---------------------------------------------------------------------------

@router.post("/profile", response_model=ProfileResponse)
async def profile_data(request: ProfileRequest) -> ProfileResponse:
    """Profile data columns — statistics, distributions, data quality."""
    return await run_profiling(request)


# ---------------------------------------------------------------------------
# Aggregation
# ---------------------------------------------------------------------------

@router.post("/aggregate", response_model=AggregateResponse)
async def aggregate_data(request: AggregateRequest) -> AggregateResponse:
    """Aggregate data with group-by, sort, and optional filtering."""
    return await run_aggregation(request)
