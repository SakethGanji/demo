"""Quality API — rule CRUD and validation runs.

Routes live under the dataset tree so RBAC follows the standard pattern:
reads need dataset:read, mutations dataset:write, and cross-team requests
404 (existence hidden). Validation runs synchronously (rules compile to
single DuckDB queries) with a jobs row as the execution record.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.exc import IntegrityError
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.api.errors import ProblemException
from app.api.pagination import Page, PageParams, pagination
from app.features.auth.deps import Principal, ensure_dataset_permission, get_principal
from app.features.auth.permissions import Permission
from app.features.webhooks import service as webhooks
from app.infra.db.storage import ArtifactLayout, get_storage
from app.shared import jobs
from app.shared.datasets import ensure_sheet_schema, get_version_sheet_rows, resolve_version
from app.shared.repo import get_live_dataset_sheet

from . import repo
from .engine import evaluate_rule
from .schemas import (
    RuleCreate,
    RuleOut,
    RuleResultOut,
    RuleUpdate,
    ValidationDetail,
    ValidationRunOut,
    check_rule_shape,
)

logger = logging.getLogger("analytics.quality")

router = APIRouter()


# Postgres SQLSTATE for unique_violation. quality_rules' only unique index
# besides the primary key is (dataset_id, name), and ``id`` is not mutable, so
# a 23505 from an UPDATE here is always the rule-name collision.
_UNIQUE_VIOLATION = "23505"


def _is_name_collision(exc: IntegrityError) -> bool:
    """True only for the (dataset_id, name) unique violation.

    ``IntegrityError`` is the DBAPI class for every constraint failure, so the
    SQLSTATE is what distinguishes "that name is taken" from a NOT NULL or
    CHECK violation that the caller cannot fix by picking another name.
    """
    return getattr(exc.orig, "sqlstate", None) == _UNIQUE_VIOLATION


async def _resolve_sheet_selector(dataset_id: str, fields: dict) -> dict:
    """Pin a sheet_selector to its live logical sheet when one exists.

    The selector text stays authoritative for display and for sheets that
    don't exist yet; the logical id is what makes the rule follow a
    confirmed rename.

    Keyed off *presence* of ``sheet_selector``, not its truthiness, and it
    always writes ``logical_sheet_id`` — including ``None``. Anything a caller
    re-targets has to re-pin: the engine resolves the logical id FIRST
    (``engine._find_sheet``), so leaving a stale id behind after a PATCH would
    silently keep validating the *old* sheet while the API reported the new
    selector. An unresolvable selector means "no pin", which is exactly what
    create already stores for a sheet that does not exist yet.
    """
    if "sheet_selector" in fields:
        selector = fields["sheet_selector"]
        ls = await get_live_dataset_sheet(dataset_id, selector) if selector else None
        fields["logical_sheet_id"] = ls["id"] if ls else None
        if ls:
            fields["sheet_selector"] = ls["current_sheet_key"]
    return fields


# ---------------------------------------------------------------------------
# Rules CRUD
# ---------------------------------------------------------------------------

@router.post("/datasets/{dataset_id}/rules", response_model=RuleOut, status_code=201, tags=["quality"])
async def create_rule(
    dataset_id: str, body: RuleCreate, principal: Principal = Depends(get_principal),
) -> RuleOut:
    """Create a quality rule for a dataset."""
    await ensure_dataset_permission(principal, dataset_id, Permission.DATASET_WRITE)
    row = await repo.create_rule(
        dataset_id,
        await _resolve_sheet_selector(dataset_id, {**body.model_dump(), "scope_type": body.scope_type}),
        created_by=principal.user_id,
    )
    if not row:
        raise HTTPException(409, f"A rule named '{body.name}' already exists on this dataset")
    return RuleOut(**row)


@router.get("/datasets/{dataset_id}/rules", response_model=Page[RuleOut], tags=["quality"])
async def list_rules(dataset_id: str, principal: Principal = Depends(get_principal)) -> Page[RuleOut]:
    """List all quality rules for a dataset.

    Unpaginated — a dataset's rule set is small and the screen renders it whole
    — but it still answers in the shared ``Page`` envelope. ``limit`` is
    floored at 1 rather than left as ``len(items)``: the empty rule set is the
    first thing every new dataset shows, and ``limit: 0`` is both a division by
    zero for a pager computing ``ceil(total / limit)`` and a value this
    service's own ``pagination`` dependency rejects as input (``ge=1``).
    """
    await ensure_dataset_permission(principal, dataset_id, Permission.DATASET_READ)
    rows = await repo.list_rules(dataset_id)
    items = [RuleOut(**r) for r in rows]
    return Page(items=items, total=len(items), limit=max(len(items), 1), offset=0)


@router.get("/datasets/{dataset_id}/rules/{rule_id}", response_model=RuleOut, tags=["quality"])
async def get_rule(
    dataset_id: str, rule_id: str, principal: Principal = Depends(get_principal),
) -> RuleOut:
    """Fetch one rule. Rules on another dataset 404 (existence hidden)."""
    await ensure_dataset_permission(principal, dataset_id, Permission.DATASET_READ)
    row = await repo.get_rule(dataset_id, rule_id)
    if not row:
        raise HTTPException(404, f"Rule not found: {rule_id}")
    return RuleOut(**row)


@router.patch("/datasets/{dataset_id}/rules/{rule_id}", response_model=RuleOut, tags=["quality"])
async def update_rule(
    dataset_id: str, rule_id: str, body: RuleUpdate,
    principal: Principal = Depends(get_principal),
) -> RuleOut:
    """Update a rule (selectors, parameters, severity, enabled).

    The patch is merged over the stored rule and re-checked against the SAME
    invariants create enforces. Without that, an edit could persist a shape
    create rejects — e.g. clearing a foreign_key rule's parameters — and the
    only symptom would be a per-rule ``error`` on the next validation run,
    long after the request that caused it returned 200.
    """
    await ensure_dataset_permission(principal, dataset_id, Permission.DATASET_WRITE)
    existing = await repo.get_rule(dataset_id, rule_id)
    if not existing:
        raise HTTPException(404, f"Rule not found: {rule_id}")

    fields = body.model_dump(exclude_unset=True)
    merged = {**existing, **fields}
    try:
        check_rule_shape(merged["rule_type"], merged.get("sheet_selector"),
                         merged.get("column_selector"), merged.get("parameters"))
    except ValueError as e:
        raise ProblemException(422, str(e), code="invalid-rule-shape") from e

    try:
        row = await repo.update_rule(
            dataset_id, rule_id, await _resolve_sheet_selector(dataset_id, fields),
        )
    except IntegrityError as e:
        # Same collision POST reports as a 409; the unique index is on
        # (dataset_id, name), so a rename onto a sibling must not escape as 500.
        #
        # Narrowed to the unique violation on purpose. IntegrityError also
        # covers NOT NULL and CHECK failures on this table, and reporting one of
        # those as a name collision names a rule that does not exist and blames
        # a field the caller never sent. Anything else keeps its own shape.
        if not _is_name_collision(e):
            raise
        taken = fields.get("name", existing["name"])
        raise HTTPException(
            409, f"A rule named '{taken}' already exists on this dataset") from e
    if not row:
        raise HTTPException(404, f"Rule not found: {rule_id}")
    return RuleOut(**row)


@router.delete("/datasets/{dataset_id}/rules/{rule_id}", status_code=204, tags=["quality"])
async def delete_rule(
    dataset_id: str, rule_id: str, principal: Principal = Depends(get_principal),
) -> None:
    """Delete a rule (past validation results keep their snapshot of it)."""
    await ensure_dataset_permission(principal, dataset_id, Permission.DATASET_WRITE)
    if not await repo.delete_rule(dataset_id, rule_id):
        raise HTTPException(404, f"Rule not found: {rule_id}")


# ---------------------------------------------------------------------------
# Validation runs
# ---------------------------------------------------------------------------

@router.post(
    "/datasets/{dataset_id}/versions/{version_number}/validate",
    response_model=ValidationDetail, tags=["quality"],
)
async def validate_version(
    dataset_id: str, version_number: int,
    principal: Principal = Depends(get_principal),
) -> ValidationDetail:
    """Run all enabled rules against a version and persist the results."""
    ds = await ensure_dataset_permission(principal, dataset_id, Permission.DATASET_WRITE)
    ver = await resolve_version(dataset_id, version_number=version_number)
    if ver["status"] != "ready":
        raise HTTPException(409, f"Version {version_number} is not ready (status: {ver['status']})")

    rules = await repo.list_rules(dataset_id, enabled_only=True)
    if not rules:
        raise HTTPException(400, "Dataset has no enabled quality rules")

    job = await jobs.create_job(
        "validation", dataset_id=dataset_id, dataset_version_id=str(ver["id"]),
        team_id=str(ds["team_id"]), parameters={"rules": len(rules)},
    )
    await jobs.start_job(str(job["id"]))
    run = await repo.create_run(dataset_id, str(ver["id"]), str(job["id"]), principal.user_id)

    try:
        sheets = [await ensure_sheet_schema(ver, r) for r in await get_version_sheet_rows(ver)]
        # One layout object, used both to WRITE the failure parquet and to
        # REGISTER it. The storage key is not derivable from the filename, so
        # building a second layout from a differently-spelled dataset id (the
        # raw path parameter vs the canonical uuid) would register a key that
        # points at nothing and orphan the file that was actually written.
        failures = ArtifactLayout("validation_failures",
                                  team_id=str(ds["team_id"]),
                                  dataset_id=str(ds["id"]))
        results = [evaluate_rule(rule, ver, sheets, failures)
                   for rule in rules]
        # Failing rows were written to the object store by the engine; register
        # each file so /samples authorization applies and it can be published.
        await _register_failure_artifacts(results, ds, principal.user_id, failures)
        run = await repo.complete_run(run["id"], results)
        await jobs.complete_job(str(job["id"]), result={
            "validation_run_id": run["id"],
            "rules_total": run["rules_total"],
            "rules_failed": run["rules_failed"],
            "error_failures": run["error_failures"],
        })
    except Exception as e:
        await repo.fail_run(run["id"], str(e))
        await jobs.fail_job(str(job["id"]), str(e))
        # The bookkeeping above is unconditional, but the STATUS is not ours to
        # invent: a 404 raised in here ("Sheet data unreadable", a version that
        # vanished) is a precise answer the caller can act on, and rewriting it
        # to 500 tells them to file a bug instead. Only genuinely unexpected
        # exceptions become a 500.
        if isinstance(e, StarletteHTTPException):  # ProblemException included
            raise
        raise HTTPException(500, f"Validation run failed: {e}") from e

    # Past this point the validation HAS succeeded and both rows say so.
    # Announcing it is a side-effect, not part of the run: a notification that
    # cannot be queued is not a failed validation, so it must not reach the
    # caller as a 500 and must not drag the closed rows back to 'failed'.
    # ``webhooks.emit`` already promises never to raise; this is the belt to
    # that pair of braces, and it is what keeps the promise load-bearing.
    try:
        # Counts only — a webhook body must never carry failing rows.
        await webhooks.emit(
            "validation.failed" if run["error_failures"] else "validation.passed",
            team_id=str(ds["team_id"]), dataset_id=dataset_id,
            data={"validation_run_id": run["id"],
                  "version_number": version_number,
                  "rules_total": run["rules_total"],
                  "rules_failed": run["rules_failed"],
                  "error_failures": run["error_failures"]})
    except Exception:  # noqa: BLE001
        logger.exception("validation %s completed but its webhook could not be "
                         "emitted", run["id"])

    # Read the results back rather than echoing the in-memory list: POST and
    # GET /validations/{run_id} then return byte-identical documents. The
    # hand-built echo had id=None on every row (so a UI could not link to a
    # result it had just been handed) and a different order (rule created_at,
    # not rule_name).
    persisted = await repo.list_run_results(run["id"])
    return ValidationDetail(**run, results=[RuleResultOut(**r) for r in persisted])


@router.get(
    "/datasets/{dataset_id}/versions/{version_number}/validations",
    response_model=Page[ValidationRunOut], tags=["quality"],
)
async def list_validations(
    dataset_id: str, version_number: int,
    page: PageParams = Depends(pagination),
    principal: Principal = Depends(get_principal),
) -> Page[ValidationRunOut]:
    """List validation runs for a version, newest first."""
    await ensure_dataset_permission(principal, dataset_id, Permission.DATASET_READ)
    ver = await resolve_version(dataset_id, version_number=version_number)
    rows, total = await repo.list_runs(dataset_id, str(ver["id"]),
                                       limit=page.limit, offset=page.offset)
    return Page.of([ValidationRunOut(**r) for r in rows], total, page)


@router.get(
    "/datasets/{dataset_id}/validations/{run_id}",
    response_model=ValidationDetail, tags=["quality"],
)
async def get_validation(
    dataset_id: str, run_id: str, principal: Principal = Depends(get_principal),
) -> ValidationDetail:
    """Full detail (per-rule results) for one validation run."""
    await ensure_dataset_permission(principal, dataset_id, Permission.DATASET_READ)
    run = await repo.get_run(dataset_id, run_id)
    if not run:
        raise HTTPException(404, f"Validation run not found: {run_id}")
    results = await repo.list_run_results(run_id)
    return ValidationDetail(**run, results=[RuleResultOut(**r) for r in results])


async def _register_failure_artifacts(results: list[dict], ds: dict,
                                      user_id: str | None,
                                      layout: ArtifactLayout) -> None:
    """Give each persisted failure file an ownership row.

    The engine writes failing rows to the artifact area (they are dataset
    content and must not go into Postgres). Registering them here is what makes
    `GET /samples/{f}` authorize correctly — cross-team callers get a 404 — and
    what lets a steward publish the failures as a dataset to work through.

    *layout* is the caller's — the same object the engine wrote under. It is a
    parameter rather than something rebuilt here because a rebuilt one can
    disagree about the dataset id, and the storage key it produces is the ONLY
    way to resolve the blob.
    """
    from app.features.library import repo as library_repo

    for result in results:
        filename = result.get("failure_sample_file")
        if not filename:
            continue
        key = layout.key(filename)
        try:
            size = get_storage().size(key)
        except Exception:
            size = None
        artifact = await library_repo.create_artifact(
            key, "validation_failures", filename=filename,
            format="parquet", size_bytes=size,
            media_type="application/vnd.apache.parquet",
            created_by=user_id, dataset_id=str(ds["id"]),
            team_id=str(ds["team_id"]),
        )
        result["failure_artifact_id"] = artifact["id"]
