"""Relationships API — seed, discover, review (ROADMAP §22).

Edges hang off their owning dataset, so RBAC is the usual
``ensure_dataset_permission`` pattern. A cross-dataset edge additionally
requires permission on the TARGET dataset, otherwise a relationship would be a
side channel for learning that another team's dataset exists.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query

from app.api.pagination import Page, PageParams, pagination
from app.features.auth.deps import Principal, ensure_dataset_permission, get_principal
from app.features.auth.permissions import Permission
from app.shared.masking import ensure_raw_access
from app.shared.datasets import resolve_version

from app.features.library import repo as library_repo
from app.features.library.schemas import PublishResponse

from . import joins, repo, service
from .schemas import (
    JoinBuildSpec,
    JoinExecuteResponse,
    JoinPreview,
    JoinPublishRequest,
    RelationshipCreate,
    RelationshipOut,
    SeedResponse,
    SuggestResponse,
)

router = APIRouter()


def _visible_team_ids(principal: Principal) -> list[str] | None:
    """Teams whose datasets this principal may read, or None for "all".

    Used to keep a cross-dataset edge out of a listing when the caller cannot
    read the dataset it points at — the same rule
    ``ensure_dataset_permission`` applies, expressed as a filter so the page
    and its ``total`` agree.
    """
    if principal.is_superuser:
        return None
    return [t for t in principal.memberships
            if principal.can(t, Permission.DATASET_READ)]


async def _can_read_dataset(principal: Principal, dataset_id: str) -> bool:
    """Whether the principal may read *dataset_id*, without raising.

    Deliberately not ``ensure_dataset_permission``: its 404/403 both name the
    dataset id in the detail, and here that id is exactly the thing being
    protected — the caller never supplied it, they got it off a relationship.
    """
    from app.shared.repo import get_dataset

    ds = await get_dataset(dataset_id)
    return bool(ds and principal.can(str(ds["team_id"]), Permission.DATASET_READ))


async def _relationship_or_404(dataset_id: str, relationship_id: str,
                               principal: Principal) -> dict:
    """Load an edge owned by *dataset_id*, hiding it if its TARGET is off-limits.

    A relationship row carries the target dataset's id and current sheet key,
    so returning one whose target the caller cannot read would make an edge a
    side channel for learning that another team's dataset exists — the leak
    ``_authorized_relationship`` already prevents on the join routes.
    """
    row = await repo.get_relationship(relationship_id)
    if not row or row["dataset_id"] != dataset_id:
        raise HTTPException(404, f"Relationship not found: {relationship_id}")
    if (row["to_dataset_id"] != dataset_id
            and not await _can_read_dataset(principal, row["to_dataset_id"])):
        raise HTTPException(404, f"Relationship not found: {relationship_id}")
    return row


@router.post("/datasets/{dataset_id}/relationships/seed",
             response_model=SeedResponse, tags=["relationships"])
async def seed_relationships(
    dataset_id: str, principal: Principal = Depends(get_principal),
) -> SeedResponse:
    """Turn this dataset's enabled `foreign_key` quality rules into edges.

    Idempotent: re-seeding refreshes evidence but never overrides a status a
    human already set.
    """
    ds = await ensure_dataset_permission(principal, dataset_id, Permission.DATASET_WRITE)
    seeded = await service.seed_from_fk_rules(ds, principal.user_id)
    # `created` counts newly-inserted edges only; a re-seed that merely refreshed
    # existing edges reports 0, so a "Created N relationships" toast is truthful.
    return SeedResponse(created=sum(1 for r in seeded if r.get("inserted")),
                        relationships=[RelationshipOut(**r) for r in seeded])


@router.post("/datasets/{dataset_id}/relationships/suggest",
             response_model=SuggestResponse, tags=["relationships"])
async def suggest_relationships(
    dataset_id: str,
    sync: bool = Query(default=True,
                       description="Run in-request; false enqueues for the worker"),
    principal: Principal = Depends(get_principal),
) -> SuggestResponse:
    """Probe the current version for undeclared relationships.

    Suggestions are never applied — they land as `suggested` for review.
    Either mode returns a `job_id`: with `sync=false` it is the only handle the
    caller gets on work that has not run yet.
    """
    ds = await ensure_dataset_permission(principal, dataset_id, Permission.DATASET_WRITE)
    result = await service.dispatch_discovery(
        dataset_id=dataset_id, team_id=str(ds["team_id"]),
        triggered_by=principal.user_id, inline=sync)
    rows, _ = await repo.list_relationships(
        dataset_id, status="suggested", limit=100, offset=0,
        visible_team_ids=_visible_team_ids(principal))
    return SuggestResponse(
        job_id=result.get("job_id"),
        pairs_examined=result.get("pairs_examined", 0),
        suggested=result.get("suggested", 0),
        skipped=result.get("skipped", 0),
        relationships=[RelationshipOut(**r) for r in rows])


@router.post("/datasets/{dataset_id}/relationships", response_model=RelationshipOut,
             status_code=201, tags=["relationships"])
async def create_relationship(
    dataset_id: str, body: RelationshipCreate,
    principal: Principal = Depends(get_principal),
) -> RelationshipOut:
    """Declare a relationship by hand. Both endpoints are validated against the
    current schemas, and the target dataset needs read permission too."""
    ds = await ensure_dataset_permission(principal, dataset_id, Permission.DATASET_WRITE)
    to_dataset_id = body.to_dataset_id or dataset_id
    if to_dataset_id != dataset_id:
        await ensure_dataset_permission(principal, to_dataset_id, Permission.DATASET_READ)

    from_ver = await resolve_version(dataset_id)
    from_sheet = await service.resolve_sheet(from_ver, body.from_sheet)
    to_ver = (from_ver if to_dataset_id == dataset_id
              else await resolve_version(to_dataset_id))
    to_sheet = await service.resolve_sheet(to_ver, body.to_sheet)

    row = await repo.upsert_relationship(
        dataset_id=dataset_id,
        from_logical_sheet_id=str(from_sheet["logical_sheet_id"]),
        from_column=service.normalized_column(from_sheet, body.from_column),
        to_dataset_id=to_dataset_id,
        to_logical_sheet_id=str(to_sheet["logical_sheet_id"]),
        to_column=service.normalized_column(to_sheet, body.to_column),
        method="manual", evidence={"declared_by": principal.user_id},
        confidence=1.0, created_by=principal.user_id)
    if body.confirmed and row["status"] == "suggested":
        row = await repo.set_status(row["id"], "confirmed", principal.user_id)
    return RelationshipOut(**row)


@router.get("/datasets/{dataset_id}/relationships",
            response_model=Page[RelationshipOut], tags=["relationships"])
async def list_relationships(
    dataset_id: str,
    status: str | None = Query(default=None,
                               pattern="^(suggested|confirmed|rejected)$"),
    page: PageParams = Depends(pagination),
    principal: Principal = Depends(get_principal),
) -> Page[RelationshipOut]:
    """Relationships owned by this dataset, most confident first.

    An edge pointing at a dataset you cannot read is omitted entirely (see
    :func:`_relationship_or_404`), and omitted from ``total`` with it.
    """
    await ensure_dataset_permission(principal, dataset_id, Permission.DATASET_READ)
    rows, total = await repo.list_relationships(
        dataset_id, status=status, limit=page.limit, offset=page.offset,
        visible_team_ids=_visible_team_ids(principal))
    return Page.of([RelationshipOut(**r) for r in rows], total, page)


@router.get("/datasets/{dataset_id}/relationships/{relationship_id}",
            response_model=RelationshipOut, tags=["relationships"])
async def get_relationship(
    dataset_id: str, relationship_id: str,
    principal: Principal = Depends(get_principal),
) -> RelationshipOut:
    """One relationship, with the evidence behind it."""
    await ensure_dataset_permission(principal, dataset_id, Permission.DATASET_READ)
    return RelationshipOut(
        **await _relationship_or_404(dataset_id, relationship_id, principal))


@router.post("/datasets/{dataset_id}/relationships/{relationship_id}/confirm",
             response_model=RelationshipOut, tags=["relationships"])
async def confirm_relationship(
    dataset_id: str, relationship_id: str,
    principal: Principal = Depends(get_principal),
) -> RelationshipOut:
    """Accept a relationship. Only confirmed edges can drive joins (§23)."""
    await ensure_dataset_permission(principal, dataset_id, Permission.DATASET_WRITE)
    row = await _relationship_or_404(dataset_id, relationship_id, principal)
    return RelationshipOut(**await service.review(row, "confirmed", principal))


@router.post("/datasets/{dataset_id}/relationships/{relationship_id}/reject",
             response_model=RelationshipOut, tags=["relationships"])
async def reject_relationship(
    dataset_id: str, relationship_id: str,
    principal: Principal = Depends(get_principal),
) -> RelationshipOut:
    """Turn a relationship down. Re-running discovery will not resurrect it."""
    await ensure_dataset_permission(principal, dataset_id, Permission.DATASET_WRITE)
    row = await _relationship_or_404(dataset_id, relationship_id, principal)
    return RelationshipOut(**await service.review(row, "rejected", principal))


@router.delete("/datasets/{dataset_id}/relationships/{relationship_id}",
               status_code=204, tags=["relationships"])
async def delete_relationship(
    dataset_id: str, relationship_id: str,
    principal: Principal = Depends(get_principal),
) -> None:
    """Remove a relationship entirely.

    Refused with 409 ``relationship-has-dependents`` while a saved `join`
    definition still names it — nothing links the two rows, so deleting the
    edge would leave that definition listed and unusable.
    """
    await ensure_dataset_permission(principal, dataset_id, Permission.DATASET_WRITE)
    row = await repo.get_relationship(relationship_id)
    if not row or row["dataset_id"] != dataset_id:
        raise HTTPException(404, f"Relationship not found: {relationship_id}")
    await service.ensure_no_join_definitions(dataset_id, relationship_id)
    if not await repo.delete_relationship(dataset_id, relationship_id):
        raise HTTPException(404, f"Relationship not found: {relationship_id}")


# ---------------------------------------------------------------------------
# §23 Join builder — team-scoped, because a join has two datasets and no
# single owner. This is the ONLY place cross-dataset joins are allowed.
# ---------------------------------------------------------------------------

async def _authorized_relationship(relationship_id: str, principal: Principal,
                                   permission: Permission) -> tuple[dict, dict]:
    """Load a relationship and authorize BOTH of its datasets.

    Checking only the owning side would make a relationship a side channel for
    reading — or learning the existence of — another team's dataset.
    """
    relationship = await repo.get_relationship(relationship_id)
    if not relationship:
        raise HTTPException(404, f"Relationship not found: {relationship_id}")
    left = await ensure_dataset_permission(
        principal, relationship["dataset_id"], permission)
    if relationship["to_dataset_id"] != relationship["dataset_id"]:
        await ensure_dataset_permission(
            principal, relationship["to_dataset_id"], Permission.DATASET_READ)
    return relationship, left


@router.post("/joins/preview", response_model=JoinPreview, tags=["joins"])
async def preview_join(
    spec: JoinBuildSpec, principal: Principal = Depends(get_principal),
) -> JoinPreview:
    """Measure what a join would do — duplicate keys, row expansion, unmatched
    rates, column collisions — plus a five-row sample. Persists nothing."""
    relationship, _ = await _authorized_relationship(
        spec.relationship_id, principal, Permission.DATASET_READ)
    # The sample rows are real joined data from BOTH sides, so both must clear
    # the raw-read gate. Nothing here masks, and a join output can carry either
    # dataset's sensitive columns under a collision alias — refuse, as
    # /aggregate and /sql do, rather than render values this seat can't see.
    await ensure_raw_access(principal, str(relationship["dataset_id"]))
    if str(relationship["to_dataset_id"]) != str(relationship["dataset_id"]):
        await ensure_raw_access(principal, str(relationship["to_dataset_id"]))
    return await joins.preview_join(relationship, spec)


@router.post("/joins/execute", response_model=JoinExecuteResponse, tags=["joins"])
async def execute_join(
    spec: JoinBuildSpec, principal: Principal = Depends(get_principal),
) -> JoinExecuteResponse:
    """Run the join and store the result as a `join_output` artifact.

    Requires a CONFIRMED relationship — an unreviewed edge is a 409.

    Unlike ``/joins/preview``, this is a WRITE on the owning dataset: it
    creates an analytics definition, a job, a run and a parquet artifact that
    all outlive the request. So the LEFT side needs ``DATASET_WRITE``; the
    right side stays at ``DATASET_READ`` inside ``_authorized_relationship``,
    because nothing is written there.
    """
    relationship, _ = await _authorized_relationship(
        spec.relationship_id, principal, Permission.DATASET_WRITE)
    result = await joins.execute_join(relationship, spec, principal)
    return JoinExecuteResponse(
        run_id=result["run"]["id"],
        sample_file=result["summary"]["sample_file"],
        row_count=result["summary"]["row_count"],
        warnings=result["warnings"],
        output_columns=result["output_columns"],
        relationship=RelationshipOut(**relationship))


@router.post("/joins/{run_id}/publish", response_model=PublishResponse, tags=["joins"])
async def publish_join(
    run_id: str, body: JoinPublishRequest,
    principal: Principal = Depends(get_principal),
) -> PublishResponse:
    """Publish a join output as a new dataset (or a new version of one side).

    Both parents are recorded in lineage, so a joined dataset can always be
    traced back to each source.
    """
    run = await library_repo.get_run(run_id)
    if not run or run.get("kind") != "join":
        raise HTTPException(404, f"Join run not found: {run_id}")
    relationship_id = (run.get("result_summary") or {}).get("relationship_id")
    if not relationship_id:
        raise HTTPException(409, "This run did not record the relationship it used")
    relationship, _ = await _authorized_relationship(
        relationship_id, principal, Permission.DATASET_READ)

    target_id = body.dataset_id or relationship["dataset_id"]
    target_ds = await ensure_dataset_permission(
        principal, target_id, Permission.DATASET_WRITE)
    result = await joins.publish_join(
        run, relationship, target_ds, mode=body.mode, name=body.name,
        principal=principal)
    return PublishResponse(**result)
