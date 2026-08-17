"""Discovery feature — column search, facets, favorites, sheet metadata, usage."""

from __future__ import annotations

import json

from sqlalchemy import text

from app.infra.db.postgres import async_session_factory
from app.shared import request_effects
from app.shared.repo import is_uuid


def like_contains(q: str) -> str:
    """Turn user text into a ``%…%`` ILIKE pattern that matches it LITERALLY.

    Binding the query as a parameter stops SQL injection but does NOT stop
    pattern injection: Postgres still reads ``%`` and ``_`` as wildcards inside
    whatever pattern the value ends up in. Unescaped, a search for ``a_b``
    silently matches ``axb``, and a search for ``%`` matches the caller's entire
    visible catalog while looking like a real hit list — a wrong answer the user
    cannot tell from a right one.

    Backslash is LIKE's default escape character in Postgres, so escaping here
    needs no ``ESCAPE`` clause; the whole pattern is bound as one value so the
    metacharacters we add are the only ones the planner sees.
    """
    escaped = q.replace("\\", "\\\\").replace("%", r"\%").replace("_", r"\_")
    return f"%{escaped}%"


async def search_columns(
    q: str, team_ids: list[str] | None, limit: int = 50, offset: int = 0,
) -> tuple[list[dict], int]:
    """Search columns of current versions by name (physical/original/normalized).

    Runs entirely on the schemas Phase 1 put in Postgres — no file I/O.
    The query is matched as a literal substring (see ``like_contains``).
    """
    team_clause = "" if team_ids is None else " AND d.team_id = ANY(:tids)"
    base = f"""
        FROM datasets d
        JOIN dataset_versions dv ON dv.id = d.current_version_id
        JOIN dataset_version_sheets s ON s.dataset_version_id = dv.id
        CROSS JOIN LATERAL jsonb_array_elements(COALESCE(s.schema_json, '[]'::jsonb)) AS col
        WHERE (col->>'normalized_name' ILIKE :q
               OR col->>'name' ILIKE :q
               OR col->>'original_name' ILIKE :q){team_clause}
    """
    params: dict = {"q": like_contains(q), "tids": team_ids}
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


def signals_lateral(alias: str = "d") -> str:
    """A ``CROSS JOIN LATERAL ... sig`` exposing the §18 catalog signals for the
    dataset row aliased ``alias``:

    - ``validation_status`` — ``passed`` / ``failed`` / ``none`` (latest completed
      validation run for the current version; error-level failures ⇒ failed).
    - ``has_schema_drift`` — bool: any schema-fingerprint churn across a logical
      sheet in the last ten ready versions.
    - ``documentation`` — ``full`` / ``partial`` / ``none``.

    Every signal is derived from the SAME sources the §17 health dimensions use
    (``evaluate_validation`` / ``evaluate_schema_stability`` /
    ``evaluate_documentation`` + ``documentation_stats``) so the catalog and
    ``/datasets/{id}/health`` can never disagree. Reused by ``facets`` and by
    ``data_accelerator.repo.list_datasets`` for both filtering and display.
    """
    from .health import DOC_COLUMN_COVERAGE  # local: health imports this module

    d = alias
    return f"""
    CROSS JOIN LATERAL (
        SELECT x.validation_status, x.has_schema_drift,
               CASE
                 WHEN x.has_desc AND x.has_dom AND x.ts > 0 AND x.ds >= x.ts
                      AND (x.tc = 0 OR x.dc::float / NULLIF(x.tc, 0) >= {DOC_COLUMN_COVERAGE})
                   THEN 'full'
                 WHEN NOT x.has_desc AND NOT x.has_dom AND x.ds = 0 AND x.dc = 0
                   THEN 'none'
                 ELSE 'partial'
               END AS documentation
        FROM (
          SELECT
            COALESCE((
              SELECT CASE WHEN COALESCE(vr.error_failures, 0) > 0
                          THEN 'failed' ELSE 'passed' END
              FROM validation_runs vr
              WHERE vr.dataset_id = {d}.id
                AND vr.dataset_version_id = {d}.current_version_id
                AND vr.status = 'completed'
              ORDER BY vr.started_at DESC LIMIT 1), 'none') AS validation_status,
            EXISTS (
              SELECT 1 FROM (
                SELECT COALESCE(s.logical_sheet_id::text, 'key:' || s.sheet_name) AS lk,
                       s.schema_fingerprint AS fp
                FROM (SELECT id FROM dataset_versions
                      WHERE dataset_id = {d}.id AND status = 'ready'
                      ORDER BY version_number DESC LIMIT 10) v
                JOIN dataset_version_sheets s ON s.dataset_version_id = v.id
                WHERE s.schema_fingerprint IS NOT NULL
              ) f GROUP BY f.lk HAVING COUNT(DISTINCT f.fp) > 1
            ) AS has_schema_drift,
            ({d}.description IS NOT NULL AND {d}.description <> '') AS has_desc,
            ({d}.domain IS NOT NULL AND {d}.domain <> '') AS has_dom,
            (SELECT COUNT(*) FROM dataset_sheets ls
              WHERE ls.dataset_id = {d}.id AND ls.retired_at IS NULL) AS ts,
            (SELECT COUNT(*) FROM dataset_sheet_metadata m
               JOIN dataset_sheets ls ON ls.id = m.logical_sheet_id
                AND ls.retired_at IS NULL
              WHERE m.dataset_id = {d}.id
                AND (m.grain IS NOT NULL OR m.primary_key_columns IS NOT NULL
                     OR m.description IS NOT NULL)) AS ds,
            (SELECT COUNT(*) FROM dataset_column_metadata c
               JOIN dataset_sheets ls ON ls.id = c.logical_sheet_id
                AND ls.retired_at IS NULL
              WHERE c.dataset_id = {d}.id
                AND (c.business_name IS NOT NULL OR c.description IS NOT NULL
                     OR c.semantic_type IS NOT NULL OR c.unit IS NOT NULL
                     OR c.sensitivity IS NOT NULL
                     OR c.allowed_values IS NOT NULL)) AS dc,
            (SELECT COALESCE(SUM(jsonb_array_length(
                       COALESCE(s2.schema_json, '[]'::jsonb))), 0)
               FROM dataset_version_sheets s2
              WHERE s2.dataset_version_id = {d}.current_version_id) AS tc
        ) x
    ) sig
    """


async def facets(team_ids: list[str] | None) -> dict:
    """Dataset counts by classification / domain / source_system + §18 signals
    (validation status, schema drift, documentation completeness), team-scoped."""
    team_clause = "" if team_ids is None else " WHERE team_id = ANY(:tids)"
    d_team = "" if team_ids is None else " WHERE d.team_id = ANY(:tids)"
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

        # §18 — the catalog signals, one row per dataset, tallied in Python.
        sig_rows = (await s.execute(
            text(f"SELECT sig.validation_status AS vs, sig.has_schema_drift AS drift, "
                 f"sig.documentation AS doc FROM datasets d {signals_lateral()}{d_team}"),
            {"tids": team_ids},
        )).all()
        val: dict = {}
        drift: dict = {}
        doc: dict = {}
        for r in sig_rows:
            val[r.vs] = val.get(r.vs, 0) + 1
            dk = str(r.drift).lower()
            drift[dk] = drift.get(dk, 0) + 1
            doc[r.doc] = doc.get(r.doc, 0) + 1
        out["validation_status"] = val
        out["has_schema_drift"] = drift
        out["documentation"] = doc
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

_SM_COLS = """id::text, dataset_id::text, sheet_key, logical_sheet_id::text,
              grain, primary_key_columns,
              description, updated_by::text, updated_at::text AS updated_at"""


async def upsert_sheet_metadata(
    dataset_id: str, sheet_key: str, fields: dict, updated_by: str,
) -> dict:
    """Whole-record write (PUT). Every column is assigned from the payload, so
    a field the caller left out is stored as NULL — that is replace semantics
    and it is deliberate: it is the only way to clear a field back to NULL.
    ``update_sheet_metadata`` is the merging (PATCH) counterpart.

    ``logical_sheet_id`` is the one exception: it is not client-supplied, so it
    is COALESCEd rather than replaced (a re-link only ever fills it in).
    """
    async with async_session_factory() as s:
        row = (await s.execute(
            text(f"""
                INSERT INTO dataset_sheet_metadata
                    (dataset_id, sheet_key, logical_sheet_id,
                     grain, primary_key_columns, description, updated_by)
                VALUES (:did, :key,
                        (SELECT id FROM dataset_sheets
                         WHERE dataset_id = :did AND current_sheet_key = :key
                           AND retired_at IS NULL),
                        :grain, CAST(:pk AS jsonb), :description, :uid)
                ON CONFLICT (dataset_id, sheet_key) DO UPDATE
                    SET grain = EXCLUDED.grain,
                        primary_key_columns = EXCLUDED.primary_key_columns,
                        description = EXCLUDED.description,
                        updated_by = EXCLUDED.updated_by,
                        logical_sheet_id = COALESCE(EXCLUDED.logical_sheet_id,
                                                    dataset_sheet_metadata.logical_sheet_id),
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


async def get_sheet_metadata(dataset_id: str, sheet_key: str) -> dict | None:
    """The metadata row for one logical sheet, or None if none is recorded."""
    async with async_session_factory() as s:
        row = (await s.execute(
            text(f"SELECT {_SM_COLS} FROM dataset_sheet_metadata "
                 f"WHERE dataset_id = :did AND sheet_key = :key"),
            {"did": dataset_id, "key": sheet_key},
        )).mappings().first()
        return dict(row) if row else None


async def update_sheet_metadata(
    dataset_id: str, sheet_key: str, fields: dict, updated_by: str,
) -> dict | None:
    """Partial write (PATCH): assign ONLY the keys present in *fields*.

    *fields* must come from ``model_dump(exclude_unset=True)``. That is what
    separates the two cases the client can express:

    - key ABSENT from the body   -> absent from *fields* -> no SET clause is
      emitted for that column, so the stored value survives.
    - key sent as explicit null  -> present in *fields* with value ``None``
      -> ``col = NULL`` is emitted, so the field is cleared.

    This is why the UPDATE is built column by column instead of reusing the
    upsert's ``COALESCE(EXCLUDED.col, existing)`` idiom: once an explicit null
    has been flattened into a bind parameter, COALESCE can no longer tell it
    apart from "not supplied" and would silently ignore the clear.

    Returns None when the sheet has no metadata row — PATCH updates, it never
    creates (use ``upsert_sheet_metadata`` for that).
    """
    sets: list[str] = []
    params: dict = {"did": dataset_id, "key": sheet_key, "uid": updated_by}
    if "grain" in fields:
        sets.append("grain = :grain")
        params["grain"] = fields["grain"]
    if "primary_key_columns" in fields:
        pk = fields["primary_key_columns"]
        sets.append("primary_key_columns = CAST(:pk AS jsonb)")
        params["pk"] = json.dumps(pk) if pk is not None else None
    if "description" in fields:
        sets.append("description = :description")
        params["description"] = fields["description"]
    if not sets:  # nothing to change — a no-op PATCH is not an error
        return await get_sheet_metadata(dataset_id, sheet_key)
    async with async_session_factory() as s:
        row = (await s.execute(
            text(f"""
                UPDATE dataset_sheet_metadata SET {', '.join(sets)},
                    updated_by = :uid,
                    updated_at = now(),
                    logical_sheet_id = COALESCE(
                        logical_sheet_id,
                        (SELECT id FROM dataset_sheets
                          WHERE dataset_id = :did AND current_sheet_key = :key
                            AND retired_at IS NULL))
                WHERE dataset_id = :did AND sheet_key = :key
                RETURNING {_SM_COLS}
            """),
            params,
        )).mappings().first()
        await s.commit()
        return dict(row) if row else None


async def list_sheet_metadata(dataset_id: str) -> list[dict]:
    """EVERY sheet-metadata row for a dataset — the in-process read.

    Deliberately unbounded: callers inside the service (row-diff key lookup)
    need the whole set, and truncating it would silently change their answer.
    HTTP list responses use ``page_sheet_metadata`` instead.
    """
    async with async_session_factory() as s:
        rows = (await s.execute(
            text(f"SELECT {_SM_COLS} FROM dataset_sheet_metadata "
                 f"WHERE dataset_id = :did ORDER BY sheet_key"),
            {"did": dataset_id},
        )).mappings().all()
        return [dict(r) for r in rows]


async def page_sheet_metadata(
    dataset_id: str, *, limit: int, offset: int,
) -> tuple[list[dict], int]:
    """One page of sheet-metadata rows plus the FULL count.

    ``total`` counts every row, not the page, so the ``Page`` envelope can tell
    a client there is more to fetch.
    """
    async with async_session_factory() as s:
        total = (await s.execute(
            text("SELECT COUNT(*) FROM dataset_sheet_metadata WHERE dataset_id = :did"),
            {"did": dataset_id})).scalar_one()
        rows = (await s.execute(
            text(f"SELECT {_SM_COLS} FROM dataset_sheet_metadata "
                 f"WHERE dataset_id = :did ORDER BY sheet_key "
                 f"LIMIT :limit OFFSET :offset"),
            {"did": dataset_id, "limit": limit, "offset": offset},
        )).mappings().all()
        return [dict(r) for r in rows], total


# ---------------------------------------------------------------------------
# Health signals (§17)
# ---------------------------------------------------------------------------

async def recent_version_sheets(dataset_id: str, limit_versions: int = 10) -> list[dict]:
    """Sheet rows of the most recent ready versions, version-ascending —
    the schema-stability input (fingerprint churn per logical sheet)."""
    async with async_session_factory() as s:
        rows = (await s.execute(
            text("""
                SELECT v.version_number, s.logical_sheet_id::text AS logical_sheet_id,
                       s.sheet_name, s.schema_fingerprint
                FROM (SELECT id, version_number FROM dataset_versions
                      WHERE dataset_id = :did AND status = 'ready'
                      ORDER BY version_number DESC LIMIT :lim) v
                JOIN dataset_version_sheets s ON s.dataset_version_id = v.id
                ORDER BY v.version_number ASC, s.sheet_index ASC
            """),
            {"did": dataset_id, "lim": limit_versions},
        )).mappings().all()
        return [dict(r) for r in rows]


async def latest_profiled_pairs(dataset_id: str, algorithm_version: int = 1) -> list[dict]:
    """Per logical sheet: the completed profile runs of its last TWO profiled
    versions (rank 1 = latest). Feeds missing/duplicates (rank 1) and drift
    (rank 1 vs rank 2)."""
    async with async_session_factory() as s:
        rows = (await s.execute(
            text("""
                SELECT run_id, logical_sheet_id, sheet_name, profile, version_number
                FROM (
                    SELECT p.id::text AS run_id,
                           p.logical_sheet_id::text AS logical_sheet_id,
                           ls.display_name AS sheet_name,
                           p.profile, v.version_number,
                           ROW_NUMBER() OVER (PARTITION BY p.logical_sheet_id
                                              ORDER BY v.version_number DESC) AS rn
                    FROM profile_runs p
                    JOIN dataset_versions v ON v.id = p.dataset_version_id
                    LEFT JOIN dataset_sheets ls ON ls.id = p.logical_sheet_id
                    WHERE p.dataset_id = :did AND p.status = 'completed'
                      AND p.algorithm_version = :alg
                ) t
                WHERE rn <= 2
                ORDER BY logical_sheet_id, version_number DESC
            """),
            {"did": dataset_id, "alg": algorithm_version},
        )).mappings().all()
        return [dict(r) for r in rows]


async def documentation_stats(dataset_id: str, current_version_id: str | None) -> dict:
    """Sheet + column documentation coverage over LIVE logical sheets; column
    totals come from the current version's captured schemas."""
    async with async_session_factory() as s:
        row = (await s.execute(
            text("""
                SELECT
                  (SELECT COUNT(*) FROM dataset_sheets ls
                    WHERE ls.dataset_id = :did AND ls.retired_at IS NULL)
                      AS total_sheets,
                  (SELECT COUNT(*) FROM dataset_sheet_metadata m
                    JOIN dataset_sheets ls ON ls.id = m.logical_sheet_id
                     AND ls.retired_at IS NULL
                    WHERE m.dataset_id = :did
                      AND (m.grain IS NOT NULL OR m.primary_key_columns IS NOT NULL
                           OR m.description IS NOT NULL))
                      AS documented_sheets,
                  (SELECT COUNT(*) FROM dataset_column_metadata c
                    JOIN dataset_sheets ls ON ls.id = c.logical_sheet_id
                     AND ls.retired_at IS NULL
                    WHERE c.dataset_id = :did
                      AND (c.business_name IS NOT NULL OR c.description IS NOT NULL
                           OR c.semantic_type IS NOT NULL OR c.unit IS NOT NULL
                           OR c.sensitivity IS NOT NULL
                           OR c.allowed_values IS NOT NULL))
                      AS documented_columns,
                  (SELECT COALESCE(SUM(jsonb_array_length(
                            COALESCE(s2.schema_json, '[]'::jsonb))), 0)
                     FROM dataset_version_sheets s2
                    WHERE s2.dataset_version_id = CAST(:vid AS uuid))
                      AS total_columns
            """),
            {"did": dataset_id, "vid": current_version_id},
        )).mappings().one()
        return dict(row)


# ---------------------------------------------------------------------------
# Column-level data dictionary (§15)
# ---------------------------------------------------------------------------

_CM_COLS = """id::text, dataset_id::text, logical_sheet_id::text, column_name,
              business_name, description, semantic_type, unit, sensitivity,
              allowed_values, updated_by::text, updated_at::text AS updated_at"""


async def get_live_logical_sheet(dataset_id: str, sheet_key: str) -> dict | None:
    """The live logical-sheet row for a normalized sheet_key, or None."""
    async with async_session_factory() as s:
        row = (await s.execute(
            text("""
                SELECT id::text, current_sheet_key, display_name
                FROM dataset_sheets
                WHERE dataset_id = :did AND current_sheet_key = :key
                  AND retired_at IS NULL
            """),
            {"did": dataset_id, "key": sheet_key},
        )).mappings().first()
        return dict(row) if row else None


async def upsert_column_metadata(
    dataset_id: str, logical_sheet_id: str, column_name: str,
    fields: dict, updated_by: str,
) -> dict:
    """Whole-record write (PUT) — see ``upsert_sheet_metadata``: every column
    is assigned from the payload, so an omitted field is stored as NULL. That
    is replace semantics, and the only way to clear a field.
    ``update_column_metadata`` is the merging (PATCH) counterpart.
    """
    async with async_session_factory() as s:
        row = (await s.execute(
            text(f"""
                INSERT INTO dataset_column_metadata
                    (dataset_id, logical_sheet_id, column_name, business_name,
                     description, semantic_type, unit, sensitivity,
                     allowed_values, updated_by)
                VALUES (:did, :lsid, :col, :business_name, :description,
                        :semantic_type, :unit, :sensitivity,
                        CAST(:allowed AS jsonb), :uid)
                ON CONFLICT (logical_sheet_id, column_name) DO UPDATE
                    SET business_name = EXCLUDED.business_name,
                        description = EXCLUDED.description,
                        semantic_type = EXCLUDED.semantic_type,
                        unit = EXCLUDED.unit,
                        sensitivity = EXCLUDED.sensitivity,
                        allowed_values = EXCLUDED.allowed_values,
                        updated_by = EXCLUDED.updated_by,
                        updated_at = now()
                RETURNING {_CM_COLS}
            """),
            {"did": dataset_id, "lsid": logical_sheet_id, "col": column_name,
             "business_name": fields.get("business_name"),
             "description": fields.get("description"),
             "semantic_type": fields.get("semantic_type"),
             "unit": fields.get("unit"),
             "sensitivity": fields.get("sensitivity"),
             "allowed": json.dumps(fields.get("allowed_values"))
                        if fields.get("allowed_values") is not None else None,
             "uid": updated_by},
        )).mappings().one()
        await s.commit()
        return dict(row)


async def get_column_metadata(logical_sheet_id: str, column_name: str) -> dict | None:
    """One column's dictionary entry, or None if none is recorded."""
    async with async_session_factory() as s:
        row = (await s.execute(
            text(f"SELECT {_CM_COLS} FROM dataset_column_metadata "
                 f"WHERE logical_sheet_id = :lsid AND column_name = :col"),
            {"lsid": logical_sheet_id, "col": column_name},
        )).mappings().first()
        return dict(row) if row else None


async def update_column_metadata(
    logical_sheet_id: str, column_name: str, fields: dict, updated_by: str,
) -> dict | None:
    """Partial write (PATCH) for one dictionary entry — same contract as
    ``update_sheet_metadata``: only the keys present in *fields* (i.e. from
    ``model_dump(exclude_unset=True)``) produce a SET clause, so an omitted
    field keeps its stored value while an explicit ``null`` clears it.

    Returns None when the column has no dictionary entry yet.
    """
    sets: list[str] = []
    params: dict = {"lsid": logical_sheet_id, "col": column_name, "uid": updated_by}
    for name in ("business_name", "description", "semantic_type", "unit",
                 "sensitivity"):
        if name in fields:
            sets.append(f"{name} = :{name}")
            params[name] = fields[name]
    if "allowed_values" in fields:
        allowed = fields["allowed_values"]
        sets.append("allowed_values = CAST(:allowed AS jsonb)")
        params["allowed"] = json.dumps(allowed) if allowed is not None else None
    if not sets:  # nothing to change — a no-op PATCH is not an error
        return await get_column_metadata(logical_sheet_id, column_name)
    async with async_session_factory() as s:
        row = (await s.execute(
            text(f"""
                UPDATE dataset_column_metadata SET {', '.join(sets)},
                    updated_by = :uid,
                    updated_at = now()
                WHERE logical_sheet_id = :lsid AND column_name = :col
                RETURNING {_CM_COLS}
            """),
            params,
        )).mappings().first()
        await s.commit()
        return dict(row) if row else None


async def list_column_metadata(logical_sheet_id: str) -> list[dict]:
    """EVERY dictionary entry for a logical sheet — the in-process read.

    Deliberately unbounded: PII masking resolves the sensitivity of all columns
    from this, and a truncated set would silently unmask the tail. HTTP list
    responses use ``page_column_metadata`` instead.
    """
    async with async_session_factory() as s:
        rows = (await s.execute(
            text(f"SELECT {_CM_COLS} FROM dataset_column_metadata "
                 f"WHERE logical_sheet_id = :lsid ORDER BY column_name"),
            {"lsid": logical_sheet_id},
        )).mappings().all()
        return [dict(r) for r in rows]


async def page_column_metadata(
    logical_sheet_id: str, *, limit: int, offset: int,
) -> tuple[list[dict], int]:
    """One page of dictionary entries plus the FULL count (see
    ``page_sheet_metadata``)."""
    async with async_session_factory() as s:
        total = (await s.execute(
            text("SELECT COUNT(*) FROM dataset_column_metadata "
                 "WHERE logical_sheet_id = :lsid"),
            {"lsid": logical_sheet_id})).scalar_one()
        rows = (await s.execute(
            text(f"SELECT {_CM_COLS} FROM dataset_column_metadata "
                 f"WHERE logical_sheet_id = :lsid ORDER BY column_name "
                 f"LIMIT :limit OFFSET :offset"),
            {"lsid": logical_sheet_id, "limit": limit, "offset": offset},
        )).mappings().all()
        return [dict(r) for r in rows], total


async def delete_column_metadata(logical_sheet_id: str, column_name: str) -> bool:
    async with async_session_factory() as s:
        result = await s.execute(
            text("DELETE FROM dataset_column_metadata "
                 "WHERE logical_sheet_id = :lsid AND column_name = :col"),
            {"lsid": logical_sheet_id, "col": column_name},
        )
        await s.commit()
        return result.rowcount > 0


# ---------------------------------------------------------------------------
# Usage (derived from the audit trail)
# ---------------------------------------------------------------------------

async def dataset_usage(dataset_id: str) -> dict:
    """Download/write/read counts + last activity for SUCCESSFUL requests only.

    ``status_code < 400`` is the whole point: the audit middleware records the
    final response of every mutating or download request, including the ones
    that were denied (403), aimed at a dataset the caller cannot see (404) or
    malformed (422). Counting those makes a dataset nobody could actually touch
    look busy — a bot hammering a forbidden download would report as real usage.

    ``writes`` is the declared effect of the route (``request_effects``), NOT
    the HTTP method. The method counted the studio's own row read —
    ``POST .../sheets/{s}/query`` — as a write, so opening a dataset reported
    that it was being written to.

    ``reads`` is the rest of that same audited traffic: the read-shaped POSTs.
    They are counted rather than dropped for two reasons. They are the truest
    "is anyone using this?" signal the trail holds — downloads are rare, and
    GET reads are not audited at all — and without them ``total_events`` would
    exceed its own parts with nothing to explain the gap, which is how a
    counter starts lying again. The three partition ``total_events``: the only
    audited GETs under a dataset path are ``/download``.
    """
    async with async_session_factory() as s:
        row = (await s.execute(
            text("""
                SELECT
                    COUNT(*) FILTER (WHERE path LIKE '%/download')     AS downloads,
                    COUNT(*) FILTER (WHERE method <> 'GET'
                                       AND NOT (action = ANY(:reads))) AS writes,
                    COUNT(*) FILTER (WHERE method <> 'GET'
                                       AND action = ANY(:reads))       AS reads,
                    COUNT(*)                                           AS total_events,
                    MAX(occurred_at)::text                             AS last_activity_at
                FROM audit_log
                WHERE path LIKE '%/datasets/' || :did || '%'
                  AND status_code < 400
            """),
            {"did": dataset_id, "reads": request_effects.READ_ACTIONS},
        )).mappings().one()
        return dict(row)


# ---------------------------------------------------------------------------
# Timeline (§13) — read-only merge of existing history tables
# ---------------------------------------------------------------------------

_TIMELINE_EVENTS = """
    SELECT 'version_created' AS event_type, v.created_at AS occurred_at,
           NULL::text AS actor,
           jsonb_build_object('version_number', v.version_number,
                              'status', v.status,
                              'row_count', v.row_count) AS details
    FROM dataset_versions v WHERE v.dataset_id = :did
    UNION ALL
    SELECT 'tag_' || h.action, h.created_at, h.actor_email,
           jsonb_build_object('tag', h.tag_name,
                              'from_version', h.from_version_number,
                              'to_version', h.to_version_number,
                              'reason', h.reason,
                              'request_id', h.request_id)
    FROM dataset_tag_history h WHERE h.dataset_id = :did
    UNION ALL
    SELECT 'validation_run', r.started_at, u.email,
           jsonb_build_object('status', r.status,
                              'version_number', v.version_number,
                              'rules_total', r.rules_total,
                              'rules_failed', r.rules_failed,
                              'error_failures', r.error_failures)
    FROM validation_runs r
    LEFT JOIN users u ON u.id = r.triggered_by
    LEFT JOIN dataset_versions v ON v.id = r.dataset_version_id
    WHERE r.dataset_id = :did
    UNION ALL
    SELECT 'profile_run', p.started_at, u.email,
           jsonb_build_object('status', p.status,
                              'version_number', v.version_number,
                              'sheet', s.display_name)
    FROM profile_runs p
    LEFT JOIN users u ON u.id = p.created_by
    LEFT JOIN dataset_versions v ON v.id = p.dataset_version_id
    LEFT JOIN dataset_sheets s ON s.id = p.logical_sheet_id
    WHERE p.dataset_id = :did
    UNION ALL
    SELECT 'transformation_run', t.started_at, u.email,
           jsonb_build_object('status', t.status,
                              'mode', t.mode,
                              'transformation', d.name,
                              'version_number', v.version_number,
                              'sheet', s.display_name,
                              'row_count', t.result_summary -> 'row_count')
    FROM transformation_runs t
    JOIN transformation_definitions d ON d.id = t.definition_id
    LEFT JOIN users u ON u.id = t.triggered_by
    LEFT JOIN dataset_versions v ON v.id = t.dataset_version_id
    LEFT JOIN dataset_sheets s ON s.id = d.logical_sheet_id
    WHERE d.dataset_id = :did
    UNION ALL
    SELECT 'derived_from', l.created_at, NULL,
           jsonb_build_object('relation', l.relation,
                              'parent_dataset', l.parent_dataset_name,
                              'parent_version', l.parent_version_number,
                              'version_number', v.version_number)
    FROM dataset_lineage l
    LEFT JOIN dataset_versions v ON v.id = l.dataset_version_id
    WHERE l.dataset_id = :did
    UNION ALL
    SELECT 'published_to', l.created_at, NULL,
           jsonb_build_object('relation', l.relation,
                              'child_dataset_id', l.dataset_id::text,
                              'child_dataset', d.name)
    FROM dataset_lineage l
    LEFT JOIN datasets d ON d.id = l.dataset_id
    WHERE l.parent_dataset_id = :did AND l.dataset_id <> l.parent_dataset_id
    UNION ALL
    SELECT 'audit', a.occurred_at, a.actor_email,
           jsonb_build_object('method', a.method, 'path', a.path,
                              'status_code', a.status_code,
                              'request_id', a.request_id)
    FROM audit_log a
    WHERE a.method <> 'GET' AND NOT (a.action = ANY(:reads))
      AND a.path LIKE '%/datasets/' || :did || '%'
"""


async def dataset_timeline(dataset_id: str, *, limit: int, offset: int) -> tuple[list[dict], int]:
    """Merged history events, newest first. Audit events are writes only —
    reads are usage (see dataset_usage), not history.

    "Writes only" was enforced by ``method <> 'GET'``, which let every
    ``POST .../query`` into the feed as an ``audit`` event: a history entry
    saying something happened to a dataset that nothing happened to. It is the
    same misclassification the usage counters had, so both surfaces now read
    the one declared table and cannot drift apart.
    """
    params = {"did": dataset_id, "reads": request_effects.READ_ACTIONS}
    async with async_session_factory() as s:
        total = (await s.execute(
            text(f"SELECT COUNT(*) FROM ({_TIMELINE_EVENTS}) e"),
            params)).scalar_one()
        rows = (await s.execute(
            text(f"""
                SELECT event_type, occurred_at::text AS occurred_at, actor, details
                FROM ({_TIMELINE_EVENTS}) e
                ORDER BY occurred_at DESC
                LIMIT :limit OFFSET :offset
            """),
            {**params, "limit": limit, "offset": offset},
        )).mappings().all()
        return [dict(r) for r in rows], total


async def list_dataset_column_metadata(dataset_id: str) -> list[dict]:
    """Every column dictionary entry for a dataset, across all its sheets."""
    if not is_uuid(dataset_id):
        return []
    async with async_session_factory() as s:
        rows = (await s.execute(
            text(f"SELECT {_CM_COLS} FROM dataset_column_metadata "
                 f"WHERE dataset_id = :did"),
            {"did": dataset_id},
        )).mappings().all()
        return [dict(r) for r in rows]
