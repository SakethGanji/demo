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
    "count_cascading_logical_sheet_state",
]


# ---------------------------------------------------------------------------
# Logical sheet identity — cascade accounting for confirm-rename
# ---------------------------------------------------------------------------

async def count_cascading_logical_sheet_state(logical_sheet_id: str) -> dict[str, int]:
    """State that a DELETE of this logical sheet would destroy outright.

    ``app.shared.repo.count_logical_sheet_state`` covers the three tables whose
    FK is ``ON DELETE SET NULL`` (sheet metadata, quality rules) or that predate
    the rest (column metadata). Everything counted here is ``ON DELETE
    CASCADE`` on ``dataset_sheets(id)``, so confirming a rename — which deletes
    the spurious identity — silently *destroys* it rather than orphaning it:

    * ``transformation_definitions`` (and, transitively, every
      ``transformation_run`` row that references the definition),
    * ``dataset_views`` (saved views),
    * ``dataset_relationships`` on either endpoint.

    The migrations note that these tables key on ``logical_sheet_id`` so a
    rename "needs no rewriting". That is true for the SURVIVING identity; it is
    exactly wrong for the spurious one the rename folds away, which is the row
    that gets deleted.

    Deliberately excluded: ``profile_runs``, which are machine-derived,
    idempotently recomputable per (version, sheet, algorithm) and have no user
    authored content to lose.
    """
    async with async_session_factory() as s:
        async def _count(sql: str) -> int:
            return int((await s.execute(text(sql), {"id": logical_sheet_id})).scalar() or 0)

        return {
            "transformations": await _count(
                "SELECT COUNT(*) FROM transformation_definitions "
                "WHERE logical_sheet_id = :id"),
            "transformation_runs": await _count(
                "SELECT COUNT(*) FROM transformation_runs r "
                "JOIN transformation_definitions d ON d.id = r.definition_id "
                "WHERE d.logical_sheet_id = :id"),
            "saved_views": await _count(
                "SELECT COUNT(*) FROM dataset_views WHERE logical_sheet_id = :id"),
            "relationships": await _count(
                "SELECT COUNT(*) FROM dataset_relationships "
                "WHERE from_logical_sheet_id = :id OR to_logical_sheet_id = :id"),
        }


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


async def previous_tag_version_number(
    dataset_id: str, tag_name: str, current_version_number: int,
) -> int | None:
    """The most recent version this tag pointed at that isn't the current one.

    Resolved in SQL rather than by scanning a page of ``list_tag_history``.
    Nothing dedupes a no-op promote, so a tag re-promoted to the same version
    (CI re-running a release job) accumulates history rows that all name the
    current target; a bounded scan would run out of rows and report "no
    previous version" for a tag that plainly has one. This predicate is exact
    however deep the run of same-target entries goes.

    ``action = 'delete'`` rows carry a NULL target and are skipped by the
    ``IS NOT NULL`` test, so no extra clause is needed for them.
    """
    async with async_session_factory() as s:
        return (await s.execute(
            text("""
                SELECT to_version_number
                FROM dataset_tag_history
                WHERE dataset_id = :did AND tag_name = :tag
                  AND to_version_number IS NOT NULL
                  AND to_version_number <> :current
                ORDER BY id DESC
                LIMIT 1
            """),
            {"did": dataset_id, "tag": tag_name, "current": current_version_number},
        )).scalar()


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
    validation_status: str | None = None,
    has_schema_drift: bool | None = None,
    documentation: str | None = None,
) -> tuple[list[dict], int]:
    """List datasets, scoped to *team_ids* (None = all teams, for superusers).

    Optional text search, domain filter, favorites-only filter, deprecated
    exclusion, and the §18 catalog-signal filters (validation status, schema
    drift, documentation completeness — same signals as ``/health``).
    ``viewer_user_id`` fills the per-row is_favorite flag. Returns
    (rows, total_count).
    """
    from app.features.discovery.repo import signals_lateral

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
        if validation_status:
            clauses.append("sig.validation_status = :vstatus")
            params["vstatus"] = validation_status
        if has_schema_drift is not None:
            clauses.append("sig.has_schema_drift = :drift")
            params["drift"] = has_schema_drift
        if documentation:
            clauses.append("sig.documentation = :doc")
            params["doc"] = documentation
        where = " AND ".join(clauses) or "true"
        sig = signals_lateral()

        total = (await s.execute(
            text(f"SELECT COUNT(*) FROM datasets d {sig} WHERE {where}"), params,
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
                       sig.validation_status, sig.has_schema_drift, sig.documentation,
                       d.created_at::text AS created_at,
                       d.updated_at::text AS updated_at
                FROM datasets d
                LEFT JOIN dataset_versions dv ON d.current_version_id = dv.id
                {sig}
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


#: Columns PATCH /datasets/{id} may assign, in the order the response lists them.
_DS_PATCH_COLS = """id::text, name, description, classification,
                    domain, source_system, refresh_frequency,
                    deprecated, deprecation_reason, metadata,
                    created_at::text AS created_at,
                    updated_at::text AS updated_at"""

#: Assignable columns. ``metadata`` is jsonb and needs its own cast, so it is
#: not in this list.
_DS_PATCH_SCALARS = ("name", "description", "classification", "domain",
                     "source_system", "refresh_frequency", "deprecated",
                     "deprecation_reason")


async def get_dataset_patch_view(dataset_id: str) -> dict | None:
    """The PATCH-shaped projection of one dataset (used for no-op PATCHes)."""
    from app.shared.repo import is_uuid
    if not is_uuid(dataset_id):
        return None
    async with async_session_factory() as s:
        row = (await s.execute(
            text(f"SELECT {_DS_PATCH_COLS} FROM datasets WHERE id = :did"),
            {"did": dataset_id},
        )).mappings().first()
        return dict(row) if row else None


async def update_dataset(dataset_id: str, fields: dict) -> dict | None:
    """Partial write (PATCH): assign ONLY the keys present in *fields*.

    *fields* must come from ``model_dump(exclude_unset=True)``. That is what
    separates the two cases the client can express:

    - key ABSENT from the body   -> absent from *fields* -> no SET clause is
      emitted for that column, so the stored value survives.
    - key sent as explicit null  -> present in *fields* with value ``None``
      -> ``col = NULL`` is emitted, so the field is cleared.

    Same reasoning as ``discovery.repo.update_sheet_metadata``: the UPDATE is
    built column by column rather than reusing a ``COALESCE(:new, existing)``
    idiom, because once an explicit null has been flattened into a bind
    parameter COALESCE can no longer tell it apart from "not supplied" and
    would silently ignore the clear. Filtering on ``value is not None`` — what
    this function used to do — has exactly the same effect.

    ``name``/``classification``/``deprecated``/``metadata`` back NOT NULL
    columns; ``UpdateDatasetRequest`` rejects an explicit null for those before
    it gets here, so no clause here can violate the constraint.

    Returns the updated row, or None if the dataset does not exist.
    """
    import json as _json

    from app.shared.repo import is_uuid
    if not is_uuid(dataset_id):
        return None
    sets: list[str] = []
    params: dict = {"did": dataset_id}
    for key in _DS_PATCH_SCALARS:
        if key in fields:
            sets.append(f"{key} = :{key}")
            params[key] = fields[key]
    if "metadata" in fields:
        sets.append("metadata = CAST(:metadata AS jsonb)")
        params["metadata"] = _json.dumps(fields["metadata"])
    if not sets:  # nothing to change — a no-op PATCH is not an error
        return await get_dataset_patch_view(dataset_id)

    async with async_session_factory() as s:
        row = (await s.execute(
            text(f"""
                UPDATE datasets SET {', '.join(sets)}, updated_at = now()
                WHERE id = :did
                RETURNING {_DS_PATCH_COLS}
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
