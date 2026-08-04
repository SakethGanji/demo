"""Quality API — rule CRUD and validation runs.

Routes live under the dataset tree so RBAC follows the standard pattern:
reads need dataset:read, mutations dataset:write, and cross-team requests
404 (existence hidden). Validation runs synchronously (rules compile to
single DuckDB queries) with a jobs row as the execution record.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException

from app.api.pagination import Page, PageParams, pagination
from app.features.auth.deps import Principal, ensure_dataset_permission, get_principal
from app.features.auth.permissions import Permission
from app.shared import jobs
from app.shared.datasets import ensure_sheet_schema, get_version_sheet_rows, resolve_version

from . import repo
from .engine import evaluate_rule
from .schemas import (
    RuleCreate,
    RuleOut,
    RuleResultOut,
    RuleUpdate,
    ValidationDetail,
    ValidationRunOut,
)

router = APIRouter()


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
        {**body.model_dump(), "scope_type": body.scope_type},
        created_by=principal.user_id,
    )
    if not row:
        raise HTTPException(409, f"A rule named '{body.name}' already exists on this dataset")
    return RuleOut(**row)


@router.get("/datasets/{dataset_id}/rules", response_model=Page[RuleOut], tags=["quality"])
async def list_rules(dataset_id: str, principal: Principal = Depends(get_principal)) -> Page[RuleOut]:
    """List all quality rules for a dataset."""
    await ensure_dataset_permission(principal, dataset_id, Permission.DATASET_READ)
    rows = await repo.list_rules(dataset_id)
    items = [RuleOut(**r) for r in rows]
    return Page(items=items, total=len(items), limit=len(items), offset=0)


@router.patch("/datasets/{dataset_id}/rules/{rule_id}", response_model=RuleOut, tags=["quality"])
async def update_rule(
    dataset_id: str, rule_id: str, body: RuleUpdate,
    principal: Principal = Depends(get_principal),
) -> RuleOut:
    """Update a rule (selectors, parameters, severity, enabled)."""
    await ensure_dataset_permission(principal, dataset_id, Permission.DATASET_WRITE)
    row = await repo.update_rule(dataset_id, rule_id, body.model_dump(exclude_unset=True))
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
        results = [evaluate_rule(rule, ver, sheets) for rule in rules]
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
        raise HTTPException(500, f"Validation run failed: {e}")

    return ValidationDetail(
        **run, results=[RuleResultOut(**r, id=None) for r in results],
    )


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
