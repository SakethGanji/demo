"""Explorer feature — profile-run persistence (+ saved views, §10)."""

from __future__ import annotations

import json

from sqlalchemy import text

from app.infra.db.postgres import async_session_factory

_RUN_COLS = """id::text, dataset_id::text, dataset_version_id::text,
               logical_sheet_id::text, job_id::text, status,
               algorithm_version, profile, error, created_by::text,
               started_at::text AS started_at,
               completed_at::text AS completed_at"""

_INSIGHT_COLS = """id::text, profile_run_id::text, rule, severity,
                   column_name, message, evidence"""


async def upsert_run(*, dataset_id: str, dataset_version_id: str,
                     logical_sheet_id: str, job_id: str | None,
                     created_by: str, algorithm_version: int = 1) -> dict:
    """Create (or reset, for idempotent re-profiling) a run row."""
    async with async_session_factory() as s:
        row = (await s.execute(
            text(f"""
                INSERT INTO profile_runs
                    (dataset_id, dataset_version_id, logical_sheet_id, job_id,
                     created_by, algorithm_version)
                VALUES (:did, :vid, :lsid, :jid, :uid, :alg)
                ON CONFLICT (dataset_version_id, logical_sheet_id, algorithm_version)
                DO UPDATE SET status = 'running', profile = NULL, error = NULL,
                              job_id = EXCLUDED.job_id,
                              created_by = EXCLUDED.created_by,
                              started_at = now(), completed_at = NULL
                RETURNING {_RUN_COLS}
            """),
            {"did": dataset_id, "vid": dataset_version_id,
             "lsid": logical_sheet_id, "jid": job_id, "uid": created_by,
             "alg": algorithm_version},
        )).mappings().first()
        # A rerun replaces the previous insights wholesale.
        await s.execute(
            text("DELETE FROM profile_insights WHERE profile_run_id = :rid"),
            {"rid": row["id"]})
        await s.commit()
        return dict(row)


async def complete_run(run_id: str, profile: dict, insights: list[dict]) -> dict:
    async with async_session_factory() as s:
        row = (await s.execute(
            text(f"""
                UPDATE profile_runs
                SET status = 'completed', profile = CAST(:profile AS jsonb),
                    completed_at = now()
                WHERE id = :rid
                RETURNING {_RUN_COLS}
            """),
            {"rid": run_id, "profile": json.dumps(profile)},
        )).mappings().first()
        for ins in insights:
            await s.execute(
                text("""
                    INSERT INTO profile_insights
                        (profile_run_id, rule, severity, column_name, message,
                         evidence)
                    VALUES (:rid, :rule, :severity, :column_name, :message,
                            CAST(:evidence AS jsonb))
                """),
                {"rid": run_id, "rule": ins["rule"],
                 "severity": ins["severity"],
                 "column_name": ins.get("column_name"),
                 "message": ins["message"],
                 "evidence": json.dumps(ins.get("evidence") or {})})
        await s.commit()
        return dict(row)


async def fail_run(run_id: str, error: str) -> None:
    """Close a run as failed — but only if it is still open.

    The ``status = 'running'`` guard makes this "fail it if it has not already
    ended" rather than "fail it". ``profile_version`` holds the run in a local
    and fails it from one shared ``except``, but the local is only cleared
    *after* ``_run_out`` has built the response — so a pydantic validation error
    while shaping the output of a run that already COMPLETED would otherwise
    demote that row to ``failed``. The profile it computed is still sitting in
    the row; only the status would be a lie.

    Same shape and same reason as ``transform/repo.py::fail_run`` and the
    guarded close in ``scripts/audit_stranded_analytics_runs.py``: it makes the
    write idempotent, so belt-and-braces bookkeeping is safe.
    """
    async with async_session_factory() as s:
        await s.execute(
            text("""
                UPDATE profile_runs
                SET status = 'failed', error = :err, completed_at = now()
                WHERE id = :rid AND status = 'running'
            """),
            {"rid": run_id, "err": error})
        await s.commit()


async def list_runs_for_version(
    dataset_version_id: str, *, status: str | None = None,
    algorithm_version: int | None = None,
    limit: int | None = None, offset: int = 0,
) -> tuple[list[dict], int]:
    """(page of runs, total matching) for one version.

    Paged and filterable because this is a collection endpoint like every other
    one in the service; ``limit=None`` returns everything, which is what the
    internal callers that are not serving a page want.
    """
    where = ["dataset_version_id = :vid"]
    params: dict = {"vid": dataset_version_id}
    if status is not None:
        where.append("status = :status")
        params["status"] = status
    if algorithm_version is not None:
        where.append("algorithm_version = :alg")
        params["alg"] = algorithm_version
    clause = " AND ".join(where)

    async with async_session_factory() as s:
        total = (await s.execute(
            text(f"SELECT COUNT(*) FROM profile_runs WHERE {clause}"),
            params)).scalar_one()
        window = ""
        if limit is not None:
            window = " LIMIT :limit OFFSET :offset"
            params = {**params, "limit": limit, "offset": offset}
        rows = (await s.execute(
            text(f"""
                SELECT {_RUN_COLS} FROM profile_runs
                WHERE {clause}
                ORDER BY started_at, logical_sheet_id{window}
            """),
            params,
        )).mappings().all()
        return [dict(r) for r in rows], total


async def get_run(dataset_id: str, run_id: str) -> dict | None:
    from app.shared.repo import is_uuid

    if not is_uuid(run_id):
        return None
    async with async_session_factory() as s:
        row = (await s.execute(
            text(f"""
                SELECT {_RUN_COLS} FROM profile_runs
                WHERE id = :rid AND dataset_id = :did
            """),
            {"rid": run_id, "did": dataset_id},
        )).mappings().first()
        return dict(row) if row else None


async def list_insights(run_ids: list[str]) -> dict[str, list[dict]]:
    """Insights grouped by run id, for the given runs."""
    if not run_ids:
        return {}
    async with async_session_factory() as s:
        rows = (await s.execute(
            text(f"""
                SELECT {_INSIGHT_COLS} FROM profile_insights
                WHERE profile_run_id = ANY(CAST(:rids AS uuid[]))
                ORDER BY severity, rule, column_name
            """),
            {"rids": run_ids},
        )).mappings().all()
    grouped: dict[str, list[dict]] = {}
    for r in rows:
        grouped.setdefault(r["profile_run_id"], []).append(dict(r))
    return grouped


async def get_completed_run(dataset_version_id: str, logical_sheet_id: str,
                            algorithm_version: int = 1) -> dict | None:
    async with async_session_factory() as s:
        row = (await s.execute(
            text(f"""
                SELECT {_RUN_COLS} FROM profile_runs
                WHERE dataset_version_id = :vid AND logical_sheet_id = :lsid
                  AND algorithm_version = :alg AND status = 'completed'
            """),
            {"vid": dataset_version_id, "lsid": logical_sheet_id,
             "alg": algorithm_version},
        )).mappings().first()
        return dict(row) if row else None


# ---------------------------------------------------------------------------
# Saved views (§10)
# ---------------------------------------------------------------------------

_VIEW_COLS = """v.id::text, v.dataset_id::text, v.logical_sheet_id::text,
                s.current_sheet_key AS sheet_key, s.display_name AS sheet_name,
                v.name, v.description, v.version_selector, v.query,
                v.created_by::text,
                v.created_at::text AS created_at,
                v.updated_at::text AS updated_at"""

_VIEW_FROM = "dataset_views v JOIN dataset_sheets s ON s.id = v.logical_sheet_id"


async def create_view(*, dataset_id: str, logical_sheet_id: str, name: str,
                      description: str | None, version_selector: dict,
                      query: dict, created_by: str) -> dict | None:
    """Insert a view; None on a (dataset, name) collision."""
    async with async_session_factory() as s:
        row = (await s.execute(
            text("""
                INSERT INTO dataset_views
                    (dataset_id, logical_sheet_id, name, description,
                     version_selector, query, created_by)
                VALUES (:did, :lsid, :name, :description,
                        CAST(:selector AS jsonb), CAST(:query AS jsonb), :uid)
                ON CONFLICT (dataset_id, name) DO NOTHING
                RETURNING id::text
            """),
            {"did": dataset_id, "lsid": logical_sheet_id, "name": name,
             "description": description,
             "selector": json.dumps(version_selector),
             "query": json.dumps(query), "uid": created_by},
        )).mappings().first()
        await s.commit()
    return await get_view(dataset_id, row["id"]) if row else None


async def list_views(dataset_id: str, *, limit: int, offset: int) -> tuple[list[dict], int]:
    async with async_session_factory() as s:
        total = (await s.execute(
            text("SELECT COUNT(*) FROM dataset_views WHERE dataset_id = :did"),
            {"did": dataset_id})).scalar_one()
        rows = (await s.execute(
            text(f"""
                SELECT {_VIEW_COLS} FROM {_VIEW_FROM}
                WHERE v.dataset_id = :did
                ORDER BY v.name LIMIT :limit OFFSET :offset
            """),
            {"did": dataset_id, "limit": limit, "offset": offset},
        )).mappings().all()
        return [dict(r) for r in rows], total


async def get_view(dataset_id: str, view_id: str) -> dict | None:
    from app.shared.repo import is_uuid

    if not is_uuid(view_id):
        return None
    async with async_session_factory() as s:
        row = (await s.execute(
            text(f"""
                SELECT {_VIEW_COLS} FROM {_VIEW_FROM}
                WHERE v.id = :vid AND v.dataset_id = :did
            """),
            {"vid": view_id, "did": dataset_id},
        )).mappings().first()
        return dict(row) if row else None


async def update_view(dataset_id: str, view_id: str, fields: dict) -> dict | None:
    """Update the given columns; None when the view doesn't exist."""
    sets = ["updated_at = now()"]
    params: dict = {"vid": view_id, "did": dataset_id}
    for col in ("name", "description", "logical_sheet_id"):
        if col in fields:
            sets.append(f"{col} = :{col}")
            params[col] = fields[col]
    for col in ("version_selector", "query"):
        if col in fields:
            sets.append(f"{col} = CAST(:{col} AS jsonb)")
            params[col] = json.dumps(fields[col])
    async with async_session_factory() as s:
        row = (await s.execute(
            text(f"""
                UPDATE dataset_views SET {', '.join(sets)}
                WHERE id = :vid AND dataset_id = :did
                RETURNING id::text
            """),
            params,
        )).mappings().first()
        await s.commit()
    return await get_view(dataset_id, view_id) if row else None


async def delete_view(dataset_id: str, view_id: str) -> bool:
    from app.shared.repo import is_uuid

    if not is_uuid(view_id):
        return False
    async with async_session_factory() as s:
        result = await s.execute(
            text("DELETE FROM dataset_views WHERE id = :vid AND dataset_id = :did"),
            {"vid": view_id, "did": dataset_id})
        await s.commit()
        return result.rowcount > 0


async def previous_ready_version(dataset_id: str, before_version_number: int) -> dict | None:
    """The highest ready version strictly below *before_version_number*."""
    async with async_session_factory() as s:
        row = (await s.execute(
            text("""
                SELECT id::text, version_number
                FROM dataset_versions
                WHERE dataset_id = :did AND version_number < :vn
                  AND status = 'ready'
                ORDER BY version_number DESC LIMIT 1
            """),
            {"did": dataset_id, "vn": before_version_number},
        )).mappings().first()
        return dict(row) if row else None
