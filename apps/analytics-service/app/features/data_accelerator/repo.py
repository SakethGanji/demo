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
    "list_tag_history",
    "list_datasets",
    "list_versions",
    "delete_dataset",
]


# ---------------------------------------------------------------------------
# Tags (write operations)
# ---------------------------------------------------------------------------

async def _current_tag_target(s, dataset_id: str, tag_name: str) -> dict | None:
    """The tag's current (version_id, version_number) inside an open session."""
    row = (await s.execute(
        text("""
            SELECT t.version_id::text, dv.version_number
            FROM dataset_version_tags t
            JOIN dataset_versions dv ON dv.id = t.version_id
            WHERE t.dataset_id = :did AND t.tag_name = :tag
        """),
        {"did": dataset_id, "tag": tag_name},
    )).mappings().first()
    return dict(row) if row else None


async def _record_tag_history(
    s,
    dataset_id: str,
    tag_name: str,
    action: str,
    *,
    from_target: dict | None,
    to_version_id: str | None,
    to_version_number: int | None,
    reason: str | None,
    actor_user_id: str | None,
    actor_email: str | None,
    request_id: str | None = None,
) -> None:
    await s.execute(
        text("""
            INSERT INTO dataset_tag_history
                (dataset_id, tag_name, action,
                 from_version_id, from_version_number,
                 to_version_id, to_version_number,
                 reason, actor_user_id, actor_email, request_id)
            VALUES (:did, :tag, :action, :fvid, :fvn, :tvid, :tvn, :reason, :uid, :email, :rid)
        """),
        {"did": dataset_id, "tag": tag_name, "action": action,
         "fvid": from_target["version_id"] if from_target else None,
         "fvn": from_target["version_number"] if from_target else None,
         "tvid": to_version_id, "tvn": to_version_number,
         "reason": reason, "uid": actor_user_id, "email": actor_email,
         "rid": request_id},
    )


async def set_tag(
    dataset_id: str,
    version_id: str,
    tag_name: str,
    created_by: str = DEFAULT_USER_ID,
    *,
    action: str = "set",
    reason: str | None = None,
    actor_email: str | None = None,
    version_number: int | None = None,
    request_id: str | None = None,
) -> dict:
    """Create or move a tag. Upserts on (dataset_id, tag_name).

    Every mutation appends a ``dataset_tag_history`` row (same transaction)
    recording the transition, the actor, and the optional reason.
    """
    async with async_session_factory() as s:
        prev = await _current_tag_target(s, dataset_id, tag_name)
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
        await _record_tag_history(
            s, dataset_id, tag_name, action,
            from_target=prev,
            to_version_id=version_id, to_version_number=version_number,
            reason=reason, actor_user_id=created_by, actor_email=actor_email,
            request_id=request_id,
        )
        await s.commit()
        result = dict(row)
        result["previous_version_number"] = prev["version_number"] if prev else None
        return result


async def delete_tag(
    dataset_id: str,
    tag_name: str,
    *,
    actor_user_id: str | None = None,
    actor_email: str | None = None,
    reason: str | None = None,
    request_id: str | None = None,
) -> bool:
    """Remove a tag (recording the deletion). Returns True if a row was deleted."""
    async with async_session_factory() as s:
        prev = await _current_tag_target(s, dataset_id, tag_name)
        result = await s.execute(
            text("DELETE FROM dataset_version_tags WHERE dataset_id = :did AND tag_name = :tag"),
            {"did": dataset_id, "tag": tag_name},
        )
        if result.rowcount > 0:
            await _record_tag_history(
                s, dataset_id, tag_name, "delete",
                from_target=prev, to_version_id=None, to_version_number=None,
                reason=reason, actor_user_id=actor_user_id, actor_email=actor_email,
                request_id=request_id,
            )
        await s.commit()
        return result.rowcount > 0


async def list_tag_history(
    dataset_id: str, tag_name: str, limit: int = 50, offset: int = 0,
) -> tuple[list[dict], int]:
    """Tag transitions, newest first. Includes entries for deleted tags."""
    async with async_session_factory() as s:
        params = {"did": dataset_id, "tag": tag_name}
        total = (await s.execute(
            text("SELECT COUNT(*) FROM dataset_tag_history WHERE dataset_id = :did AND tag_name = :tag"),
            params,
        )).scalar()
        rows = (await s.execute(
            text("""
                SELECT id, tag_name, action,
                       from_version_number, to_version_number,
                       reason, actor_user_id::text, actor_email, request_id,
                       created_at::text AS created_at
                FROM dataset_tag_history
                WHERE dataset_id = :did AND tag_name = :tag
                ORDER BY id DESC
                LIMIT :limit OFFSET :offset
            """),
            {**params, "limit": limit, "offset": offset},
        )).mappings().all()
        return [dict(r) for r in rows], total


# ---------------------------------------------------------------------------
# Dataset management (moved from files/repo.py)
# ---------------------------------------------------------------------------

async def list_datasets(
    team_ids: list[str] | None = None,
    search: str | None = None,
    limit: int = 50,
    offset: int = 0,
    *,
    domain: str | None = None,
    favorites_user_id: str | None = None,
    include_deprecated: bool = True,
    viewer_user_id: str | None = None,
) -> tuple[list[dict], int]:
    """List datasets, scoped to *team_ids* (None = all teams, for superusers).

    Optional text search, domain filter, favorites-only filter, and
    deprecated exclusion. ``viewer_user_id`` fills the per-row is_favorite
    flag. Returns (rows, total_count).
    """
    async with async_session_factory() as s:
        clauses = [] if team_ids is None else ["d.team_id = ANY(:tids)"]
        params: dict = {"tids": team_ids, "limit": limit, "offset": offset,
                        "uid": viewer_user_id or favorites_user_id}
        if search:
            clauses.append("(d.name ILIKE '%' || :search || '%'"
                           " OR COALESCE(d.description, '') ILIKE '%' || :search || '%')")
            params["search"] = search
        if domain:
            clauses.append("d.domain = :domain")
            params["domain"] = domain
        if not include_deprecated:
            clauses.append("NOT d.deprecated")
        if favorites_user_id:
            clauses.append("EXISTS (SELECT 1 FROM dataset_favorites f "
                           "WHERE f.dataset_id = d.id AND f.user_id = CAST(:uid AS uuid))")
        where = " AND ".join(clauses) or "true"

        total = (await s.execute(
            text(f"SELECT COUNT(*) FROM datasets d WHERE {where}"), params,
        )).scalar()

        rows = (await s.execute(
            text(f"""
                SELECT d.id::text, d.name, d.description, d.classification,
                       d.domain, d.source_system, d.refresh_frequency, d.deprecated,
                       (CAST(:uid AS uuid) IS NOT NULL AND EXISTS (
                           SELECT 1 FROM dataset_favorites f
                           WHERE f.dataset_id = d.id AND f.user_id = CAST(:uid AS uuid)
                       )) AS is_favorite,
                       dv.version_number AS current_version,
                       dv.row_count, dv.size_bytes,
                       d.created_at::text AS created_at,
                       d.updated_at::text AS updated_at
                FROM datasets d
                LEFT JOIN dataset_versions dv ON d.current_version_id = dv.id
                WHERE {where}
                ORDER BY d.updated_at DESC LIMIT :limit OFFSET :offset
            """),
            params,
        )).mappings().all()
        return [dict(r) for r in rows], total


async def search_datasets(
    query: str,
    team_ids: list[str] | None = None,
    limit: int = 20,
    offset: int = 0,
) -> tuple[list[dict], int]:
    """Search datasets, scoped to *team_ids* (None = all teams). Versions + tags inline."""
    async with async_session_factory() as s:
        team_clause = "" if team_ids is None else " AND team_id = ANY(:tids)"
        d_team_clause = "" if team_ids is None else " AND d.team_id = ANY(:tids)"
        base = {"tids": team_ids, "q": query}

        total = (await s.execute(
            text(f"""
                SELECT COUNT(*) FROM datasets
                WHERE (name ILIKE '%' || :q || '%'
                       OR COALESCE(description, '') ILIKE '%' || :q || '%'){team_clause}
            """),
            base,
        )).scalar()

        ds_rows = (await s.execute(
            text(f"""
                SELECT d.id::text, d.name, d.description,
                       d.current_version_id::text,
                       d.created_at::text AS created_at,
                       d.updated_at::text AS updated_at
                FROM datasets d
                WHERE (d.name ILIKE '%' || :q || '%'
                       OR COALESCE(d.description, '') ILIKE '%' || :q || '%'){d_team_clause}
                ORDER BY d.updated_at DESC
                LIMIT :limit OFFSET :offset
            """),
            {**base, "limit": limit, "offset": offset},
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


async def update_dataset(dataset_id: str, **fields) -> dict | None:
    """Patch a dataset's mutable metadata. Only provided (non-None) fields change.

    Returns the updated row, or None if the dataset does not exist.
    """
    import json as _json

    from app.shared.repo import is_uuid
    if not is_uuid(dataset_id):
        return None
    allowed = {"name", "description", "classification", "domain", "source_system",
               "refresh_frequency", "deprecated", "deprecation_reason", "metadata"}
    sets = ["updated_at = now()"]
    params: dict = {"did": dataset_id}
    for key, value in fields.items():
        if key not in allowed or value is None:
            continue
        if key == "metadata":
            sets.append("metadata = CAST(:metadata AS jsonb)")
            params["metadata"] = _json.dumps(value)
        else:
            sets.append(f"{key} = :{key}")
            params[key] = value

    async with async_session_factory() as s:
        row = (await s.execute(
            text(f"""
                UPDATE datasets SET {', '.join(sets)}
                WHERE id = :did
                RETURNING id::text, name, description, classification,
                          domain, source_system, refresh_frequency,
                          deprecated, deprecation_reason, metadata,
                          created_at::text AS created_at,
                          updated_at::text AS updated_at
            """),
            params,
        )).mappings().first()
        await s.commit()
        return dict(row) if row else None


async def list_versions(dataset_id: str) -> list[dict]:
    """List all versions for a dataset, including tags."""
    async with async_session_factory() as s:
        rows = (await s.execute(
            text("""
                SELECT dv.id::text, dv.version_number, dv.status,
                       dv.size_bytes, dv.row_count, dv.sheet_count,
                       dv.checksum, dv.source_checksum, dv.manifest_checksum,
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
