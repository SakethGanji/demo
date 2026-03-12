"""Data accelerator feature — dataset metadata, tag, and version operations.

Shared read queries live in shared.repo and are re-exported here.
Tag write operations and dataset management (list/delete/versions) live here.
"""

from __future__ import annotations

from sqlalchemy import text

from app.infra.db.postgres import async_session_factory
from app.shared.repo import (
    DEFAULT_TEAM_ID,
    DEFAULT_USER_ID,
    get_current_version,
    get_dataset,
    get_version,
    get_version_by_number,
    list_tags_for_dataset,
    list_tags_for_version,
    get_version_by_tag,
)

__all__ = [
    "get_dataset",
    "get_version",
    "get_current_version",
    "list_tags_for_dataset",
    "list_tags_for_version",
    "get_version_by_number",
    "get_version_by_tag",
    "set_tag",
    "delete_tag",
    "list_datasets",
    "list_versions",
    "delete_dataset",
]


# ---------------------------------------------------------------------------
# Tags (write operations)
# ---------------------------------------------------------------------------

async def set_tag(
    dataset_id: str,
    version_id: str,
    tag_name: str,
    created_by: str = DEFAULT_USER_ID,
) -> dict:
    """Create or move a tag. Upserts on (dataset_id, tag_name)."""
    async with async_session_factory() as s:
        row = (await s.execute(
            text("""
                INSERT INTO dataset_version_tags (dataset_id, version_id, tag_name, created_by)
                VALUES (:did, :vid, :tag, :uid)
                ON CONFLICT (dataset_id, tag_name) DO UPDATE
                    SET version_id = EXCLUDED.version_id,
                        updated_at = now()
                RETURNING id::text, dataset_id::text, version_id::text,
                          tag_name, created_at::text, updated_at::text
            """),
            {"did": dataset_id, "vid": version_id, "tag": tag_name, "uid": created_by},
        )).mappings().one()
        await s.commit()
        return dict(row)


async def delete_tag(dataset_id: str, tag_name: str) -> bool:
    """Remove a tag. Returns True if a row was deleted."""
    async with async_session_factory() as s:
        result = await s.execute(
            text("DELETE FROM dataset_version_tags WHERE dataset_id = :did AND tag_name = :tag"),
            {"did": dataset_id, "tag": tag_name},
        )
        await s.commit()
        return result.rowcount > 0


# ---------------------------------------------------------------------------
# Dataset management (moved from files/repo.py)
# ---------------------------------------------------------------------------

async def list_datasets(
    team_id: str = DEFAULT_TEAM_ID,
    search: str | None = None,
    limit: int = 50,
    offset: int = 0,
) -> tuple[list[dict], int]:
    """List datasets with optional text search on name + description. Returns (rows, total_count)."""
    async with async_session_factory() as s:
        count_sql = """
            SELECT COUNT(*) FROM datasets
            WHERE team_id = :tid
        """
        params: dict = {"tid": team_id, "limit": limit, "offset": offset}
        if search:
            count_sql += " AND (name ILIKE '%' || :search || '%' OR COALESCE(description, '') ILIKE '%' || :search || '%')"
            params["search"] = search
        total = (await s.execute(text(count_sql), params)).scalar()

        query_sql = """
            SELECT d.id::text, d.name, d.description,
                   dv.version_number AS current_version,
                   dv.row_count, dv.size_bytes,
                   d.created_at::text AS created_at,
                   d.updated_at::text AS updated_at
            FROM datasets d
            LEFT JOIN dataset_versions dv ON d.current_version_id = dv.id
            WHERE d.team_id = :tid
        """
        if search:
            query_sql += " AND (d.name ILIKE '%' || :search || '%' OR COALESCE(d.description, '') ILIKE '%' || :search || '%')"
        query_sql += " ORDER BY d.updated_at DESC LIMIT :limit OFFSET :offset"

        rows = (await s.execute(text(query_sql), params)).mappings().all()
        return [dict(r) for r in rows], total


async def search_datasets(
    query: str,
    team_id: str = DEFAULT_TEAM_ID,
    limit: int = 20,
    offset: int = 0,
) -> tuple[list[dict], int]:
    """Search datasets by name/description and return results with versions + tags inline."""
    async with async_session_factory() as s:
        # Count matches
        total = (await s.execute(
            text("""
                SELECT COUNT(*) FROM datasets
                WHERE team_id = :tid
                  AND (name ILIKE '%' || :q || '%'
                       OR COALESCE(description, '') ILIKE '%' || :q || '%')
            """),
            {"tid": team_id, "q": query},
        )).scalar()

        # Fetch matching datasets
        ds_rows = (await s.execute(
            text("""
                SELECT d.id::text, d.name, d.description,
                       d.current_version_id::text,
                       d.created_at::text AS created_at,
                       d.updated_at::text AS updated_at
                FROM datasets d
                WHERE d.team_id = :tid
                  AND (d.name ILIKE '%' || :q || '%'
                       OR COALESCE(d.description, '') ILIKE '%' || :q || '%')
                ORDER BY d.updated_at DESC
                LIMIT :limit OFFSET :offset
            """),
            {"tid": team_id, "q": query, "limit": limit, "offset": offset},
        )).mappings().all()

        if not ds_rows:
            return [], total

        dataset_ids = [r["id"] for r in ds_rows]

        # Fetch all versions for matched datasets in one query
        ver_rows = (await s.execute(
            text("""
                SELECT dv.id::text, dv.dataset_id::text, dv.version_number,
                       dv.status, dv.size_bytes, dv.row_count, dv.checksum,
                       dv.created_at::text AS created_at,
                       dv.processed_at::text AS processed_at,
                       COALESCE(
                           (SELECT array_agg(t.tag_name ORDER BY t.tag_name)
                            FROM dataset_version_tags t
                            WHERE t.version_id = dv.id),
                           ARRAY[]::text[]
                       ) AS tags
                FROM dataset_versions dv
                WHERE dv.dataset_id = ANY(:dids)
                ORDER BY dv.dataset_id, dv.version_number DESC
            """),
            {"dids": dataset_ids},
        )).mappings().all()

        # Group versions by dataset_id
        versions_by_ds: dict[str, list[dict]] = {}
        for v in ver_rows:
            v_dict = dict(v)
            ds_id = v_dict.pop("dataset_id")
            versions_by_ds.setdefault(ds_id, []).append(v_dict)

        results = []
        for ds in ds_rows:
            d = dict(ds)
            d["versions"] = versions_by_ds.get(d["id"], [])
            results.append(d)

        return results, total


async def list_versions(dataset_id: str) -> list[dict]:
    """List all versions for a dataset, including tags."""
    async with async_session_factory() as s:
        rows = (await s.execute(
            text("""
                SELECT dv.id::text, dv.version_number, dv.status,
                       dv.size_bytes, dv.row_count, dv.checksum,
                       dv.created_at::text AS created_at,
                       dv.processed_at::text AS processed_at,
                       COALESCE(
                           (SELECT array_agg(t.tag_name ORDER BY t.tag_name)
                            FROM dataset_version_tags t
                            WHERE t.version_id = dv.id),
                           ARRAY[]::text[]
                       ) AS tags
                FROM dataset_versions dv
                WHERE dv.dataset_id = :did
                ORDER BY dv.version_number DESC
            """),
            {"did": dataset_id},
        )).mappings().all()
        return [dict(r) for r in rows]


async def delete_dataset(dataset_id: str) -> list[str]:
    """Delete a dataset and all versions from DB. Returns version paths for storage cleanup."""
    async with async_session_factory() as s:
        rows = (await s.execute(
            text("SELECT path FROM dataset_versions WHERE dataset_id = :did AND path IS NOT NULL"),
            {"did": dataset_id},
        )).all()
        paths = [r[0] for r in rows]

        await s.execute(
            text("DELETE FROM dataset_version_tags WHERE dataset_id = :did"),
            {"did": dataset_id},
        )
        await s.execute(
            text("DELETE FROM jobs WHERE dataset_id = :did"),
            {"did": dataset_id},
        )
        await s.execute(
            text("DELETE FROM dataset_versions WHERE dataset_id = :did"),
            {"did": dataset_id},
        )
        await s.execute(
            text("DELETE FROM datasets WHERE id = :did"),
            {"did": dataset_id},
        )
        await s.commit()
        return paths
