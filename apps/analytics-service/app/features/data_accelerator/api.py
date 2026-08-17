"""Data accelerator API routes — datasets, versions, tags, sheets, and analytics.

All routes are mounted under the service-wide ``/api/v1`` prefix (see app.main)
behind the global authentication guard. Item routes additionally enforce
team-scoped RBAC via ``ensure_dataset_permission``; list routes are scoped to the
caller's teams. List endpoints return the uniform ``Page`` envelope; errors are
problem+json.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, Request

from . import repo
from .schemas import (
    AggregateRequest,
    AggregateResponse,
    ConfirmRenameRequest,
    ConfirmRenameResponse,
    CoordinatedSampleRequest,
    CoordinatedSampleResponse,
    DatasetInfo,
    DatasetMetadataResponse,
    DatasetPatched,
    DatasetSearchResult,
    DeleteResponse,
    DocumentationLevel,
    PivotRequest,
    PivotResponse,
    ProfileRequest,
    RowDiffRequest,
    RowDiffResponse,
    ProfileResponse,
    PromoteTagRequest,
    RollbackTagRequest,
    SampleRequest,
    SampleResponse,
    SetTagRequest,
    SheetDiffResponse,
    SheetMetadataResponse,
    TagHistoryEntry,
    TagInfo,
    TagOpResponse,
    UpdateDatasetRequest,
    ValidationStatus,
    VersionInfo,
    WorkbookDiffResponse,
)
from .services.aggregation import run_aggregation
from .services.pivot import run_pivot
from .services.datasets import (
    get_dataset_metadata,
    get_dataset_sheets,
    get_sheet_metadata,
    get_version_sheets,
)
from .services.diffs import sheet_diff, workbook_diff
from .services.rowdiff import run_row_diff
from .services.sheet_identity import confirm_sheet_rename
from .services.profiling import run_profiling
from .services.sampling import run_coordinated_sampling, run_sampling_pipeline
from app.api.errors import ProblemException
from app.api.pagination import Page, PageParams, pagination
from app.features.auth.deps import (
    Principal,
    ensure_dataset_permission,
    get_principal,
    pick_active_team,
)
from app.features.auth.permissions import Permission
from app.features.webhooks import service as webhooks
from app.features.files.services.management import delete_dataset_with_files
from app.features.library import repo as library_repo
from app.infra.db.storage import ArtifactLayout, get_storage
from app.shared.datasets import resolve_version
from app.shared.masking import ensure_raw_access

router = APIRouter()


def _collection(items: list) -> Page:
    """Wrap a small, fully-materialised sub-collection in the Page envelope.

    limit is floored at 1: an empty collection (a fresh dataset's tags/sheets/
    versions) must not report limit:0, which the Page envelope rejects and a
    pager computing ceil(total/limit) divides by."""
    return Page(items=items, total=len(items), limit=max(len(items), 1), offset=0)


def _scope(principal: Principal) -> list[str] | None:
    """Team filter for list queries: None for superusers (all teams)."""
    return None if principal.is_superuser else principal.team_ids


def _norm_tag(name: str) -> str:
    """Tags are case-insensitive slugs — normalize path params the same way
    the SetTagRequest validator normalizes bodies."""
    return name.strip().lower()


def _rid(request: Request) -> str | None:
    """The request id assigned by middleware, for history correlation."""
    return getattr(request.state, "request_id", None)


async def _authorize_source(principal: Principal, request) -> dict | None:
    """Analytics ops accept a dataset_id, a file_path, or inline data.

    A dataset_id must be readable by the caller (team RBAC). A raw file_path
    reads straight from the server filesystem with no team scoping, so it is
    restricted to platform superusers — otherwise any member could point it at
    another team's parquet files. Inline data is the caller's own.

    Returns the dataset row when the source is a dataset (artifact ownership).

    Datasets that declare a sensitive column additionally require
    ``dataset:read_sensitive``, exactly as ``/download`` and the SQL console do.
    These endpoints take caller-supplied filters, group-bys, stratification
    keys and sort orders, and they persist their unmasked output as a parquet
    artifact the whole team can fetch — so per-column masking of the inline
    preview would not hold: a filter on a masked column plus the returned row
    count is a working search oracle, and the artifact bypasses the preview
    entirely. Datasets that declare nothing are unaffected.
    """
    if getattr(request, "file_path", None) and not principal.is_superuser:
        raise HTTPException(403, "file_path sources are restricted to platform administrators")
    if getattr(request, "dataset_id", None):
        ds = await ensure_dataset_permission(principal, request.dataset_id, Permission.DATASET_READ)
        await ensure_raw_access(principal, request.dataset_id)
        return ds
    return None


def _output_layout(principal: Principal, ds: dict | None,
                   artifact_type: str) -> ArtifactLayout:
    """Where a derived output of *artifact_type* belongs in the bucket.

    An output belongs to the source dataset's team, or to the caller's team for
    dataset-less sources (inline data, superuser file_path). Both the service
    that writes the parquet and :func:`_register_output_artifacts` build the
    layout from here, so they agree on the key without passing it around.
    """
    if ds:
        team_id: str | None = str(ds["team_id"])
    else:
        try:
            team_id = pick_active_team(principal, None)
        except HTTPException:  # no unambiguous team → ownerless (superuser-only)
            team_id = None
    return ArtifactLayout(artifact_type, team_id=team_id,
                          dataset_id=str(ds["id"]) if ds else None)


async def _register_output_artifacts(
    principal: Principal, ds: dict | None, artifact_type: str, filenames: list[str | None],
) -> None:
    """Record ownership for persisted sample/aggregation outputs.

    Downloads under /samples/{filename} authorize against these rows, and now
    also *resolve* through them: the storage key is no longer derivable from a
    filename alone, so the row is the only mapping.
    """
    layout = _output_layout(principal, ds, artifact_type)
    storage = get_storage()
    for filename in filenames:
        if not filename:
            continue
        key = layout.key(filename)
        try:
            size = storage.size(key)
        except Exception:  # listing degrades to 0 rather than failing the run
            size = None
        await library_repo.create_artifact(
            key,
            artifact_type,
            filename=filename,
            format="parquet",
            size_bytes=size,
            created_by=principal.user_id,
            dataset_id=str(ds["id"]) if ds else None,
            team_id=layout.team_id,
        )


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
    domain: str | None = Query(None, description="Filter by domain"),
    favorites: bool = Query(False, description="Only your starred datasets"),
    include_deprecated: bool = Query(True, description="Include deprecated datasets"),
    validation_status: ValidationStatus | None = Query(
        None, description="Filter by validation status: passed | failed | none"),
    has_schema_drift: bool | None = Query(
        None, description="Filter by whether the schema has drifted"),
    documentation: DocumentationLevel | None = Query(
        None, description="Filter by documentation completeness: full | partial | none"),
    page: PageParams = Depends(pagination),
    principal: Principal = Depends(get_principal),
) -> Page[DatasetInfo]:
    """List datasets you can access, with search, facet filters, and favorites.

    ``validation_status`` and ``documentation`` are closed vocabularies (the
    ones ``discovery.repo.signals_lateral`` emits), so a mistyped facet is a
    422 naming the allowed values. Typed as bare strings they matched nothing
    and returned an empty 200 with ``total: 0`` — indistinguishable from "you
    can't see any datasets", which is what callers blamed it on.
    """
    rows, total = await repo.list_datasets(
        team_ids=_scope(principal), search=q, limit=page.limit, offset=page.offset,
        domain=domain,
        favorites_user_id=principal.user_id if favorites else None,
        include_deprecated=include_deprecated,
        viewer_user_id=principal.user_id,
        validation_status=validation_status,
        has_schema_drift=has_schema_drift,
        documentation=documentation,
    )
    return Page.of([DatasetInfo(**r) for r in rows], total, page)


@router.get("/datasets/{dataset_id}", response_model=DatasetMetadataResponse, tags=["datasets"])
async def get_dataset(dataset_id: str, principal: Principal = Depends(get_principal)) -> DatasetMetadataResponse:
    """Return metadata (columns, preview, sheets) for the dataset's current version.

    The preview holds literal cells, so columns the data dictionary marks
    sensitive come back masked unless the caller has elevated access;
    `masked_columns` says which.
    """
    await ensure_dataset_permission(principal, dataset_id, Permission.DATASET_READ)
    return await get_dataset_metadata(dataset_id, principal)


@router.patch("/datasets/{dataset_id}", response_model=DatasetPatched, tags=["datasets"])
async def update_dataset(
    dataset_id: str, body: UpdateDatasetRequest, principal: Principal = Depends(get_principal),
) -> DatasetPatched:
    """Update a dataset's descriptive + discovery metadata.

    Only the fields **present** in the body are written; omitted fields keep
    their stored value. An explicit ``null`` still clears a nullable field, so
    partial updates never destroy data by accident and clearing stays possible.
    An empty body is a no-op that returns the record unchanged.

    Same technique as PATCH sheet-metadata / column-metadata:
    ``model_dump(exclude_unset=True)`` plus a clause-by-clause UPDATE.
    """
    await ensure_dataset_permission(principal, dataset_id, Permission.DATASET_WRITE)
    row = await repo.update_dataset(dataset_id, body.model_dump(exclude_unset=True))
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


def _wants_profile(include: str | None) -> bool:
    """Parse the diff endpoints' comma-separated ``include`` param."""
    parts = {p.strip() for p in (include or "").split(",") if p.strip()}
    unknown = parts - {"profile"}
    if unknown:
        raise HTTPException(400, f"Unknown include section(s): {', '.join(sorted(unknown))}")
    return "profile" in parts


@router.get(
    "/datasets/{dataset_id}/versions/{from_version}/diff/{to_version}",
    response_model=WorkbookDiffResponse, tags=["versions"],
)
async def diff_versions(
    dataset_id: str, from_version: int, to_version: int,
    include: str | None = Query(
        None, description="Extra sections: 'profile' adds per-sheet profile drift"),
    principal: Principal = Depends(get_principal),
) -> WorkbookDiffResponse:
    """Workbook-level diff between two versions: added/removed/modified/unchanged
    sheets, plus advisory rename candidates (matching schema fingerprints).
    With include=profile, adds drift computed from persisted profile runs."""
    await ensure_dataset_permission(principal, dataset_id, Permission.DATASET_READ)
    # Profile drift's added/removed categories are verbatim cell values taken
    # from `top_values`, so the profile section is a raw read even though the
    # diff itself isn't. No-op for datasets that declare nothing sensitive.
    if _wants_profile(include):
        await ensure_raw_access(principal, dataset_id)
    return await workbook_diff(dataset_id, from_version, to_version,
                               include_profile=_wants_profile(include))


@router.get(
    "/datasets/{dataset_id}/versions/{from_version}/sheets/{sheet_name}/diff/{to_version}",
    response_model=SheetDiffResponse, tags=["sheets"],
)
async def diff_sheet(
    dataset_id: str, from_version: int, sheet_name: str, to_version: int,
    include: str | None = Query(
        None, description="Extra sections: 'profile' adds profile drift "
                          "(400 profile-required when either side lacks a run)"),
    principal: Principal = Depends(get_principal),
) -> SheetDiffResponse:
    """Column-level schema diff for one sheet across two versions:
    adds/removes, type/nullability/order changes, and the row-count delta.
    With include=profile, adds drift computed from persisted profile runs."""
    await ensure_dataset_permission(principal, dataset_id, Permission.DATASET_READ)
    # Profile drift's added/removed categories are verbatim cell values taken
    # from `top_values`, so the profile section is a raw read even though the
    # diff itself isn't. No-op for datasets that declare nothing sensitive.
    if _wants_profile(include):
        await ensure_raw_access(principal, dataset_id)
    return await sheet_diff(dataset_id, from_version, sheet_name, to_version,
                            include_profile=_wants_profile(include))


@router.post(
    "/datasets/{dataset_id}/versions/{from_version}/sheets/{sheet_name}/row-diff/{to_version}",
    response_model=RowDiffResponse, tags=["sheets"],
)
async def row_diff_sheet(
    dataset_id: str, from_version: int, sheet_name: str, to_version: int,
    body: RowDiffRequest | None = None,
    principal: Principal = Depends(get_principal),
) -> RowDiffResponse:
    """Which ROWS changed between two versions of a sheet, and to what.

    Rows are matched on a key — the sheet's declared primary key by default,
    or `key` in the body. Returns counts, a per-column "what moved" summary,
    and inline samples; the complete cell-level diff is written to a
    `diff_output` artifact you can fetch via `/samples/{diff_file}/data`.

    A key that is not unique in either version is a 409: the join would fan
    out and report rows as both added and removed.
    """
    ds = await ensure_dataset_permission(principal, dataset_id, Permission.DATASET_READ)
    body = body or RowDiffRequest()
    return RowDiffResponse(**await run_row_diff(
        ds, from_version, to_version, sheet_name,
        key_columns=body.key, compare_columns=body.columns,
        sample_limit=body.sample_limit, principal=principal))


@router.post(
    "/datasets/{dataset_id}/versions/{version_number}/confirm-rename",
    response_model=ConfirmRenameResponse, tags=["sheets"],
)
async def confirm_rename(
    dataset_id: str, version_number: int, body: ConfirmRenameRequest,
    principal: Principal = Depends(get_principal),
) -> ConfirmRenameResponse:
    """Confirm a sheet rename in this version: the renamed sheet keeps its
    logical identity, so sheet metadata and quality rules follow it instead of
    silently detaching. Pairs come from the diff's rename candidates; a
    non-candidate pair needs force=true."""
    await ensure_dataset_permission(principal, dataset_id, Permission.DATASET_WRITE)
    return await confirm_sheet_rename(
        dataset_id, version_number,
        from_sheet=body.from_sheet, to_sheet=body.to_sheet, force=body.force,
    )


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
async def set_tag(
    dataset_id: str, body: SetTagRequest, request: Request,
    principal: Principal = Depends(get_principal),
) -> TagInfo:
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

    tag_row = await repo.set_tag(
        dataset_id, version_id, body.tag_name,
        created_by=principal.user_id, actor_email=principal.email,
        version_number=ver["version_number"], request_id=_rid(request),
    )
    return TagInfo(
        tag_name=tag_row["tag_name"],
        version_id=tag_row["version_id"],
        version_number=ver["version_number"],
        created_at=tag_row["created_at"],
        updated_at=tag_row["updated_at"],
    )


async def _resolve_target_version(
    dataset_id: str, version_id: str | None, version_number: int | None,
) -> dict:
    """Resolve a promote target to a version row belonging to this dataset."""
    if version_id:
        ver = await repo.get_version(version_id)
        if not ver or str(ver["dataset_id"]) != dataset_id:
            raise HTTPException(404, f"Version {version_id} not found for dataset {dataset_id}")
    elif version_number is not None:
        ver = await repo.get_version_by_number(dataset_id, version_number)
        if not ver:
            raise HTTPException(404, f"Version {version_number} not found for dataset {dataset_id}")
    else:
        raise HTTPException(400, "Provide either version_id or version_number")
    return ver


@router.post("/datasets/{dataset_id}/tags/{tag_name}/promote", response_model=TagOpResponse, tags=["tags"])
async def promote_tag(
    dataset_id: str, tag_name: str, body: PromoteTagRequest, request: Request,
    principal: Principal = Depends(get_principal),
) -> TagOpResponse:
    """Promote a tag to a version, recording who did it and why.

    Unlike the raw PUT, promotion refuses versions that are not ``ready``.
    """
    ds = await ensure_dataset_permission(principal, dataset_id, Permission.DATASET_WRITE)
    tag_name = _norm_tag(tag_name)
    ver = await _resolve_target_version(dataset_id, body.version_id, body.version_number)
    if ver["status"] != "ready":
        raise HTTPException(409, f"Cannot promote to version {ver['version_number']} (status: {ver['status']})")

    # Promotion gate: if the dataset has enabled quality rules, the target
    # version needs a completed validation run with zero error-level failures.
    # (Raw PUT /tags stays ungated as the documented escape hatch; rollback is
    # the emergency path and is never gated.)
    from app.features.quality import repo as quality_repo
    if await quality_repo.count_enabled_rules(dataset_id) > 0:
        gate = await quality_repo.latest_completed_run(dataset_id, str(ver["id"]))
        if gate is None:
            raise ProblemException(
                409, f"Version {ver['version_number']} has not been validated — run "
                     f"POST /datasets/{dataset_id}/versions/{ver['version_number']}/validate first",
                code="validation-required",
            )
        if (gate["error_failures"] or 0) > 0:
            raise ProblemException(
                409, f"Version {ver['version_number']} failed validation: "
                     f"{gate['error_failures']} error-level failure(s)",
                code="validation-failed",
                validation_run_id=gate["id"],
                error_failures=gate["error_failures"],
                warning_failures=gate["warning_failures"],
            )

    tag_row = await repo.set_tag(
        dataset_id, str(ver["id"]), tag_name,
        created_by=principal.user_id, actor_email=principal.email,
        action="promote", reason=body.reason, version_number=ver["version_number"],
        request_id=_rid(request),
    )
    # Promotion is the moment downstream consumers care about most.
    await webhooks.emit(
        "tag.promoted", team_id=str(ds["team_id"]), dataset_id=dataset_id,
        data={"tag": tag_name,
              "from_version_number": tag_row["previous_version_number"],
              "to_version_number": ver["version_number"],
              "reason": body.reason})
    return TagOpResponse(
        tag_name=tag_name, action="promote",
        from_version_number=tag_row["previous_version_number"],
        to_version_number=ver["version_number"], reason=body.reason,
    )


@router.post("/datasets/{dataset_id}/tags/{tag_name}/rollback", response_model=TagOpResponse, tags=["tags"])
async def rollback_tag(
    dataset_id: str, tag_name: str, request: Request, body: RollbackTagRequest | None = None,
    principal: Principal = Depends(get_principal),
) -> TagOpResponse:
    """Move a tag back to the previous version it pointed at (from history)."""
    ds = await ensure_dataset_permission(principal, dataset_id, Permission.DATASET_WRITE)
    tag_name = _norm_tag(tag_name)
    current = await repo.get_version_by_tag(dataset_id, tag_name)
    if not current:
        raise HTTPException(404, f"Tag '{tag_name}' not found on dataset {dataset_id}")

    # Resolved in SQL: this used to scan the newest 100 history rows, so a tag
    # re-promoted to its current version more than 100 times reported "no
    # previous version" for a tag that had one.
    target_number = await repo.previous_tag_version_number(
        dataset_id, tag_name, current["version_number"])
    if target_number is None:
        raise HTTPException(409, f"No previous version in history for tag '{tag_name}'")
    ver = await repo.get_version_by_number(dataset_id, target_number)
    if not ver or ver["status"] != "ready":
        raise HTTPException(409, f"Previous version {target_number} is no longer available")

    reason = body.reason if body else None
    await repo.set_tag(
        dataset_id, str(ver["id"]), tag_name,
        created_by=principal.user_id, actor_email=principal.email,
        action="rollback", reason=reason, version_number=ver["version_number"],
        request_id=_rid(request),
    )
    # ``tag.rolled_back`` is a declared, subscribable event type. It was
    # accepted on subscribe and then never emitted, so a consumer that
    # subscribed to it to un-publish downstream artifacts simply never fired —
    # and a rollback is precisely the moment that matters most.
    await webhooks.emit(
        "tag.rolled_back", team_id=str(ds["team_id"]), dataset_id=dataset_id,
        data={"tag": tag_name,
              "from_version_number": current["version_number"],
              "to_version_number": ver["version_number"],
              "reason": reason})
    return TagOpResponse(
        tag_name=tag_name, action="rollback",
        from_version_number=current["version_number"],
        to_version_number=ver["version_number"], reason=reason,
    )


@router.get("/datasets/{dataset_id}/tags/{tag_name}/history", response_model=Page[TagHistoryEntry], tags=["tags"])
async def tag_history(
    dataset_id: str, tag_name: str,
    page: PageParams = Depends(pagination),
    principal: Principal = Depends(get_principal),
) -> Page[TagHistoryEntry]:
    """Full transition history for a tag (survives tag deletion), newest first."""
    await ensure_dataset_permission(principal, dataset_id, Permission.DATASET_READ)
    rows, total = await repo.list_tag_history(dataset_id, _norm_tag(tag_name), limit=page.limit, offset=page.offset)
    if total == 0:
        raise HTTPException(404, f"No history for tag '{tag_name}' on dataset {dataset_id}")
    return Page.of([TagHistoryEntry(**r) for r in rows], total, page)


@router.get("/datasets/{dataset_id}/tags/{tag_name}", response_model=VersionInfo, tags=["tags"])
async def resolve_tag(dataset_id: str, tag_name: str, principal: Principal = Depends(get_principal)) -> VersionInfo:
    """Resolve a tag to its version metadata.

    The payload is the same ``VersionInfo`` the versions list returns, field
    for field — it used to drop ``sheet_count``/``source_checksum``/
    ``manifest_checksum`` on the floor and answer null for all three, so a
    consumer that resolved 'production' and compared its content identity
    against the list got a mismatch for the very same version.
    """
    await ensure_dataset_permission(principal, dataset_id, Permission.DATASET_READ)
    tag_name = _norm_tag(tag_name)
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
        sheet_count=ver.get("sheet_count"),
        checksum=ver.get("checksum"),
        source_checksum=ver.get("source_checksum"),
        manifest_checksum=ver.get("manifest_checksum"),
        created_at=str(ver["created_at"]),
        processed_at=str(ver["processed_at"]) if ver.get("processed_at") else None,
        tags=tags,
    )


@router.delete("/datasets/{dataset_id}/tags/{tag_name}", response_model=DeleteResponse, tags=["tags"])
async def delete_tag(
    dataset_id: str, tag_name: str, request: Request,
    principal: Principal = Depends(get_principal),
) -> DeleteResponse:
    """Remove a tag from a dataset."""
    await ensure_dataset_permission(principal, dataset_id, Permission.DATASET_WRITE)
    tag_name = _norm_tag(tag_name)
    deleted = await repo.delete_tag(
        dataset_id, tag_name,
        actor_user_id=principal.user_id, actor_email=principal.email,
        request_id=_rid(request),
    )
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


@router.get("/datasets/{dataset_id}/versions/{version_number}/sheets",
            response_model=Page[SheetMetadataResponse], tags=["sheets"])
async def list_version_sheets(
    dataset_id: str, version_number: int,
    principal: Principal = Depends(get_principal),
) -> Page[SheetMetadataResponse]:
    """List the sheets of ONE version, with full metadata.

    ``GET /datasets/{id}/sheets`` always answers for the dataset's *current*
    version. That is the wrong version whenever a caller pinned one, or a tag
    rollback moved the current pointer backwards — and a consumer that offered
    those sheet keys as "the tables in this version" was describing a schema
    the query would not see. This route is version-scoped, so the answer
    matches whatever version the caller is actually reading.
    """
    await ensure_dataset_permission(principal, dataset_id, Permission.DATASET_READ)
    ver = await resolve_version(dataset_id, version_number=version_number)
    return _collection(await get_version_sheets(ver))


@router.get("/datasets/{dataset_id}/sheets/{sheet_name}", response_model=SheetMetadataResponse, tags=["sheets"])
async def get_sheet(dataset_id: str, sheet_name: str, principal: Principal = Depends(get_principal)) -> SheetMetadataResponse:
    """Return metadata for a single sheet, including a preview.

    Sensitive columns are masked in the preview for callers without elevated
    access; `masked_columns` says which.
    """
    await ensure_dataset_permission(principal, dataset_id, Permission.DATASET_READ)
    return await get_sheet_metadata(dataset_id, sheet_name, principal)


# ---------------------------------------------------------------------------
# Analytics — sampling, profiling, aggregation
# ---------------------------------------------------------------------------

@router.post("/sample", response_model=SampleResponse, tags=["analytics"])
async def sample_data(request: SampleRequest, principal: Principal = Depends(get_principal)) -> SampleResponse:
    """Goal-oriented data sampling over a dataset, file, or inline data."""
    ds = await _authorize_source(principal, request)
    resp = await run_sampling_pipeline(
        request, _output_layout(principal, ds, "sample_output"))
    await _register_output_artifacts(
        principal, ds, "sample_output", [resp.sample_file])
    return resp


@router.post("/sample/coordinated", response_model=CoordinatedSampleResponse, tags=["analytics"])
async def sample_coordinated(
    request: CoordinatedSampleRequest, principal: Principal = Depends(get_principal),
) -> CoordinatedSampleResponse:
    """Sample a driver sheet, then filter related sheets by key (consistent slice)."""
    ds = await _authorize_source(principal, request)
    resp = await run_coordinated_sampling(
        request, _output_layout(principal, ds, "sample_output"))
    await _register_output_artifacts(
        principal, ds, "sample_output",
        [resp.driver.sample_file] + [r.sample_file for r in resp.related])
    return resp


@router.post("/profile", response_model=ProfileResponse, tags=["analytics"])
async def profile_data(request: ProfileRequest, principal: Principal = Depends(get_principal)) -> ProfileResponse:
    """Profile data columns — statistics, distributions, data quality.

    On a dataset that declares a sensitive column this **refuses** a caller
    without `dataset:read_sensitive` (403 `sensitive-data-restricted`, via
    `_authorize_source`), because the profile it returns is the raw one: the
    caller chooses the columns and gets `top_values`, extremes and quantiles
    straight back, with no per-column policy applied on the way out.

    `POST /datasets/{id}/versions/{v}/profile-runs` deliberately answers the
    same caller instead of refusing: it persists a run and every read of that
    run is redacted per-principal. The asymmetry is "refuse the raw read, allow
    the redacted one", not an oversight — see that handler for why gating it
    too would cost more than it protects. If this endpoint is ever made to
    answer a redacted profile as well, the refusal is what should go, and the
    UI copy that says profiling is refused rather than masked goes with it.
    """
    await _authorize_source(principal, request)
    return await run_profiling(request)


@router.post("/pivot", response_model=PivotResponse, tags=["analytics"])
async def pivot_data(request: PivotRequest, principal: Principal = Depends(get_principal)) -> PivotResponse:
    """Pivot data: row dims × one pivot dim × value aggregations, with
    percentage displays and re-aggregated totals."""
    ds = await _authorize_source(principal, request)
    resp = await run_pivot(
        request, _output_layout(principal, ds, "pivot_output"))
    await _register_output_artifacts(
        principal, ds, "pivot_output", [resp.result_file])
    return resp


@router.post("/aggregate", response_model=AggregateResponse, tags=["analytics"])
async def aggregate_data(request: AggregateRequest, principal: Principal = Depends(get_principal)) -> AggregateResponse:
    """Aggregate data with group-by, sort, and optional filtering."""
    ds = await _authorize_source(principal, request)
    resp = await run_aggregation(
        request, _output_layout(principal, ds, "aggregation_output"))
    await _register_output_artifacts(
        principal, ds, "aggregation_output", [resp.result_file])
    return resp
