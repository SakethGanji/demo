"""Guided join builder (ROADMAP §23).

This is the ONLY place the service joins across datasets, and it is deliberately
narrow: a join is driven by a **confirmed** relationship, never by free-form
keys. That single constraint is what makes cross-dataset joining safe to expose
— the keys have been reviewed by a human, and both endpoints carry a dataset id
whose permissions are checked independently.

The other half of "guided" is the pre-flight. Before a join runs, its actual
behaviour is measured: duplicate keys per side, whether it is many-to-many, the
exact output row count (per-key multiplicities multiply), the unmatched
percentage on each side, and which non-key column names collide. Users find out
that a join will 10× their rows *before* producing the result, not after.

Execution rides the existing analytics machinery — one `join` definition per
(relationship, how) plus an `analytics_runs` row — so run history, artifact
ownership, /samples authorization, and publishing all work unchanged.
"""

from __future__ import annotations

import hashlib
import logging

from fastapi import HTTPException

from app.api.errors import ProblemException
from app.features.data_accelerator.services.sampling import _persist_table, _physical_key
from app.features.library import repo as library_repo
from app.features.library.service import (
    publish_artifact_as_version,
    resolve_publishable_artifact,
)
from app.infra.db.storage import ArtifactLayout, get_storage
from app.shared import jobs
from app.shared.data_io import load_data
from app.shared.datasets import (
    ensure_sheet_schema,
    get_version_sheet_rows,
    resolve_version,
    sheet_data_path,
)
from app.shared.utils.sql import quote_ident, safe_value

from . import probes, repo
from .schemas import JoinPreview, JoinWarnings, RelationshipOut

logger = logging.getLogger("analytics.joins")

PREVIEW_ROWS = 5


class JoinSide:
    """One side of a join: its version, sheet, physical key, and columns."""

    def __init__(self, dataset: dict, version: dict, sheet_row: dict,
                 normalized_key: str, view: str):
        self.dataset = dataset
        self.version = version
        self.sheet_row = sheet_row
        self.normalized_key = normalized_key
        self.key = _physical_key(sheet_row, normalized_key)
        self.view = view
        self.columns: list[str] = []

    @property
    def label(self) -> str:
        return f"{self.dataset['name']}.{self.sheet_row['sheet_name']}"


async def _side_sheet(dataset_id: str, logical_sheet_id: str,
                      version_number: int | None) -> tuple[dict, dict]:
    """(version, sheet row with schema) for one endpoint of a relationship."""
    ver = await resolve_version(dataset_id, version_number=version_number)
    if not ver.get("path"):
        raise HTTPException(
            404, f"Version has no data (status: {ver.get('status', 'unknown')})")
    rows = await get_version_sheet_rows(ver)
    row = next((r for r in rows
                if str(r.get("logical_sheet_id") or "") == logical_sheet_id), None)
    if row is None:
        raise ProblemException(
            404, f"The relationship's sheet is not present in version "
                 f"{ver['version_number']}",
            code="sheet-not-in-version", version_number=ver["version_number"])
    return ver, await ensure_sheet_schema(ver, row)


def require_confirmed(relationship: dict) -> None:
    """Only reviewed relationships may drive a join."""
    if relationship["status"] != "confirmed":
        raise ProblemException(
            409,
            "This relationship has not been confirmed — only confirmed "
            "relationships can drive a join",
            code="relationship-not-confirmed",
            current_status=relationship["status"])


def build_join_sql(left: JoinSide, right: JoinSide, how: str,
                   select_columns: list[str] | None) -> tuple[str, list[str]]:
    """SELECT for the joined result, plus its output column names.

    The right side's key is dropped (it duplicates the left's) and any other
    colliding name is prefixed with the right sheet's key — the same
    disambiguation rule the aggregation join already uses, so joined column
    names mean the same thing everywhere.

    The prefix alone does not guarantee uniqueness: a right column literally
    named ``{sheet_key}_{something}`` can land on a name already emitted (by
    the left side, or by an earlier prefixed right column). Every alias is
    therefore checked against what has already been emitted and suffixed
    ``_2``, ``_3``… until it is unique. Without that, the preview builds its
    rows with ``zip(cols, row)`` into a dict, so the duplicate name silently
    overwrites the other column's values while ``output_columns`` still lists
    it twice — a confident, wrong answer.
    """
    parts: list[str] = [f"l.{quote_ident(c)}" for c in left.columns]
    names: list[str] = list(left.columns)
    seen: set[str] = set(names)
    prefix = right.sheet_row["sheet_key"]
    for c in right.columns:
        if c == right.key:
            continue
        alias = c if c not in seen else f"{prefix}_{c}"
        if alias in seen:
            base, n = alias, 2
            while alias in seen:
                alias = f"{base}_{n}"
                n += 1
        parts.append(f"r.{quote_ident(c)} AS {quote_ident(alias)}")
        names.append(alias)
        seen.add(alias)

    if select_columns is not None:
        unknown = [c for c in select_columns if c not in names]
        if unknown:
            raise ProblemException(
                400, f"Unknown output column(s): {unknown}",
                code="unknown-column", available=names)
        keep = set(select_columns)
        parts = [p for p, n in zip(parts, names) if n in keep]
        names = [n for n in names if n in keep]

    how_sql = "LEFT" if how == "left" else "INNER"
    sql = (f"SELECT {', '.join(parts)} FROM {left.view} l "
           f"{how_sql} JOIN {right.view} r "
           f"ON l.{quote_ident(left.key)} = r.{quote_ident(right.key)}")
    return sql, names


def measure(conn, left: JoinSide, right: JoinSide, how: str,
            output_columns: list[str]) -> JoinWarnings:
    """Run every pre-flight probe. No result rows are materialized."""
    left_rows = conn.execute(f"SELECT COUNT(*) FROM {left.view}").fetchone()[0]
    right_rows = conn.execute(f"SELECT COUNT(*) FROM {right.view}").fetchone()[0]

    left_dups = conn.execute(
        probes.duplicate_keys_sql(left.view, left.key)).fetchone()[0]
    right_dups = conn.execute(
        probes.duplicate_keys_sql(right.view, right.key)).fetchone()[0]

    estimated = conn.execute(
        probes.expansion_sql(left.view, left.key, right.view, right.key, how)
    ).fetchone()[0]

    l_total, l_unmatched = conn.execute(
        probes.unmatched_sql(left.view, left.key, right.view, right.key)).fetchone()
    r_total, r_unmatched = conn.execute(
        probes.unmatched_sql(right.view, right.key, left.view, left.key)).fetchone()

    shape = probes.JoinShape(int(left_dups or 0), int(right_dups or 0))
    return JoinWarnings(
        left_rows=int(left_rows), right_rows=int(right_rows),
        left_duplicate_keys=shape.left_dup_keys,
        right_duplicate_keys=shape.right_dup_keys,
        many_to_many=probes.is_many_to_many(shape),
        estimated_output_rows=int(estimated or 0),
        row_expansion_factor=probes.expansion_factor(int(estimated or 0),
                                                     int(left_rows)),
        unmatched_left_pct=round(100 * probes.ratio(l_unmatched, l_total), 2),
        unmatched_right_pct=round(100 * probes.ratio(r_unmatched, r_total), 2),
        column_collisions=probes.column_collisions(
            left.columns, right.columns, left.key, right.key),
    )


async def _open_sides(relationship: dict, spec) -> tuple[JoinSide, JoinSide, object]:
    """Resolve both endpoints and register them as views on one connection."""
    from app.shared.repo import get_dataset

    left_ds = await get_dataset(relationship["dataset_id"])
    right_ds = await get_dataset(relationship["to_dataset_id"])
    if not left_ds or not right_ds:
        raise HTTPException(409, "A dataset behind this relationship no longer exists")

    left_ver, left_sheet = await _side_sheet(
        relationship["dataset_id"], relationship["from_logical_sheet_id"],
        spec.left_version)
    right_ver, right_sheet = await _side_sheet(
        relationship["to_dataset_id"], relationship["to_logical_sheet_id"],
        spec.right_version)

    left = JoinSide(left_ds, left_ver, left_sheet, relationship["from_column"], "join_l")
    right = JoinSide(right_ds, right_ver, right_sheet, relationship["to_column"], "join_r")

    # load_data on the first path configures storage access (S3 included) for
    # the connection; the second side is then a plain read_parquet view.
    conn = load_data(file_path=sheet_data_path(left_ver, left_sheet))
    try:
        for side, ver in ((left, left_ver), (right, right_ver)):
            escaped = str(sheet_data_path(ver, side.sheet_row)).replace("'", "''")
            conn.execute(
                f"CREATE VIEW {side.view} AS SELECT * FROM read_parquet('{escaped}')")
            side.columns = [r[0] for r in conn.execute(f"DESCRIBE {side.view}").fetchall()]
            if side.key not in side.columns:
                raise ProblemException(
                    400,
                    f"The relationship's key '{side.normalized_key}' is not present "
                    f"on {side.label} in this version",
                    code="relationship-endpoint-mismatch",
                    column=side.normalized_key, sheet=side.sheet_row["sheet_name"])
    except Exception:
        conn.close()
        raise
    return left, right, conn


async def preview_join(relationship: dict, spec) -> JoinPreview:
    """Measure the join and return a handful of rows — nothing is persisted."""
    left, right, conn = await _open_sides(relationship, spec)
    try:
        sql, names = build_join_sql(left, right, spec.how, spec.select_columns)
        warnings = measure(conn, left, right, spec.how, names)
        cur = conn.execute(f"{sql} LIMIT {PREVIEW_ROWS}")
        cols = [d[0] for d in cur.description]
        rows = [{c: safe_value(v) for c, v in zip(cols, row)} for row in cur.fetchall()]
    finally:
        conn.close()
    return JoinPreview(warnings=warnings, output_columns=names, preview=rows,
                       relationship=RelationshipOut(**relationship))


async def _join_definition(relationship: dict, how: str, created_by: str) -> dict:
    """The `join` analytics definition for this (relationship, how).

    Deterministic and reused across executions, so repeated joins accumulate
    run history under one definition instead of littering the library.
    """
    dataset_id = relationship["dataset_id"]
    name = f"join:{relationship['id'][:8]}:{how}"
    existing = [d for d in await library_repo.list_definitions(dataset_id)
                if d["name"] == name]
    if existing:
        return existing[0]
    created = await library_repo.create_definition(
        dataset_id,
        {"name": name,
         "description": (f"{relationship['from_sheet']}.{relationship['from_column']} "
                         f"→ {relationship['to_sheet']}.{relationship['to_column']} "
                         f"({how} join)"),
         "kind": "join",
         "sheet": relationship["from_sheet"],
         "params": {"relationship_id": relationship["id"], "how": how}},
        created_by=created_by)
    if created is None:  # raced with a concurrent execute — take the winner
        return next(d for d in await library_repo.list_definitions(dataset_id)
                    if d["name"] == name)
    return created


async def execute_join(relationship: dict, spec, principal) -> dict:
    """Materialize the join as a `join_output` artifact and record a run."""
    require_confirmed(relationship)
    definition = await _join_definition(relationship, spec.how, principal.user_id)

    left, right, conn = await _open_sides(relationship, spec)
    team_id = str(left.dataset["team_id"])
    layout = ArtifactLayout("join_output", team_id=team_id,
                            dataset_id=relationship["dataset_id"])
    job = await jobs.create_job(
        "analytics", dataset_id=relationship["dataset_id"],
        dataset_version_id=str(left.version["id"]), team_id=team_id,
        parameters={"kind": "join", "relationship_id": relationship["id"]})
    await jobs.start_job(str(job["id"]))
    run = await library_repo.create_run(
        definition["id"], str(left.version["id"]), str(job["id"]), principal.user_id)

    try:
        sql, names = build_join_sql(left, right, spec.how, spec.select_columns)
        warnings = measure(conn, left, right, spec.how, names)
        conn.execute(f"CREATE TABLE join_out AS {sql}")
        row_count = conn.execute("SELECT COUNT(*) FROM join_out").fetchone()[0]
        filename = _persist_table(conn, "join_out", "join", layout)
    except Exception as exc:  # noqa: BLE001 — recorded on the run, then surfaced
        await library_repo.fail_run(run["id"], str(exc))
        await jobs.fail_job(str(job["id"]), str(exc))
        raise
    finally:
        conn.close()

    # The tail is a SECOND guarded block rather than an extension of the first,
    # because the DuckDB connection must be released before the storage read
    # and the Postgres writes — widening the block above would hold it across
    # all of them. Everything from here down can fail just as readily as the
    # query did (the object store can be unreachable, `create_artifact` can hit
    # a constraint), and an escape here leaves the run and the job `running`
    # forever: nothing retries an inline join, nothing reaps it, and
    # `resolve_publishable_artifact` rejects it with a 409 that reads as "still
    # working".
    #
    # `library_repo.fail_run` and `jobs.fail_job` are unguarded
    # (`WHERE id = :id`), so they would happily overwrite a row that already
    # reached a terminal status. These two flags are what makes the recovery
    # path safe: whichever record is already closed is left exactly as it is,
    # and only the one still open gets failed. The close order — run first,
    # then job — is the same one every other close path uses, and
    # `scripts/audit_stranded_analytics_runs.py` depends on it: an open run
    # beside a terminal job is its STRANDED *proof*, which only holds while no
    # live code closes the job first.
    run_closed = False
    job_closed = False
    try:
        key = layout.key(filename)
        blob = get_storage().read_bytes(key)
        artifact = await library_repo.create_artifact(
            key, "join_output", filename=filename, format="parquet",
            media_type="application/vnd.apache.parquet",
            size_bytes=len(blob), checksum=hashlib.sha256(blob).hexdigest(),
            created_by=principal.user_id,
            dataset_id=relationship["dataset_id"], team_id=team_id)

        summary = {
            "sample_file": filename, "row_count": int(row_count),
            "relationship_id": relationship["id"], "how": spec.how,
            "left": left.label, "right": right.label,
            "left_version_number": left.version["version_number"],
            "right_version_number": right.version["version_number"],
            "output_columns": names,
            "warnings": warnings.model_dump(),
        }
        completed = await library_repo.complete_run(
            run["id"], result_summary=summary, artifact_id=artifact["id"])
        run_closed = True
        await jobs.complete_job(str(job["id"]), result=summary)
        job_closed = True
    except Exception as exc:  # noqa: BLE001 — recorded on the run, then surfaced
        if not run_closed:
            await library_repo.fail_run(run["id"], str(exc))
        if not job_closed:
            await jobs.fail_job(str(job["id"]), str(exc))
        raise
    return {"run": completed, "summary": summary, "warnings": warnings,
            "output_columns": names}


async def _right_parent_version(relationship: dict, run: dict) -> dict:
    """The right-hand version the join ACTUALLY read — not today's current one.

    The left parent comes from ``run["dataset_version_id"]``, so it is pinned to
    the version that produced the artifact. The right side has no such column,
    and resolving it as "the current version of the target dataset" is wrong
    twice over: a join may name an explicit ``right_version``, and the target
    dataset may have gained versions between execute and publish. Either way
    lineage would credit joined rows to a version they were never computed
    from, and nothing downstream can detect it — the numbers simply do not
    reconcile against the version lineage names.

    ``execute_join`` records ``right_version_number`` on the run, so that is the
    truthful answer. Runs written before that field existed fall back to the
    current version, which is all that was ever knowable about them.
    """
    number = (run.get("result_summary") or {}).get("right_version_number")
    if number is None:
        return await resolve_version(relationship["to_dataset_id"])
    try:
        return await resolve_version(relationship["to_dataset_id"],
                                     version_number=int(number))
    except HTTPException as exc:
        # Mirrors resolve_publishable_artifact's treatment of a vanished left
        # version: refuse rather than substitute a different version.
        raise ProblemException(
            409,
            f"Version {number} of the joined dataset no longer exists, so this "
            "join's second lineage parent cannot be recorded",
            code="join-source-version-missing",
            dataset_id=str(relationship["to_dataset_id"]),
            version_number=int(number)) from exc


async def publish_join(run: dict, relationship: dict, target_ds: dict, *,
                       mode: str, name: str | None, principal) -> dict:
    """Publish a join output, recording BOTH parents in lineage."""
    artifact, parent_ver = await resolve_publishable_artifact(run)
    right_ver = await _right_parent_version(relationship, run)
    from app.shared.repo import get_dataset

    right_ds = await get_dataset(relationship["to_dataset_id"])

    extra = []
    if right_ds and right_ver:
        extra.append({
            "parent_dataset_id": str(right_ds["id"]),
            "parent_version_id": str(right_ver["id"]),
            "parent_dataset_name": right_ds["name"],
            "parent_version_number": right_ver["version_number"],
            "parent_sheet_key": relationship["to_sheet"],
            "relation": "joined_from",
        })
    return await publish_artifact_as_version(
        target_ds, artifact, parent_ver, mode=mode, name=name, principal=principal,
        relation="joined_from", parent_sheet_key=relationship["from_sheet"],
        default_suffix="joined",
        source_extra={"analytics_run_id": run["id"],
                      "relationship_id": relationship["id"]},
        extra_lineage=extra)
