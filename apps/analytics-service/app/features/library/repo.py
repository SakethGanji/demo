"""Library feature — saved analytics definitions, runs, artifacts, lineage."""

from __future__ import annotations

import json

from sqlalchemy import text

from app.infra.db.postgres import async_session_factory
from app.shared.repo import is_uuid

_DEF_COLS = """id::text, dataset_id::text, name, description, kind,
               version_selector, sheet, params, created_by::text,
               created_at::text AS created_at, updated_at::text AS updated_at"""


async def create_definition(dataset_id: str, fields: dict, created_by: str) -> dict:
    async with async_session_factory() as s:
        row = (await s.execute(
            text(f"""
                INSERT INTO analytics_definitions
                    (dataset_id, name, description, kind, version_selector, sheet, params, created_by)
                VALUES (:did, :name, :description, :kind, CAST(:vs AS jsonb), :sheet,
                        CAST(:params AS jsonb), :uid)
                ON CONFLICT (dataset_id, name) DO NOTHING
                RETURNING {_DEF_COLS}
            """),
            {"did": dataset_id, "name": fields["name"],
             "description": fields.get("description"), "kind": fields["kind"],
             "vs": json.dumps(fields.get("version_selector") or {"mode": "current"}),
             "sheet": fields.get("sheet"),
             "params": json.dumps(fields.get("params") or {}),
             "uid": created_by},
        )).mappings().first()
        await s.commit()
        return dict(row) if row else None


async def list_definitions(dataset_id: str) -> list[dict]:
    async with async_session_factory() as s:
        rows = (await s.execute(
            text(f"SELECT {_DEF_COLS} FROM analytics_definitions "
                 f"WHERE dataset_id = :did ORDER BY name"),
            {"did": dataset_id},
        )).mappings().all()
        return [dict(r) for r in rows]


async def get_definition(dataset_id: str, definition_id: str) -> dict | None:
    if not is_uuid(definition_id):
        return None
    async with async_session_factory() as s:
        row = (await s.execute(
            text(f"SELECT {_DEF_COLS} FROM analytics_definitions "
                 f"WHERE id = :id AND dataset_id = :did"),
            {"id": definition_id, "did": dataset_id},
        )).mappings().first()
        return dict(row) if row else None


async def update_definition(dataset_id: str, definition_id: str, fields: dict) -> dict | None:
    allowed = {"name", "description", "version_selector", "sheet", "params"}
    updates = {k: v for k, v in fields.items() if k in allowed}
    if not updates or not is_uuid(definition_id):
        return await get_definition(dataset_id, definition_id)
    sets, params = ["updated_at = now()"], {"id": definition_id, "did": dataset_id}
    for k, v in updates.items():
        if k in ("version_selector", "params"):
            sets.append(f"{k} = CAST(:{k} AS jsonb)")
            params[k] = json.dumps(v or {})
        else:
            sets.append(f"{k} = :{k}")
            params[k] = v
    async with async_session_factory() as s:
        row = (await s.execute(
            text(f"UPDATE analytics_definitions SET {', '.join(sets)} "
                 f"WHERE id = :id AND dataset_id = :did RETURNING {_DEF_COLS}"),
            params,
        )).mappings().first()
        await s.commit()
        return dict(row) if row else None


async def delete_definition(dataset_id: str, definition_id: str) -> bool:
    if not is_uuid(definition_id):
        return False
    async with async_session_factory() as s:
        result = await s.execute(
            text("DELETE FROM analytics_definitions WHERE id = :id AND dataset_id = :did"),
            {"id": definition_id, "did": dataset_id},
        )
        await s.commit()
        return result.rowcount > 0


# ---------------------------------------------------------------------------
# Artifacts
# ---------------------------------------------------------------------------

async def create_artifact(
    storage_key: str, artifact_type: str, *,
    format: str | None = None, media_type: str | None = None,
    size_bytes: int | None = None, checksum: str | None = None,
    created_by: str | None = None,
) -> dict:
    async with async_session_factory() as s:
        row = (await s.execute(
            text("""
                INSERT INTO artifacts (storage_key, artifact_type, format, media_type,
                                       size_bytes, checksum, created_by)
                VALUES (:sk, :at, :fmt, :mt, :sb, :cs, :uid)
                RETURNING id::text, storage_key, artifact_type, format, media_type,
                          size_bytes, checksum, created_at::text AS created_at
            """),
            {"sk": storage_key, "at": artifact_type, "fmt": format, "mt": media_type,
             "sb": size_bytes, "cs": checksum, "uid": created_by},
        )).mappings().one()
        await s.commit()
        return dict(row)


async def get_artifact(artifact_id: str) -> dict | None:
    if not is_uuid(artifact_id):
        return None
    async with async_session_factory() as s:
        row = (await s.execute(
            text("""SELECT id::text, storage_key, artifact_type, format, media_type,
                           size_bytes, checksum, created_at::text AS created_at
                    FROM artifacts WHERE id = :id"""),
            {"id": artifact_id},
        )).mappings().first()
        return dict(row) if row else None


# ---------------------------------------------------------------------------
# Analytics runs
# ---------------------------------------------------------------------------

_RUN_COLS = """id::text, definition_id::text, dataset_version_id::text, job_id::text,
               status, result_summary, artifact_id::text, triggered_by::text,
               started_at::text AS started_at, completed_at::text AS completed_at, error"""


async def create_run(definition_id: str, version_id: str | None, job_id: str | None,
                     triggered_by: str | None) -> dict:
    async with async_session_factory() as s:
        row = (await s.execute(
            text(f"""
                INSERT INTO analytics_runs (definition_id, dataset_version_id, job_id, triggered_by)
                VALUES (:defid, :vid, :jid, :uid)
                RETURNING {_RUN_COLS}
            """),
            {"defid": definition_id, "vid": version_id, "jid": job_id, "uid": triggered_by},
        )).mappings().one()
        await s.commit()
        return dict(row)


async def complete_run(run_id: str, *, result_summary: dict | None,
                       artifact_id: str | None) -> dict:
    async with async_session_factory() as s:
        row = (await s.execute(
            text(f"""
                UPDATE analytics_runs
                SET status = 'completed', completed_at = now(),
                    result_summary = CAST(:summary AS jsonb), artifact_id = :aid
                WHERE id = :id
                RETURNING {_RUN_COLS}
            """),
            {"id": run_id, "summary": json.dumps(result_summary) if result_summary else None,
             "aid": artifact_id},
        )).mappings().one()
        await s.commit()
        return dict(row)


async def fail_run(run_id: str, error: str) -> None:
    async with async_session_factory() as s:
        await s.execute(
            text("UPDATE analytics_runs SET status = 'failed', error = :e, "
                 "completed_at = now() WHERE id = :id"),
            {"id": run_id, "e": error},
        )
        await s.commit()


async def list_runs(definition_id: str, limit: int = 50, offset: int = 0) -> tuple[list[dict], int]:
    async with async_session_factory() as s:
        total = (await s.execute(
            text("SELECT COUNT(*) FROM analytics_runs WHERE definition_id = :d"),
            {"d": definition_id},
        )).scalar()
        rows = (await s.execute(
            text(f"SELECT {_RUN_COLS} FROM analytics_runs WHERE definition_id = :d "
                 f"ORDER BY started_at DESC LIMIT :limit OFFSET :offset"),
            {"d": definition_id, "limit": limit, "offset": offset},
        )).mappings().all()
        return [dict(r) for r in rows], total


async def get_run(run_id: str) -> dict | None:
    if not is_uuid(run_id):
        return None
    async with async_session_factory() as s:
        row = (await s.execute(
            text("""
                SELECT r.id::text, r.definition_id::text, r.dataset_version_id::text,
                       r.job_id::text, r.status, r.result_summary, r.artifact_id::text,
                       r.triggered_by::text, r.started_at::text AS started_at,
                       r.completed_at::text AS completed_at, r.error,
                       d.dataset_id::text AS dataset_id, d.kind, d.sheet
                FROM analytics_runs r
                JOIN analytics_definitions d ON d.id = r.definition_id
                WHERE r.id = :id
            """),
            {"id": run_id},
        )).mappings().first()
        return dict(row) if row else None


# ---------------------------------------------------------------------------
# Lineage
# ---------------------------------------------------------------------------

async def record_lineage(
    dataset_id: str, version_id: str, *,
    parent_dataset_id: str, parent_version_id: str,
    parent_dataset_name: str | None, parent_version_number: int | None,
    parent_sheet_key: str | None, relation: str,
) -> None:
    async with async_session_factory() as s:
        await s.execute(
            text("""
                INSERT INTO dataset_lineage
                    (dataset_id, dataset_version_id, parent_dataset_id, parent_version_id,
                     parent_sheet_key, relation, parent_dataset_name, parent_version_number)
                VALUES (:did, :vid, :pdid, :pvid, :psk, :rel, :pdn, :pvn)
            """),
            {"did": dataset_id, "vid": version_id, "pdid": parent_dataset_id,
             "pvid": parent_version_id, "psk": parent_sheet_key, "rel": relation,
             "pdn": parent_dataset_name, "pvn": parent_version_number},
        )
        await s.commit()


async def get_lineage(dataset_id: str) -> dict:
    """Parents of this dataset's versions + children derived from it."""
    async with async_session_factory() as s:
        parents = (await s.execute(
            text("""
                SELECT l.id::text, l.dataset_version_id::text, dv.version_number,
                       l.parent_dataset_id::text, l.parent_version_id::text,
                       l.parent_dataset_name, l.parent_version_number,
                       l.parent_sheet_key, l.relation, l.created_at::text AS created_at
                FROM dataset_lineage l
                JOIN dataset_versions dv ON dv.id = l.dataset_version_id
                WHERE l.dataset_id = :did
                ORDER BY l.created_at DESC
            """),
            {"did": dataset_id},
        )).mappings().all()
        children = (await s.execute(
            text("""
                SELECT l.id::text, l.dataset_id::text AS child_dataset_id,
                       d.name AS child_dataset_name,
                       l.dataset_version_id::text AS child_version_id,
                       dv.version_number AS child_version_number,
                       l.parent_version_number, l.parent_sheet_key, l.relation,
                       l.created_at::text AS created_at
                FROM dataset_lineage l
                JOIN datasets d ON d.id = l.dataset_id
                JOIN dataset_versions dv ON dv.id = l.dataset_version_id
                WHERE l.parent_dataset_id = :did
                ORDER BY l.created_at DESC
            """),
            {"did": dataset_id},
        )).mappings().all()
        return {"parents": [dict(r) for r in parents],
                "children": [dict(r) for r in children]}
