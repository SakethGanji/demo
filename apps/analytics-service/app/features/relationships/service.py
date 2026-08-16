"""Relationship discovery service (ROADMAP §22).

Two ways an edge gets into ``dataset_relationships``:

* **Seeding** turns the dataset's own ``foreign_key`` quality rules into edges.
  Those are declarations a steward already made, so they arrive with full
  confidence and need only confirmation.
* **Statistical discovery** looks for edges nobody declared. A Python
  pre-filter (name convention + type-family gate) proposes candidate column
  pairs without touching any data; only survivors are probed against the
  parquet for value overlap and target uniqueness. Both directions of each
  pair are scored and the better one wins, which is what orients an edge:
  the side whose key is (near-)unique is the parent.

Everything a suggestion is based on is written to ``evidence``, so a reviewer
can see *why* the system proposed an edge rather than being asked to trust a
bare number.
"""

from __future__ import annotations

import logging

from fastapi import HTTPException

from app.api.errors import ProblemException
from app.features.data_accelerator.services.sampling import _physical_key
from app.features.quality import repo as quality_repo
from app.shared import jobs, worker
from app.shared.data_io import load_data
from app.shared.datasets import (
    ensure_sheet_schema,
    get_version_sheet_rows,
    resolve_version,
    sheet_data_path,
)

from . import probes, repo

logger = logging.getLogger("analytics.relationships")

ALGORITHM_VERSION = 1

# Discovery is O(columns²) across sheet pairs. The name/type pre-filter cuts
# almost everything, but a wide workbook could still propose a lot of probes —
# so the number that reach SQL is capped, and what was skipped is reported.
MAX_CANDIDATE_PAIRS = 400


# ---------------------------------------------------------------------------
# Sheet plumbing
# ---------------------------------------------------------------------------

async def _ready_sheets(ver: dict) -> list[dict]:
    """Ready sheets of a version, each with schema and a logical identity."""
    rows = await get_version_sheet_rows(ver)
    ready = [r for r in rows
             if r.get("status", "ready") == "ready" and r.get("logical_sheet_id")]
    return [await ensure_sheet_schema(ver, r) for r in ready]


async def resolve_sheet(ver: dict, sheet: str | None) -> dict:
    """Resolve a sheet reference on *ver*, honouring the selection contract."""
    from app.shared.datasets import resolve_version_sheet_row

    row = await resolve_version_sheet_row(ver, sheet)
    if row is None or not row.get("logical_sheet_id"):
        raise ProblemException(
            400, "This sheet has no logical identity (legacy version) — "
                 "relationships require one",
            code="relationship-unsupported")
    return await ensure_sheet_schema(ver, row)


def normalized_column(sheet_row: dict, column: str) -> str:
    """Validate a column against a sheet's schema and return its NORMALIZED name.

    Relationships store normalized names so they survive the physical-name
    quirks of Excel ingestion; ``_physical_key`` maps back at probe time.
    """
    for c in sheet_row.get("schema_json") or []:
        if column in (c.get("normalized_name"), c["name"]):
            return c.get("normalized_name") or c["name"]
    raise ProblemException(
        400, f"Unknown column: '{column}'", code="unknown-column", column=column,
        available=[c.get("normalized_name") or c["name"]
                   for c in sheet_row.get("schema_json") or []])


# ---------------------------------------------------------------------------
# Seeding from foreign_key quality rules
# ---------------------------------------------------------------------------

async def seed_from_fk_rules(ds: dict, created_by: str | None) -> list[dict]:
    """Emit one directed edge per enabled ``foreign_key`` rule (idempotent).

    A foreign_key rule already states the direction — it lives ON the child
    sheet and names the parent in ``parameters`` — so seeding is a
    straightforward projection of rules onto edges. Re-seeding never clobbers a
    human verdict (see ``upsert_relationship``).
    """
    dataset_id = str(ds["id"])
    ver = await resolve_version(dataset_id)
    sheets = await _ready_sheets(ver)
    by_key: dict[str, dict] = {}
    for row in sheets:
        by_key[row["sheet_key"]] = row
        by_key[row["sheet_name"]] = row

    rules = [r for r in await quality_repo.list_rules(dataset_id, enabled_only=True)
             if r["rule_type"] == "foreign_key"]

    created: list[dict] = []
    for rule in rules:
        params = rule.get("parameters") or {}
        child = by_key.get(rule.get("sheet_selector"))
        parent = by_key.get(params.get("ref_sheet"))
        if not child or not parent:
            continue  # the rule points at a sheet this version doesn't have
        try:
            from_column = normalized_column(child, rule["column_selector"])
            to_column = normalized_column(parent, params["ref_column"])
        except (ProblemException, KeyError, TypeError):
            continue  # a rule naming a column this version dropped
        created.append(await repo.upsert_relationship(
            dataset_id=dataset_id,
            from_logical_sheet_id=str(child["logical_sheet_id"]),
            from_column=from_column,
            to_dataset_id=dataset_id,
            to_logical_sheet_id=str(parent["logical_sheet_id"]),
            to_column=to_column,
            method="fk_rule",
            evidence={"rule_id": str(rule["id"]), "rule_name": rule["name"]},
            confidence=1.0,
            created_by=created_by,
            algorithm_version=ALGORITHM_VERSION,
        ))
    return created


# ---------------------------------------------------------------------------
# Statistical discovery
# ---------------------------------------------------------------------------

def _candidate_pairs(sheets: list[dict]) -> tuple[list[tuple], int]:
    """Column pairs worth probing, from names and types alone (no I/O).

    Returns the capped candidate list and the total number proposed, so the
    caller can report truthfully when the cap bit.
    """
    candidates: list[tuple] = []
    for i, left in enumerate(sheets):
        for right in sheets[i + 1:]:
            for lcol in left["schema_json"] or []:
                for rcol in right["schema_json"] or []:
                    if not probes.types_compatible(lcol.get("dtype") or "",
                                                   rcol.get("dtype") or ""):
                        continue
                    lname = lcol.get("normalized_name") or lcol["name"]
                    rname = rcol.get("normalized_name") or rcol["name"]
                    forward = probes.name_score(lname, rname, right["sheet_key"])
                    backward = probes.name_score(rname, lname, left["sheet_key"])
                    if max(forward, backward) <= 0:
                        continue
                    candidates.append((left, lname, right, rname, forward, backward))
    total = len(candidates)
    candidates.sort(key=lambda c: max(c[4], c[5]), reverse=True)
    return candidates[:MAX_CANDIDATE_PAIRS], total


def _measure(conn, view: str, column: str) -> tuple[int, int]:
    """(distinct non-NULL values, non-NULL rows) for a candidate key column."""
    row = conn.execute(probes.uniqueness_sql(view, column)).fetchone()
    return int(row[0] or 0), int(row[1] or 0)


def _probe_direction(conn, child_view: str, child_col: str,
                     parent_view: str, parent_col: str,
                     parent_distinct: int, parent_non_null: int) -> probes.PairSignals:
    """Measure one direction: how well child→parent behaves as a reference."""
    child_distinct, matched = conn.execute(
        probes.coverage_sql(child_view, child_col, parent_view, parent_col)).fetchone()
    child_distinct, matched = int(child_distinct or 0), int(matched or 0)
    return probes.PairSignals(
        coverage=probes.ratio(matched, child_distinct),
        uniqueness=probes.ratio(parent_distinct, parent_non_null),
        child_distinct=child_distinct,
        parent_distinct=parent_distinct,
        matched_distinct=matched,
    )


async def suggest_relationships(ds: dict, created_by: str | None) -> dict:
    """Probe the current version for undeclared relationships.

    The four signals (name, type family, value overlap, target uniqueness) are
    combined into one confidence; anything at or above the threshold is
    persisted as a ``statistical`` suggestion for a human to confirm.
    """
    dataset_id = str(ds["id"])
    ver = await resolve_version(dataset_id)
    sheets = await _ready_sheets(ver)
    if len(sheets) < 2:
        return {"pairs_examined": 0, "suggested": 0, "skipped": 0}

    candidates, proposed = _candidate_pairs(sheets)
    if proposed > len(candidates):
        logger.info("relationship discovery capped: %d of %d candidate pairs probed",
                    len(candidates), proposed)
    if not candidates:
        return {"pairs_examined": 0, "suggested": 0,
                "skipped": proposed - len(candidates)}

    # One connection, every sheet as a view — the same backend-agnostic shape
    # coordinated sampling uses (load_data configures S3 access when needed).
    conn = load_data(file_path=sheet_data_path(ver, sheets[0]))
    views: dict[str, str] = {}
    suggested = 0
    try:
        for idx, row in enumerate(sheets):
            view = f"rel_sheet_{idx}"
            escaped = str(sheet_data_path(ver, row)).replace("'", "''")
            conn.execute(
                f"CREATE VIEW {view} AS SELECT * FROM read_parquet('{escaped}')")
            views[str(row["logical_sheet_id"])] = view

        uniqueness_cache: dict[tuple[str, str], tuple[int, int]] = {}

        def measured(sheet_row: dict, normalized: str) -> tuple[int, int]:
            view_name = views[str(sheet_row["logical_sheet_id"])]
            key = (view_name, normalized)
            if key not in uniqueness_cache:
                uniqueness_cache[key] = _measure(
                    conn, view_name, _physical_key(sheet_row, normalized))
            return uniqueness_cache[key]

        for left, lcol, right, rcol, forward_name, backward_name in candidates:
            lview, rview = (views[str(left["logical_sheet_id"])],
                            views[str(right["logical_sheet_id"])])
            lphys, rphys = _physical_key(left, lcol), _physical_key(right, rcol)
            l_distinct, l_non_null = measured(left, lcol)
            r_distinct, r_non_null = measured(right, rcol)

            # Score both orientations; the parent is whichever side looks
            # more like a primary key once overlap is taken into account.
            forward = _probe_direction(conn, lview, lphys, rview, rphys,
                                       r_distinct, r_non_null)
            backward = _probe_direction(conn, rview, rphys, lview, lphys,
                                        l_distinct, l_non_null)
            forward_score = probes.score_pair(forward, name=forward_name)
            backward_score = probes.score_pair(backward, name=backward_name)

            if forward_score >= backward_score:
                child, child_col, parent, parent_col = left, lcol, right, rcol
                signals, score, name_score = forward, forward_score, forward_name
            else:
                child, child_col, parent, parent_col = right, rcol, left, lcol
                signals, score, name_score = backward, backward_score, backward_name
            if not probes.qualifies(score, signals):
                continue

            await repo.upsert_relationship(
                dataset_id=dataset_id,
                from_logical_sheet_id=str(child["logical_sheet_id"]),
                from_column=child_col,
                to_dataset_id=dataset_id,
                to_logical_sheet_id=str(parent["logical_sheet_id"]),
                to_column=parent_col,
                method="statistical",
                evidence={
                    "name_score": name_score,
                    "coverage": signals.coverage,
                    "target_uniqueness": signals.uniqueness,
                    "child_distinct": signals.child_distinct,
                    "matched_distinct": signals.matched_distinct,
                    "parent_distinct": signals.parent_distinct,
                    "from_sheet": child["sheet_name"],
                    "to_sheet": parent["sheet_name"],
                    "version_number": ver["version_number"],
                },
                confidence=score,
                created_by=created_by,
                algorithm_version=ALGORITHM_VERSION,
            )
            # Counts pairs discovery DERIVED, not rows inserted: re-deriving an
            # existing edge still refreshes `evidence.statistical`, which is real
            # work, and test_relationship_guards pins that semantic. The UI words
            # the toast so "derived" is not read as "newly created".
            suggested += 1
    finally:
        conn.close()

    return {"pairs_examined": len(candidates), "suggested": suggested,
            "skipped": proposed - len(candidates)}


async def _handle_discovery(job: dict) -> dict:
    """Registered ``relationship_discovery`` handler (worker loop AND inline)."""
    from app.shared.repo import get_dataset

    params = job.get("parameters") or {}
    ds = await get_dataset(params["dataset_id"])
    if not ds:
        raise RuntimeError(f"Dataset {params['dataset_id']} is gone")
    result = await suggest_relationships(ds, params.get("triggered_by"))
    return {**result, "job_id": str(job["id"])}


worker.register_handler("relationship_discovery", _handle_discovery)


async def dispatch_discovery(*, dataset_id: str, team_id: str,
                             triggered_by: str | None, inline: bool) -> dict:
    """Enqueue a discovery run and ALWAYS hand back a handle for it.

    ``worker.dispatch(inline=False)`` returns ``None``: it creates the job row
    and then throws the id away, so the async mode — the only mode where the
    caller cannot see the result in the response — would have nothing to poll.
    The enqueue-only branch therefore creates the row here (which is all
    ``dispatch`` does when ``inline`` is false) and returns its id, so
    ``GET /jobs/{id}`` works for a run that has not happened yet.

    The inline branch still goes through ``worker.dispatch``, because that is
    what guarantees the job row reaches a terminal status whichever way the
    handler ends.
    """
    params = {"dataset_id": dataset_id, "triggered_by": triggered_by}
    if inline:
        return await worker.dispatch(
            "relationship_discovery", params=params, dataset_id=dataset_id,
            team_id=team_id, inline=True) or {}
    job = await jobs.create_job("relationship_discovery", dataset_id=dataset_id,
                                team_id=team_id, parameters=params)
    return {"job_id": str(job["id"])}


# ---------------------------------------------------------------------------
# Review transitions
# ---------------------------------------------------------------------------

# A steward can always change their mind, but a verdict is never silently
# overwritten by discovery — only these transitions are legal.
_ALLOWED_TRANSITIONS = {
    "suggested": {"confirmed", "rejected"},
    "confirmed": {"rejected"},
    "rejected": {"confirmed"},
}


def check_transition(current: str, target: str) -> None:
    if target not in _ALLOWED_TRANSITIONS.get(current, set()):
        raise ProblemException(
            409, f"Cannot move a relationship from '{current}' to '{target}'",
            # NB: not `status` — that name collides with problem+json's own field.
            code="invalid-relationship-transition",
            current_status=current, target_status=target)


async def join_definitions_using(dataset_id: str, relationship_id: str) -> list[dict]:
    """Saved ``join`` analytics definitions that name this relationship.

    ``params`` is JSONB, so there is no foreign key from an analytics
    definition to the edge it joins on and nothing cascades. Read through
    ``list_definitions`` rather than a second query against
    ``analytics_definitions``: the library owns that table, and its definitions
    are per-dataset — ``joins._join_definition`` always writes onto the edge's
    OWNING dataset — so one unbounded listing sees every dependent there is.
    """
    from app.features.library import repo as library_repo

    return [d for d in await library_repo.list_definitions(dataset_id)
            if d.get("kind") == "join"
            and (d.get("params") or {}).get("relationship_id") == relationship_id]


async def ensure_no_join_definitions(dataset_id: str, relationship_id: str) -> None:
    """409 if deleting this edge would strand a saved join definition.

    Without this, DELETE succeeded and left the library holding a row whose
    ``params.relationship_id`` points at nothing: it still lists, still counts
    towards ``total``, still shows its run history — and every button on it
    (re-run, preview, publish) answers 404, because they all start at
    ``_authorized_relationship``. The dependents are named in ``attached`` so
    the UI can offer "delete these first" instead of a bare refusal; cascading
    is deliberately not the choice here, because a definition carries run
    history the server cannot decide is disposable.
    """
    dependents = await join_definitions_using(dataset_id, relationship_id)
    if dependents:
        raise ProblemException(
            409,
            f"This relationship has {len(dependents)} saved join "
            f"definition(s) — delete them before deleting the relationship",
            code="relationship-has-dependents",
            attached={"join_definitions": [{"id": d["id"], "name": d["name"]}
                                           for d in dependents]})


async def review(relationship: dict, target: str, principal) -> dict:
    check_transition(relationship["status"], target)
    updated = await repo.set_status(relationship["id"], target, principal.user_id)
    if not updated:
        raise HTTPException(404, f"Relationship not found: {relationship['id']}")
    return updated
