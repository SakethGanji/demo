"""Discovery API — column search, facets, favorites, sheet metadata, usage.

Registered BEFORE the data_accelerator router so the static ``/datasets/facets``
path wins over the ``/datasets/{dataset_id}`` item route.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.api.errors import ProblemException
from app.api.pagination import Page, PageParams, pagination
from app.features.auth.deps import Principal, ensure_dataset_permission, get_principal
from app.features.auth.permissions import Permission
from app.shared.data_io import normalize_column_names, normalize_sheet_key
from app.shared.datasets import (
    ensure_sheet_schema,
    get_version_sheet_rows,
    resolve_version,
)
from app.shared.query.validate import _resolve as resolve_schema_column

from . import repo
from .health import DatasetHealthResponse, dataset_health

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
    # §18 — signals shared with the /datasets/{id}/health read-model.
    validation_status: dict[str, int] = Field(
        default_factory=dict, description="passed | failed | none")
    has_schema_drift: dict[str, int] = Field(
        default_factory=dict, description="true | false")
    documentation: dict[str, int] = Field(
        default_factory=dict, description="full | partial | none")


class SheetMetadataIn(BaseModel):
    """Full sheet-metadata record — the PUT body, REPLACE semantics.

    PUT writes the whole record: **any field you omit is CLEARED (set to
    null)**, which is also the only way to remove a value you no longer want.
    To change some fields and leave the rest alone, use PATCH.
    """

    grain: str | None = Field(default=None, max_length=500,
                              description='e.g. "one row per customer per day"')
    primary_key_columns: list[str] | None = Field(
        default=None, description="Normalized column names forming the logical key")
    description: str | None = Field(default=None, max_length=2000)


class SheetMetadataPatch(SheetMetadataIn):
    """Partial sheet-metadata update — the PATCH body, MERGE semantics.

    Only the fields **present** in the request body are written; anything you
    omit keeps its stored value. Sending a field as an explicit ``null`` still
    clears it, so PATCH can do everything PUT can except blank the record by
    omission.
    """


class SheetMetadataOut(SheetMetadataIn):
    """A stored sheet-metadata record."""

    id: str
    dataset_id: str
    sheet_key: str
    logical_sheet_id: str | None = None
    updated_by: str | None = None
    updated_at: str


class ColumnMetadataIn(BaseModel):
    """Full data-dictionary entry — the PUT body, REPLACE semantics.

    PUT writes the whole entry: **any field you omit is CLEARED (set to
    null)**, which is also the only way to remove a value you no longer want.
    To change some fields and leave the rest alone, use PATCH.
    """

    business_name: str | None = Field(default=None, max_length=200)
    description: str | None = Field(default=None, max_length=2000)
    semantic_type: str | None = Field(
        default=None, max_length=100,
        description='e.g. "email", "currency_usd", "country_code"')
    unit: str | None = Field(default=None, max_length=50)
    sensitivity: str | None = Field(
        default=None, max_length=50,
        description='e.g. "public", "internal", "confidential", "pii"')
    allowed_values: list[Any] | None = Field(
        default=None, description="Enumerated valid values, when closed-set")


class ColumnMetadataPatch(ColumnMetadataIn):
    """Partial data-dictionary update — the PATCH body, MERGE semantics.

    Only the fields **present** in the request body are written; anything you
    omit keeps its stored value. Sending a field as an explicit ``null`` still
    clears it.
    """


class ColumnMetadataOut(ColumnMetadataIn):
    """A stored data-dictionary entry."""

    id: str
    dataset_id: str
    sheet_key: str
    logical_sheet_id: str
    column_name: str
    updated_by: str | None = None
    updated_at: str


class UsageResponse(BaseModel):
    dataset_id: str
    downloads: int
    writes: int
    total_events: int
    last_activity_at: str | None = None


class TimelineEvent(BaseModel):
    """One event in a dataset's merged history."""

    event_type: str = Field(description=(
        "version_created | tag_set/promote/rollback/delete | validation_run | "
        "profile_run | derived_from | published_to | audit"))
    occurred_at: str
    actor: str | None = Field(default=None, description="Actor email, when recorded")
    details: dict[str, Any] = Field(default_factory=dict)


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
    """Replace grain / primary key / description for a logical sheet (by sheet_key).

    **PUT is a whole-record replace: every field you omit from the body is
    CLEARED.** Omission is the documented way to blank a field. Creates the
    record if the sheet has none yet. Use ``PATCH`` on this same path to change
    a subset of the fields and leave the rest as they are.

    404 when the dataset has no such logical sheet — the same answer the column
    routes give for the same typo. This route used to accept ANY key and store
    a record with a null ``logical_sheet_id``: a misspelled sheet reported 200,
    the documentation went nowhere the sheet could ever be read from, and
    nothing could remove the record because there is no DELETE on this path.
    """
    await ensure_dataset_permission(principal, dataset_id, Permission.DATASET_WRITE)
    key = normalize_sheet_key(sheet_key)
    # Resolve the sheet BEFORE writing. A logical sheet is never retired, so
    # this still accepts a sheet that has since dropped out of the current
    # version — documenting a sheet you no longer load stays possible.
    if await repo.get_live_logical_sheet(dataset_id, key) is None:
        raise HTTPException(404, f"Sheet not found: {sheet_key}")
    row = await repo.upsert_sheet_metadata(
        dataset_id, key, body.model_dump(), updated_by=principal.user_id,
    )
    return SheetMetadataOut(**row)


@router.patch("/datasets/{dataset_id}/sheet-metadata/{sheet_key}",
              response_model=SheetMetadataOut, tags=["discovery"])
async def patch_sheet_metadata(
    dataset_id: str, sheet_key: str, body: SheetMetadataPatch,
    principal: Principal = Depends(get_principal),
) -> SheetMetadataOut:
    """Merge grain / primary key / description into a logical sheet's metadata.

    Only the fields **present** in the body are written; omitted fields keep
    their stored value. An explicit ``null`` still clears a field, so partial
    updates never destroy data by accident and clearing stays possible. An
    empty body is a no-op that returns the record unchanged.

    404 if the sheet has no metadata record yet — PATCH updates, it does not
    create; PUT does that. Same ``dataset:write`` permission as PUT.
    """
    await ensure_dataset_permission(principal, dataset_id, Permission.DATASET_WRITE)
    row = await repo.update_sheet_metadata(
        dataset_id, normalize_sheet_key(sheet_key),
        body.model_dump(exclude_unset=True), updated_by=principal.user_id,
    )
    if row is None:
        raise HTTPException(404, f"No metadata recorded for sheet: {sheet_key}")
    return SheetMetadataOut(**row)


@router.get("/datasets/{dataset_id}/sheet-metadata",
            response_model=Page[SheetMetadataOut], tags=["discovery"])
async def list_sheet_metadata(
    dataset_id: str,
    page: PageParams = Depends(pagination),
    principal: Principal = Depends(get_principal),
) -> Page[SheetMetadataOut]:
    """Semantic metadata recorded for this dataset's logical sheets.

    Paginated like every other list route: ``total`` is the full count, and
    ``limit``/``offset`` echo what was asked for.
    """
    await ensure_dataset_permission(principal, dataset_id, Permission.DATASET_READ)
    rows, total = await repo.page_sheet_metadata(
        dataset_id, limit=page.limit, offset=page.offset)
    return Page.of([SheetMetadataOut(**r) for r in rows], total, page)


@router.get("/datasets/{dataset_id}/sheet-metadata/{sheet_key}",
            response_model=SheetMetadataOut, tags=["discovery"])
async def get_sheet_metadata(
    dataset_id: str, sheet_key: str, principal: Principal = Depends(get_principal),
) -> SheetMetadataOut:
    """The semantic metadata recorded for ONE logical sheet.

    404 when the sheet has no record — same shape as PATCH, and the same
    normalization of ``sheet_key``, so any spelling that PUT accepts reads back
    the record it wrote instead of forcing a client to list and filter.
    """
    await ensure_dataset_permission(principal, dataset_id, Permission.DATASET_READ)
    row = await repo.get_sheet_metadata(dataset_id, normalize_sheet_key(sheet_key))
    if row is None:
        raise HTTPException(404, f"No metadata recorded for sheet: {sheet_key}")
    return SheetMetadataOut(**row)


async def _resolve_dictionary_target(
    dataset_id: str, sheet_key: str, column_name: str | None = None,
    *, require_current: bool = True,
) -> tuple[dict, str | None]:
    """(live logical sheet, normalized column name) for dictionary routes.

    Entries are keyed on the NORMALIZED column name, so every route that names
    a column has to map the path segment through this one helper — otherwise
    GET and DELETE disagree about a column's identity and an entry that is
    plainly listed cannot be removed.

    Column validation runs against the CURRENT version's schema — early
    feedback only, same philosophy as saved-view create. List routes pass
    ``column_name=None`` (no column to resolve).

    ``require_current=False`` is the GET/DELETE path: it still normalizes
    through the current schema when the column is there (so ``Business Name``
    and ``business_name`` address the same entry), and falls back to the SAME
    normalization rule ingest used when the column — or the sheet, or the
    version — is gone. Reading and deleting documentation for a column that no
    longer exists is exactly when you most want to, and the fallback used to
    pass the raw URL segment through: an entry written as ``Business Name``
    was stored (and listed) as ``business_name``, so the moment the column left
    the current schema the spelling that created it 404'd.
    """
    sheet = await repo.get_live_logical_sheet(dataset_id, normalize_sheet_key(sheet_key))
    if sheet is None:
        raise HTTPException(404, f"Sheet not found: {sheet_key}")
    if column_name is None:
        return sheet, None
    try:
        ver = await resolve_version(dataset_id)
        rows = await get_version_sheet_rows(ver)
        row = next((r for r in rows
                    if str(r.get("logical_sheet_id") or "") == sheet["id"]), None)
        if row is None:
            raise ProblemException(
                404, f"Sheet '{sheet_key}' is not present in the current version",
                code="sheet-not-in-version", version_number=ver["version_number"])
        row = await ensure_sheet_schema(ver, row)
        col = resolve_schema_column(column_name, row["schema_json"])
    except StarletteHTTPException:
        if require_current:
            raise
        # No schema to resolve against — normalize with the ingest rule, which
        # is what produced the stored key in the first place.
        return sheet, normalize_column_names([column_name])[0]
    return sheet, col.get("normalized_name") or col["name"]


@router.put("/datasets/{dataset_id}/sheet-metadata/{sheet_key}/columns/{column_name}",
            response_model=ColumnMetadataOut, tags=["discovery"])
async def put_column_metadata(
    dataset_id: str, sheet_key: str, column_name: str, body: ColumnMetadataIn,
    principal: Principal = Depends(get_principal),
) -> ColumnMetadataOut:
    """Replace the data-dictionary entry for one column of a logical sheet.

    The column must exist in the current version's schema (normalized or
    physical name); the entry is stored under the normalized name.

    **PUT is a whole-record replace: every field you omit from the body is
    CLEARED.** Omission is the documented way to blank a field. Creates the
    entry if the column has none yet. Use ``PATCH`` on this same path to change
    a subset of the fields and leave the rest as they are.
    """
    await ensure_dataset_permission(principal, dataset_id, Permission.DATASET_WRITE)
    sheet, normalized = await _resolve_dictionary_target(dataset_id, sheet_key, column_name)
    row = await repo.upsert_column_metadata(
        dataset_id, sheet["id"], normalized, body.model_dump(),
        updated_by=principal.user_id,
    )
    return ColumnMetadataOut(**row, sheet_key=sheet["current_sheet_key"])


@router.patch("/datasets/{dataset_id}/sheet-metadata/{sheet_key}/columns/{column_name}",
              response_model=ColumnMetadataOut, tags=["discovery"])
async def patch_column_metadata(
    dataset_id: str, sheet_key: str, column_name: str, body: ColumnMetadataPatch,
    principal: Principal = Depends(get_principal),
) -> ColumnMetadataOut:
    """Merge fields into one column's data-dictionary entry.

    Only the fields **present** in the body are written; omitted fields keep
    their stored value. An explicit ``null`` still clears a field. An empty
    body is a no-op that returns the entry unchanged.

    The column is resolved exactly as for PUT (must exist in the current
    version's schema). 404 if the column has no dictionary entry yet — PATCH
    updates, it does not create; PUT does that. Same ``dataset:write``
    permission as PUT.
    """
    await ensure_dataset_permission(principal, dataset_id, Permission.DATASET_WRITE)
    sheet, normalized = await _resolve_dictionary_target(dataset_id, sheet_key, column_name)
    row = await repo.update_column_metadata(
        sheet["id"], normalized, body.model_dump(exclude_unset=True),
        updated_by=principal.user_id,
    )
    if row is None:
        raise HTTPException(404, f"No dictionary entry for column: {column_name}")
    return ColumnMetadataOut(**row, sheet_key=sheet["current_sheet_key"])


@router.get("/datasets/{dataset_id}/sheet-metadata/{sheet_key}/columns",
            response_model=Page[ColumnMetadataOut], tags=["discovery"])
async def list_column_metadata(
    dataset_id: str, sheet_key: str,
    page: PageParams = Depends(pagination),
    principal: Principal = Depends(get_principal),
) -> Page[ColumnMetadataOut]:
    """The data dictionary recorded for a logical sheet's columns.

    Paginated like every other list route: ``total`` is the full count, and
    ``limit``/``offset`` echo what was asked for. A wide sheet's dictionary can
    run to hundreds of entries, so this is a real page, not a courtesy envelope.
    """
    await ensure_dataset_permission(principal, dataset_id, Permission.DATASET_READ)
    sheet, _ = await _resolve_dictionary_target(dataset_id, sheet_key)
    rows, total = await repo.page_column_metadata(
        sheet["id"], limit=page.limit, offset=page.offset)
    items = [ColumnMetadataOut(**r, sheet_key=sheet["current_sheet_key"]) for r in rows]
    return Page.of(items, total, page)


@router.get("/datasets/{dataset_id}/sheet-metadata/{sheet_key}/columns/{column_name}",
            response_model=ColumnMetadataOut, tags=["discovery"])
async def get_column_metadata(
    dataset_id: str, sheet_key: str, column_name: str,
    principal: Principal = Depends(get_principal),
) -> ColumnMetadataOut:
    """One column's data-dictionary entry.

    The column name is normalized exactly as PUT/PATCH/DELETE normalize it, so
    ``Business Name`` and ``business_name`` address the same entry and a client
    never has to list the whole dictionary and filter it client-side.

    Like DELETE — and unlike PUT/PATCH — this tolerates a column that has since
    disappeared from the current version's schema: the documentation you wrote
    is still readable after the column it describes is dropped, which is exactly
    when you want to look at it. 404 when no entry is recorded.
    """
    await ensure_dataset_permission(principal, dataset_id, Permission.DATASET_READ)
    sheet, normalized = await _resolve_dictionary_target(
        dataset_id, sheet_key, column_name, require_current=False)
    row = await repo.get_column_metadata(sheet["id"], normalized)
    if row is None:
        raise HTTPException(404, f"No dictionary entry for column: {column_name}")
    return ColumnMetadataOut(**row, sheet_key=sheet["current_sheet_key"])


@router.delete("/datasets/{dataset_id}/sheet-metadata/{sheet_key}/columns/{column_name}",
               status_code=204, tags=["discovery"])
async def delete_column_metadata(
    dataset_id: str, sheet_key: str, column_name: str,
    principal: Principal = Depends(get_principal),
) -> None:
    """Remove a column's dictionary entry (works for since-removed columns).

    The column is normalized exactly as PUT/PATCH/GET normalize it, so any
    spelling those accept deletes the entry they created. It used to pass the
    raw path segment straight to the repo, which 404'd on every column whose
    URL form differs from its stored (normalized) form — ``Business Name``
    could be written and listed but never deleted.
    """
    await ensure_dataset_permission(principal, dataset_id, Permission.DATASET_WRITE)
    sheet, normalized = await _resolve_dictionary_target(
        dataset_id, sheet_key, column_name, require_current=False)
    if not await repo.delete_column_metadata(sheet["id"], normalized):
        raise HTTPException(404, f"No dictionary entry for column: {column_name}")


@router.get("/datasets/{dataset_id}/timeline",
            response_model=Page[TimelineEvent], tags=["discovery"])
async def dataset_timeline(
    dataset_id: str,
    page: PageParams = Depends(pagination),
    principal: Principal = Depends(get_principal),
) -> Page[TimelineEvent]:
    """The dataset's merged history, newest first: version uploads, tag
    changes (actor + reason + request_id), validation and profile runs,
    lineage in both directions, and audited write requests. Reads are usage
    (GET .../usage), not history."""
    await ensure_dataset_permission(principal, dataset_id, Permission.DATASET_READ)
    rows, total = await repo.dataset_timeline(dataset_id, limit=page.limit,
                                              offset=page.offset)
    return Page.of([TimelineEvent(**r) for r in rows], total, page)


@router.get("/datasets/{dataset_id}/health",
            response_model=DatasetHealthResponse, tags=["discovery"])
async def get_dataset_health(
    dataset_id: str, principal: Principal = Depends(get_principal),
) -> DatasetHealthResponse:
    """Multi-dimension health read-model — schema stability, validation,
    missing data, duplicates, drift, freshness, documentation. Each dimension
    carries evidence pointers; there is deliberately no aggregate score."""
    ds = await ensure_dataset_permission(principal, dataset_id, Permission.DATASET_READ)
    return await dataset_health(ds)


@router.get("/datasets/{dataset_id}/usage", response_model=UsageResponse, tags=["discovery"])
async def dataset_usage(
    dataset_id: str, principal: Principal = Depends(get_principal),
) -> UsageResponse:
    """Usage counts for a dataset, derived from the audit trail."""
    await ensure_dataset_permission(principal, dataset_id, Permission.DATASET_READ)
    stats: dict[str, Any] = await repo.dataset_usage(dataset_id)
    return UsageResponse(dataset_id=dataset_id, **stats)
