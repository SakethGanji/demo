"""Persistence for transformation definitions and runs (ROADMAP §19)."""

from __future__ import annotations

import json

from sqlalchemy import text

from app.infra.db.postgres import async_session_factory
from app.shared.repo import is_uuid

# `sheet_key` is joined from the logical sheet so callers always see the sheet's
# CURRENT name, however many renames it has been through.
_DEF_COLS = """t.id::text, t.dataset_id::text, t.logical_sheet_id::text,
               s.current_sheet_key AS sheet_key,
               t.name, t.description, t.version_selector, t.steps,
               t.created_by::text, t.created_at::text AS created_at,
               t.updated_at::text AS updated_at"""
_DEF_FROM = ("transformation_definitions t "
             "JOIN dataset_sheets s ON s.id = t.logical_sheet_id")


async def create_definition(*, dataset_id: str, logical_sheet_id: str, name: str,
                            description: str | None, version_selector: dict,
                            steps: list, created_by: str) -> dict | None:
    """Insert a definition; None on a (dataset, name) collision."""
    async with async_session_factory() as s:
        inserted = (await s.execute(
            text("""
                INSERT INTO transformation_definitions
                    (dataset_id, logical_sheet_id, name, description,
                     version_selector, steps, created_by)
                VALUES (:did, :lsid, :name, :descr, CAST(:vs AS jsonb),
                        CAST(:steps AS jsonb), :uid)
                ON CONFLICT (dataset_id, name) DO NOTHING
                RETURNING id::text
            """),
            {"did": dataset_id, "lsid": logical_sheet_id, "name": name,
             "descr": description, "vs": json.dumps(version_selector),
             "steps": json.dumps(steps), "uid": created_by},
        )).mappings().first()
        await s.commit()
    return await get_definition(dataset_id, inserted["id"]) if inserted else None


async def list_definitions(dataset_id: str, *, limit: int,
                           offset: int) -> tuple[list[dict], int]:
    async with async_session_factory() as s:
        total = (await s.execute(
            text("SELECT COUNT(*) FROM transformation_definitions WHERE dataset_id = :did"),
            {"did": dataset_id},
        )).scalar()
        rows = (await s.execute(
            text(f"SELECT {_DEF_COLS} FROM {_DEF_FROM} WHERE t.dataset_id = :did "
                 f"ORDER BY t.name LIMIT :limit OFFSET :offset"),
            {"did": dataset_id, "limit": limit, "offset": offset},
        )).mappings().all()
        return [dict(r) for r in rows], total


async def get_definition(dataset_id: str, definition_id: str) -> dict | None:
    if not is_uuid(definition_id):
        return None
    async with async_session_factory() as s:
        row = (await s.execute(
            text(f"SELECT {_DEF_COLS} FROM {_DEF_FROM} "
                 f"WHERE t.id = :id AND t.dataset_id = :did"),
            {"id": definition_id, "did": dataset_id},
        )).mappings().first()
        return dict(row) if row else None


async def update_definition(dataset_id: str, definition_id: str,
                            fields: dict) -> dict | None:
    if not is_uuid(definition_id):
        return None
    sets = ["updated_at = now()"]
    params: dict = {"id": definition_id, "did": dataset_id}
    for col in ("name", "description", "logical_sheet_id"):
        if col in fields:
            sets.append(f"{col} = :{col}")
            params[col] = fields[col]
    for col in ("version_selector", "steps"):
        if col in fields:
            sets.append(f"{col} = CAST(:{col} AS jsonb)")
            params[col] = json.dumps(fields[col])
    async with async_session_factory() as s:
        updated = (await s.execute(
            text(f"UPDATE transformation_definitions SET {', '.join(sets)} "
                 f"WHERE id = :id AND dataset_id = :did RETURNING id::text"),
            params,
        )).mappings().first()
        await s.commit()
    return await get_definition(dataset_id, definition_id) if updated else None


async def delete_definition(dataset_id: str, definition_id: str) -> bool:
    if not is_uuid(definition_id):
        return False
    async with async_session_factory() as s:
        result = await s.execute(
            text("DELETE FROM transformation_definitions "
                 "WHERE id = :id AND dataset_id = :did"),
            {"id": definition_id, "did": dataset_id})
        await s.commit()
        return result.rowcount > 0


# ---------------------------------------------------------------------------
# Runs
# ---------------------------------------------------------------------------

_RUN_COLS = """id::text, definition_id::text, dataset_version_id::text, job_id::text,
               status, mode, result_summary, artifact_id::text, triggered_by::text,
               started_at::text AS started_at, completed_at::text AS completed_at, error"""
_RUN_DETAIL_COLS = _RUN_COLS + ", output_profile, source_drift"


async def create_run(*, definition_id: str, dataset_version_id: str | None,
                     job_id: str | None, mode: str, triggered_by: str | None) -> dict:
    async with async_session_factory() as s:
        row = (await s.execute(
            text(f"""
                INSERT INTO transformation_runs
                    (definition_id, dataset_version_id, job_id, mode, triggered_by)
                VALUES (:defid, :vid, :jid, :mode, :uid)
                RETURNING {_RUN_COLS}
            """),
            {"defid": definition_id, "vid": dataset_version_id, "jid": job_id,
             "mode": mode, "uid": triggered_by},
        )).mappings().one()
        await s.commit()
        return dict(row)


async def attach_job(run_id: str, job_id: str) -> None:
    """Link a run to the job executing it (the handler knows its own job id)."""
    async with async_session_factory() as s:
        await s.execute(
            text("UPDATE transformation_runs SET job_id = :jid WHERE id = :id"),
            {"id": run_id, "jid": job_id})
        await s.commit()


async def complete_run(run_id: str, *, result_summary: dict | None,
                       artifact_id: str | None,
                       output_profile: dict | None = None,
                       source_drift: dict | None = None) -> dict:
    async with async_session_factory() as s:
        row = (await s.execute(
            text(f"""
                UPDATE transformation_runs
                SET status = 'completed', completed_at = now(),
                    result_summary = CAST(:summary AS jsonb), artifact_id = :aid,
                    output_profile = CAST(:profile AS jsonb),
                    source_drift = CAST(:drift AS jsonb)
                WHERE id = :id
                RETURNING {_RUN_DETAIL_COLS}
            """),
            {"id": run_id,
             "summary": json.dumps(result_summary) if result_summary else None,
             "aid": artifact_id,
             "profile": json.dumps(output_profile) if output_profile else None,
             "drift": json.dumps(source_drift) if source_drift else None},
        )).mappings().one()
        await s.commit()
        return dict(row)


async def fail_run(run_id: str, error: str) -> None:
    """Close a run as failed — but only if it is still open.

    The ``status = 'running'`` guard makes this "fail it if it has not already
    ended" rather than "fail it". Two callers can now legitimately reach for the
    same run: ``_handle_transform`` records the precise pipeline error, and
    ``start_run`` closes the row when the dispatch itself blows up outside the
    handler. Without the guard the outer, vaguer message would overwrite the
    inner, specific one — and a run that had already *completed* could be
    demoted to ``failed`` by a late exception on the way back out.

    Same shape as the guarded close in
    ``scripts/audit_stranded_analytics_runs.py``, and for the same reason: it
    makes the write idempotent, so belt-and-braces bookkeeping is safe.
    """
    async with async_session_factory() as s:
        await s.execute(
            text("UPDATE transformation_runs SET status = 'failed', error = :e, "
                 "completed_at = now() WHERE id = :id AND status = 'running'"),
            {"id": run_id, "e": error})
        await s.commit()


async def list_runs(definition_id: str, *, limit: int,
                    offset: int) -> tuple[list[dict], int]:
    async with async_session_factory() as s:
        total = (await s.execute(
            text("SELECT COUNT(*) FROM transformation_runs WHERE definition_id = :d"),
            {"d": definition_id},
        )).scalar()
        # The publication stamp is joined here too, not only in get_run: a run
        # list that omits it leaves a UI unable to tell an already-published run
        # from a publishable one, so it re-offers Publish and the second attempt
        # 409s with `run-already-published`.
        run_cols = ", ".join(f"r.{c.strip()}" for c in _RUN_COLS.replace("\n", " ").split(","))
        rows = (await s.execute(
            text(f"""
                SELECT {run_cols},
                       pub.published_version_id, pub.published_dataset_id,
                       pub.published_version_number
                FROM transformation_runs r
                LEFT JOIN LATERAL ({_PUBLISHED_VERSION_SQL}) pub ON TRUE
                WHERE r.definition_id = :d
                ORDER BY r.started_at DESC LIMIT :limit OFFSET :offset
            """),
            {"d": definition_id, "limit": limit, "offset": offset},
        )).mappings().all()
        return [dict(r) for r in rows], total


# The version a transformation run has already been published as. `source` is
# the provenance JSONB `publish_artifact_as_version` writes; a
# `transformation_run_id` inside it is written only by the transformation
# publish path. Correlated on `r`, so it is only valid inside a lateral join
# against `transformation_runs r`.
_PUBLISHED_VERSION_SQL = """
    SELECT v.id::text AS published_version_id,
           v.dataset_id::text AS published_dataset_id,
           v.version_number AS published_version_number
    FROM dataset_versions v
    WHERE v.source->>'transformation_run_id' = r.id::text
    ORDER BY v.created_at, v.version_number
    LIMIT 1
"""


async def get_run(run_id: str) -> dict | None:
    """A run joined to its definition — carries dataset_id for authorization.

    Also carries the version this run was already published as, if any. That
    fact is not a column on ``transformation_runs``: publishing stamps the run
    id into ``dataset_versions.source`` (``publish_artifact_as_version``), and
    that stamp is the record. Reading it back through a lateral join keeps the
    answer derived from the published version itself, so deleting the version
    makes the run publishable again with no bookkeeping to go stale.
    """
    if not is_uuid(run_id):
        return None
    async with async_session_factory() as s:
        row = (await s.execute(
            text(f"""
                SELECT r.id::text, r.definition_id::text, r.dataset_version_id::text,
                       r.job_id::text, r.status, r.mode, r.result_summary,
                       r.artifact_id::text, r.triggered_by::text,
                       r.started_at::text AS started_at,
                       r.completed_at::text AS completed_at, r.error,
                       r.output_profile, r.source_drift,
                       d.dataset_id::text AS dataset_id,
                       d.logical_sheet_id::text AS logical_sheet_id,
                       s.current_sheet_key AS sheet_key,
                       pub.published_version_id, pub.published_dataset_id,
                       pub.published_version_number
                FROM transformation_runs r
                JOIN transformation_definitions d ON d.id = r.definition_id
                JOIN dataset_sheets s ON s.id = d.logical_sheet_id
                LEFT JOIN LATERAL ({_PUBLISHED_VERSION_SQL}) pub ON TRUE
                WHERE r.id = :id
            """),
            {"id": run_id},
        )).mappings().first()
        return dict(row) if row else None


async def get_published_version(run_id: str) -> dict | None:
    """The version *run_id* was already published as, or None."""
    if not is_uuid(run_id):
        return None
    async with async_session_factory() as s:
        row = (await s.execute(
            text(f"SELECT pub.* FROM transformation_runs r "
                 f"LEFT JOIN LATERAL ({_PUBLISHED_VERSION_SQL}) pub ON TRUE "
                 f"WHERE r.id = :id"),
            {"id": run_id},
        )).mappings().first()
        return dict(row) if row and row.get("published_version_id") else None
