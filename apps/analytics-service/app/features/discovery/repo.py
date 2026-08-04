"""Discovery feature — column search, facets, favorites, sheet metadata, usage."""

from __future__ import annotations

import json

from sqlalchemy import text

from app.infra.db.postgres import async_session_factory


async def search_columns(
    q: str, team_ids: list[str] | None, limit: int = 50, offset: int = 0,
) -> tuple[list[dict], int]:
    """Search columns of current versions by name (physical/original/normalized).

    Runs entirely on the schemas Phase 1 put in Postgres — no file I/O.
    """
    team_clause = "" if team_ids is None else " AND d.team_id = ANY(:tids)"
    base = f"""
        FROM datasets d
        JOIN dataset_versions dv ON dv.id = d.current_version_id
        JOIN dataset_version_sheets s ON s.dataset_version_id = dv.id
        CROSS JOIN LATERAL jsonb_array_elements(COALESCE(s.schema_json, '[]'::jsonb)) AS col
        WHERE (col->>'normalized_name' ILIKE '%' || :q || '%'
               OR col->>'name' ILIKE '%' || :q || '%'
               OR col->>'original_name' ILIKE '%' || :q || '%'){team_clause}
    """
    params: dict = {"q": q, "tids": team_ids}
    async with async_session_factory() as s:
        total = (await s.execute(text(f"SELECT COUNT(*) {base}"), params)).scalar()
        rows = (await s.execute(
            text(f"""
                SELECT d.id::text AS dataset_id, d.name AS dataset_name,
                       d.domain, s.sheet_name, s.sheet_key,
                       col->>'name' AS column_name,
                       col->>'normalized_name' AS normalized_name,
                       col->>'dtype' AS dtype,
                       (col->>'position')::int AS position
                {base}
                ORDER BY d.name, s.sheet_index, (col->>'position')::int
                LIMIT :limit OFFSET :offset
            """),
            {**params, "limit": limit, "offset": offset},
        )).mappings().all()
        return [dict(r) for r in rows], total


async def facets(team_ids: list[str] | None) -> dict:
    """Dataset counts by classification / domain / source_system (team-scoped)."""
    team_clause = "" if team_ids is None else " WHERE team_id = ANY(:tids)"
    out: dict = {}
    async with async_session_factory() as s:
        for field in ("classification", "domain", "source_system"):
            rows = (await s.execute(
                text(f"SELECT {field} AS v, COUNT(*) AS n FROM datasets"
                     f"{team_clause} GROUP BY {field} ORDER BY n DESC"),
                {"tids": team_ids},
            )).all()
            out[field] = {r.v: r.n for r in rows if r.v is not None}
        rows = (await s.execute(
            text(f"SELECT deprecated, COUNT(*) AS n FROM datasets"
                 f"{team_clause} GROUP BY deprecated"),
            {"tids": team_ids},
        )).all()
        out["deprecated"] = {str(r.deprecated).lower(): r.n for r in rows}
    return out


# ---------------------------------------------------------------------------
# Favorites
# ---------------------------------------------------------------------------

async def set_favorite(user_id: str, dataset_id: str) -> None:
    async with async_session_factory() as s:
        await s.execute(
            text("INSERT INTO dataset_favorites (user_id, dataset_id) "
                 "VALUES (:uid, :did) ON CONFLICT DO NOTHING"),
            {"uid": user_id, "did": dataset_id},
        )
        await s.commit()


async def unset_favorite(user_id: str, dataset_id: str) -> bool:
    async with async_session_factory() as s:
        result = await s.execute(
            text("DELETE FROM dataset_favorites WHERE user_id = :uid AND dataset_id = :did"),
            {"uid": user_id, "did": dataset_id},
        )
        await s.commit()
        return result.rowcount > 0


# ---------------------------------------------------------------------------
# Sheet-level semantic metadata
# ---------------------------------------------------------------------------

_SM_COLS = """id::text, dataset_id::text, sheet_key, grain, primary_key_columns,
              description, updated_by::text, updated_at::text AS updated_at"""


async def upsert_sheet_metadata(
    dataset_id: str, sheet_key: str, fields: dict, updated_by: str,
) -> dict:
    async with async_session_factory() as s:
        row = (await s.execute(
            text(f"""
                INSERT INTO dataset_sheet_metadata
                    (dataset_id, sheet_key, grain, primary_key_columns, description, updated_by)
                VALUES (:did, :key, :grain, CAST(:pk AS jsonb), :description, :uid)
                ON CONFLICT (dataset_id, sheet_key) DO UPDATE
                    SET grain = EXCLUDED.grain,
                        primary_key_columns = EXCLUDED.primary_key_columns,
                        description = EXCLUDED.description,
                        updated_by = EXCLUDED.updated_by,
                        updated_at = now()
                RETURNING {_SM_COLS}
            """),
            {"did": dataset_id, "key": sheet_key,
             "grain": fields.get("grain"),
             "pk": json.dumps(fields.get("primary_key_columns"))
                   if fields.get("primary_key_columns") is not None else None,
             "description": fields.get("description"), "uid": updated_by},
        )).mappings().one()
        await s.commit()
        return dict(row)


async def list_sheet_metadata(dataset_id: str) -> list[dict]:
    async with async_session_factory() as s:
        rows = (await s.execute(
            text(f"SELECT {_SM_COLS} FROM dataset_sheet_metadata "
                 f"WHERE dataset_id = :did ORDER BY sheet_key"),
            {"did": dataset_id},
        )).mappings().all()
        return [dict(r) for r in rows]


# ---------------------------------------------------------------------------
# Usage (derived from the audit trail)
# ---------------------------------------------------------------------------

async def dataset_usage(dataset_id: str) -> dict:
    """Read/write/download counts + last activity, from the audit log."""
    async with async_session_factory() as s:
        row = (await s.execute(
            text("""
                SELECT
                    COUNT(*) FILTER (WHERE path LIKE '%/download')       AS downloads,
                    COUNT(*) FILTER (WHERE method IN ('POST','PUT','PATCH','DELETE'))
                                                                          AS writes,
                    COUNT(*)                                              AS total_events,
                    MAX(occurred_at)::text                                AS last_activity_at
                FROM audit_log
                WHERE path LIKE '%/datasets/' || :did || '%'
            """),
            {"did": dataset_id},
        )).mappings().one()
        return dict(row)
