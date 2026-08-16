"""Library feature — saved analytics definitions, runs, artifacts, lineage."""

from __future__ import annotations

import json
from datetime import datetime, timezone

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


async def list_definitions(dataset_id: str, *, limit: int | None = None,
                           offset: int = 0) -> list[dict]:
    """Definitions on a dataset, name-ordered; unbounded when *limit* is None.

    Internal callers that need to scan every definition (the join builder looks
    for its own generated one) leave *limit* unset. The HTTP route pages, via
    ``list_definitions_page``.
    """
    clause = "" if limit is None else " LIMIT :limit OFFSET :offset"
    params: dict = {"did": dataset_id}
    if limit is not None:
        params.update({"limit": limit, "offset": offset})
    async with async_session_factory() as s:
        rows = (await s.execute(
            text(f"SELECT {_DEF_COLS} FROM analytics_definitions "
                 f"WHERE dataset_id = :did ORDER BY name{clause}"),
            params,
        )).mappings().all()
        return [dict(r) for r in rows]


async def list_definitions_page(dataset_id: str, *, limit: int,
                                offset: int) -> tuple[list[dict], int]:
    """One page of definitions plus the unpaged total."""
    async with async_session_factory() as s:
        total = (await s.execute(
            text("SELECT COUNT(*) FROM analytics_definitions WHERE dataset_id = :did"),
            {"did": dataset_id},
        )).scalar()
        rows = (await s.execute(
            text(f"SELECT {_DEF_COLS} FROM analytics_definitions "
                 f"WHERE dataset_id = :did ORDER BY name LIMIT :limit OFFSET :offset"),
            {"did": dataset_id, "limit": limit, "offset": offset},
        )).mappings().all()
        return [dict(r) for r in rows], int(total or 0)


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
    filename: str | None = None,
    format: str | None = None, media_type: str | None = None,
    size_bytes: int | None = None, checksum: str | None = None,
    created_by: str | None = None,
    dataset_id: str | None = None, team_id: str | None = None,
) -> dict:
    """Record ownership for a stored artifact.

    ``filename`` is the public identifier (`/samples/{filename}`); the key is
    partitioned by team/dataset/kind and is not derivable from it, so it is
    stored rather than rebuilt.

    The retention deadline is stamped here, once, from the policy in force at
    write time — a later policy edit does not retroactively shorten the life of
    anything already stored.
    """
    from app.features.files.services.retention import expires_at

    deadline = expires_at(artifact_type, now=datetime.now(timezone.utc))
    async with async_session_factory() as s:
        row = (await s.execute(
            text("""
                INSERT INTO artifacts (storage_key, filename, artifact_type,
                                       format, media_type, size_bytes, checksum,
                                       created_by, dataset_id, team_id, expires_at)
                VALUES (:sk, :fn, :at, :fmt, :mt, :sb, :cs, :uid, :did, :tid, :exp)
                RETURNING id::text, storage_key, filename, artifact_type, format,
                          media_type, size_bytes, checksum,
                          created_at::text AS created_at,
                          expires_at::text AS expires_at
            """),
            {"sk": storage_key, "fn": filename or storage_key.rsplit("/", 1)[-1],
             "at": artifact_type, "fmt": format, "mt": media_type,
             "sb": size_bytes, "cs": checksum, "uid": created_by,
             "did": dataset_id, "tid": team_id, "exp": deadline},
        )).mappings().one()
        await s.commit()
        return dict(row)


async def get_artifact_by_filename(filename: str) -> dict | None:
    """Resolve the public filename to its ownership row (newest wins)."""
    async with async_session_factory() as s:
        row = (await s.execute(
            text("""
                SELECT id::text, storage_key, filename, artifact_type, format,
                       media_type, size_bytes, checksum,
                       dataset_id::text, team_id::text, created_by::text,
                       created_at::text AS created_at
                FROM artifacts WHERE filename = :fn
                ORDER BY created_at DESC LIMIT 1
            """),
            {"fn": filename})).mappings().first()
        return dict(row) if row else None


async def list_visible_artifacts(team_ids: list[str] | None, *, limit: int,
                                 offset: int) -> tuple[list[dict], int]:
    """Artifacts a caller can see, from Postgres rather than a bucket scan.

    The old listing enumerated every key under one flat prefix and filtered in
    Python — O(all artifacts globally) on every request. Ownership already
    lives here, so the database is both the correct and the cheap source.
    ``team_ids`` of None means unrestricted (superuser) — including the
    ownerless rows, which are superuser-only by design.
    """
    where = "TRUE" if team_ids is None else "team_id = ANY(:tids)"
    params: dict = {} if team_ids is None else {"tids": team_ids}
    async with async_session_factory() as s:
        total = (await s.execute(
            text(f"SELECT COUNT(*) FROM artifacts WHERE {where}"), params)).scalar()
        rows = (await s.execute(
            text(f"""
                SELECT id::text, storage_key, filename, artifact_type, format,
                       size_bytes, dataset_id::text, team_id::text,
                       created_at::text AS created_at
                FROM artifacts WHERE {where}
                ORDER BY created_at DESC LIMIT :limit OFFSET :offset
            """),
            {**params, "limit": limit, "offset": offset})).mappings().all()
        return [dict(r) for r in rows], total


async def get_artifact_by_key(storage_key: str) -> dict | None:
    """Ownership row for a stored artifact (download authorization)."""
    async with async_session_factory() as s:
        row = (await s.execute(
            text("""
                SELECT id::text, storage_key, artifact_type,
                       dataset_id::text, team_id::text, created_by::text
                FROM artifacts WHERE storage_key = :sk
                ORDER BY created_at DESC LIMIT 1
            """),
            {"sk": storage_key},
        )).mappings().first()
        return dict(row) if row else None


async def list_dataset_artifact_keys(dataset_id: str) -> list[str]:
    """Storage keys of every artifact owned by a dataset (for delete cleanup)."""
    if not is_uuid(dataset_id):
        return []
    async with async_session_factory() as s:
        rows = (await s.execute(
            text("SELECT storage_key FROM artifacts WHERE dataset_id = :did"),
            {"did": dataset_id},
        )).scalars().all()
        return list(rows)


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
    """Close a run as failed — but only if it is still open.

    The ``status = 'running'`` guard makes this "fail it if it has not already
    ended" rather than "fail it". ``execute_definition`` calls ``complete_job``
    after ``complete_run``; without the guard, a failure in that gap demoted a
    run that had genuinely succeeded to ``failed``.

    Same shape and reason as ``transform/repo.py::fail_run`` and the guarded
    close in ``scripts/audit_stranded_analytics_runs.py``.

    ``execute_join``'s recovery path is unaffected: it gates on its own
    ``run_closed`` flag and so never reaches here after a successful
    ``complete_run`` — see ``tests/unit/test_join_run_bookkeeping.py``.
    """
    async with async_session_factory() as s:
        await s.execute(
            text("UPDATE analytics_runs SET status = 'failed', error = :e, "
                 "completed_at = now() WHERE id = :id AND status = 'running'"),
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


async def dataset_name_taken(team_id: str, name: str) -> bool:
    """Whether the team already has a dataset with this exact name."""
    async with async_session_factory() as s:
        row = (await s.execute(
            text("SELECT 1 FROM datasets WHERE team_id = :tid AND name = :name LIMIT 1"),
            {"tid": team_id, "name": name},
        )).first()
        return row is not None


#: Fields of a lineage parent that identify the other dataset. Redacted
#: wholesale when the caller cannot see that dataset's team, so a lineage row
#: cannot become the side channel that reveals what
#: ``ensure_dataset_permission``'s 404 hides.
_PARENT_IDENTITY = ("parent_dataset_id", "parent_dataset_name", "parent_version_id",
                    "parent_version_number", "parent_sheet_key")
_CHILD_IDENTITY = ("child_dataset_id", "child_dataset_name", "child_version_id",
                   "child_version_number")


def _redact(row: dict, fields: tuple[str, ...], flag: str) -> dict:
    """Blank *fields* on a lineage row and say so, rather than dropping the row.

    Dropping it would understate the derivation — a join would look like it had
    one parent. Keeping it with the identity blanked says "this came from
    somewhere you cannot see", which is true and is not a leak.
    """
    out = {**row, flag: False}
    for field in fields:
        if field in out:
            out[field] = None
    return out


def _visible(team_id, team_ids: list[str] | None) -> bool:
    """Whether a dataset in *team_id* is visible. ``None`` team_ids = superuser."""
    return team_ids is None or (team_id is not None and str(team_id) in team_ids)


async def get_lineage(dataset_id: str, *, team_ids: list[str] | None = None) -> dict:
    """Parents of this dataset's versions + children derived from it.

    *team_ids* is the set of teams the caller belongs to; ``None`` means no
    filtering (superuser). Lineage crosses team boundaries — a join may name a
    dataset in another team — and these rows carry the other dataset's id and
    its denormalized name. Returning those to a caller who gets a 404 from
    ``GET /datasets/{that_id}`` would leak exactly the existence the 404 hides,
    so the identifying fields are blanked and ``parent_visible`` /
    ``child_visible`` says why.
    """
    async with async_session_factory() as s:
        parents = (await s.execute(
            text("""
                SELECT l.id::text, l.dataset_version_id::text, dv.version_number,
                       l.parent_dataset_id::text, l.parent_version_id::text,
                       l.parent_dataset_name, l.parent_version_number,
                       l.parent_sheet_key, l.relation, l.created_at::text AS created_at,
                       pd.team_id::text AS parent_team_id
                FROM dataset_lineage l
                JOIN dataset_versions dv ON dv.id = l.dataset_version_id
                LEFT JOIN datasets pd ON pd.id = l.parent_dataset_id
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
                       l.created_at::text AS created_at,
                       d.team_id::text AS child_team_id
                FROM dataset_lineage l
                JOIN datasets d ON d.id = l.dataset_id
                JOIN dataset_versions dv ON dv.id = l.dataset_version_id
                WHERE l.parent_dataset_id = :did
                ORDER BY l.created_at DESC
            """),
            {"did": dataset_id},
        )).mappings().all()

    def _parent(row):
        out = dict(row)
        team_id = out.pop("parent_team_id", None)
        if _visible(team_id, team_ids):
            return {**out, "parent_visible": True}
        return _redact(out, _PARENT_IDENTITY, "parent_visible")

    def _child(row):
        out = dict(row)
        team_id = out.pop("child_team_id", None)
        if _visible(team_id, team_ids):
            return {**out, "child_visible": True}
        return _redact(out, _CHILD_IDENTITY, "child_visible")

    return {"parents": [_parent(r) for r in parents],
            "children": [_child(r) for r in children]}


# ---------------------------------------------------------------------------
# Chart definitions (§14)
# ---------------------------------------------------------------------------

_CHART_COLS = """id::text, dataset_id::text, definition_id::text, view_id::text,
                 name, description, chart_type, config, created_by::text,
                 created_at::text AS created_at, updated_at::text AS updated_at"""


async def create_chart(dataset_id: str, fields: dict, created_by: str) -> dict | None:
    """Insert a chart; None on a (dataset, name) collision."""
    async with async_session_factory() as s:
        row = (await s.execute(
            text(f"""
                INSERT INTO chart_definitions
                    (dataset_id, definition_id, view_id, name, description,
                     chart_type, config, created_by)
                VALUES (:did, :def_id, :view_id, :name, :description,
                        :chart_type, CAST(:config AS jsonb), :uid)
                ON CONFLICT (dataset_id, name) DO NOTHING
                RETURNING {_CHART_COLS}
            """),
            {"did": dataset_id, "def_id": fields.get("definition_id"),
             "view_id": fields.get("view_id"), "name": fields["name"],
             "description": fields.get("description"),
             "chart_type": fields["chart_type"],
             "config": json.dumps(fields.get("config") or {}),
             "uid": created_by},
        )).mappings().first()
        await s.commit()
        return dict(row) if row else None


async def list_charts(dataset_id: str, *, limit: int | None = None,
                      offset: int = 0) -> list[dict]:
    """Charts on a dataset, name-ordered; unbounded when *limit* is None."""
    clause = "" if limit is None else " LIMIT :limit OFFSET :offset"
    params: dict = {"did": dataset_id}
    if limit is not None:
        params.update({"limit": limit, "offset": offset})
    async with async_session_factory() as s:
        rows = (await s.execute(
            text(f"SELECT {_CHART_COLS} FROM chart_definitions "
                 f"WHERE dataset_id = :did ORDER BY name{clause}"),
            params,
        )).mappings().all()
        return [dict(r) for r in rows]


async def list_charts_page(dataset_id: str, *, limit: int,
                           offset: int) -> tuple[list[dict], int]:
    """One page of charts plus the unpaged total."""
    async with async_session_factory() as s:
        total = (await s.execute(
            text("SELECT COUNT(*) FROM chart_definitions WHERE dataset_id = :did"),
            {"did": dataset_id},
        )).scalar()
        rows = (await s.execute(
            text(f"SELECT {_CHART_COLS} FROM chart_definitions "
                 f"WHERE dataset_id = :did ORDER BY name LIMIT :limit OFFSET :offset"),
            {"did": dataset_id, "limit": limit, "offset": offset},
        )).mappings().all()
        return [dict(r) for r in rows], int(total or 0)


async def get_chart(dataset_id: str, chart_id: str) -> dict | None:
    if not is_uuid(chart_id):
        return None
    async with async_session_factory() as s:
        row = (await s.execute(
            text(f"SELECT {_CHART_COLS} FROM chart_definitions "
                 f"WHERE id = :cid AND dataset_id = :did"),
            {"cid": chart_id, "did": dataset_id},
        )).mappings().first()
        return dict(row) if row else None


async def update_chart(dataset_id: str, chart_id: str, fields: dict) -> dict | None:
    if not is_uuid(chart_id):
        return None
    sets = ["updated_at = now()"]
    params: dict = {"cid": chart_id, "did": dataset_id}
    for col in ("name", "description", "chart_type", "definition_id", "view_id"):
        if col in fields:
            sets.append(f"{col} = :{col}")
            params[col] = fields[col]
    if "config" in fields:
        sets.append("config = CAST(:config AS jsonb)")
        params["config"] = json.dumps(fields["config"] or {})
    async with async_session_factory() as s:
        row = (await s.execute(
            text(f"""
                UPDATE chart_definitions SET {', '.join(sets)}
                WHERE id = :cid AND dataset_id = :did
                RETURNING {_CHART_COLS}
            """),
            params,
        )).mappings().first()
        await s.commit()
        return dict(row) if row else None


async def delete_chart(dataset_id: str, chart_id: str) -> bool:
    if not is_uuid(chart_id):
        return False
    async with async_session_factory() as s:
        result = await s.execute(
            text("DELETE FROM chart_definitions WHERE id = :cid AND dataset_id = :did"),
            {"cid": chart_id, "did": dataset_id})
        await s.commit()
        return result.rowcount > 0


async def lineage_graph(dataset_id: str, max_depth: int = 10, *,
                        team_ids: list[str] | None = None) -> dict:
    """The full derivation DAG around a dataset, both directions.

    ``get_lineage`` answers one hop — the immediate parents and children. This
    walks the whole chain, which is what turns lineage from a list into a
    story: "this dataset is a join of a transformation of an upload".

    A recursive CTE does the walk. Two guards keep it terminating on data that
    is not guaranteed acyclic: a depth cap, and a visited-path check so a cycle
    (A published from B, B later republished from A) stops instead of looping.

    The walk goes one hop PAST *max_depth* and that probe hop is what sets
    ``truncated``. Reading ``depth >= max_depth`` off the returned edges instead
    said "the DAG continues" for every graph whose true depth happened to equal
    the cap — the edge at the cap is returned, not cut, so its presence proves
    nothing. Clients are told to raise the depth on ``truncated``, and raising
    it returned an identical graph.

    *team_ids* scopes which datasets may be NAMED (``None`` = superuser). Nodes
    in other teams are withheld along with the edges touching them, and
    ``hidden_nodes`` counts them, because the direct routes answer 404 for
    those datasets and lineage must not undo that.
    """
    if not is_uuid(dataset_id):
        return {"nodes": [], "edges": [], "truncated": False, "hidden_nodes": 0}

    query = text("""
        WITH RECURSIVE
        up AS (
            SELECT l.dataset_id, l.parent_dataset_id, l.relation, 1 AS depth,
                   ARRAY[l.dataset_id] AS path
            FROM dataset_lineage l
            WHERE l.dataset_id = :did AND l.parent_dataset_id <> l.dataset_id
            UNION ALL
            SELECT l.dataset_id, l.parent_dataset_id, l.relation, up.depth + 1,
                   up.path || l.dataset_id
            FROM dataset_lineage l
            JOIN up ON l.dataset_id = up.parent_dataset_id
            WHERE up.depth < :max_depth
              AND l.parent_dataset_id <> l.dataset_id
              AND NOT (l.dataset_id = ANY(up.path))
        ),
        down AS (
            SELECT l.dataset_id, l.parent_dataset_id, l.relation, 1 AS depth,
                   ARRAY[l.parent_dataset_id] AS path
            FROM dataset_lineage l
            WHERE l.parent_dataset_id = :did AND l.parent_dataset_id <> l.dataset_id
            UNION ALL
            SELECT l.dataset_id, l.parent_dataset_id, l.relation, down.depth + 1,
                   down.path || l.parent_dataset_id
            FROM dataset_lineage l
            JOIN down ON l.parent_dataset_id = down.dataset_id
            WHERE down.depth < :max_depth
              AND l.parent_dataset_id <> l.dataset_id
              AND NOT (l.parent_dataset_id = ANY(down.path))
        )
        SELECT DISTINCT dataset_id::text AS child_id,
                        parent_dataset_id::text AS parent_id,
                        relation, MIN(depth) AS depth
        FROM (SELECT * FROM up UNION ALL SELECT * FROM down) e
        GROUP BY dataset_id, parent_dataset_id, relation
    """)
    async with async_session_factory() as s:
        walked = [dict(r) for r in (await s.execute(
            query, {"did": dataset_id, "max_depth": max_depth + 1})).mappings().all()]

        # The extra hop is a probe, not a result: it proves the DAG continues
        # and is then discarded, so its nodes must not leak into the response.
        edges = [e for e in walked if e["depth"] <= max_depth]
        truncated = any(e["depth"] > max_depth for e in walked)

        ids = {dataset_id} | {e["child_id"] for e in edges} | {e["parent_id"]
                                                               for e in edges}
        rows = [dict(r) for r in (await s.execute(
            text("""SELECT id::text, name, domain, deprecated, team_id::text,
                           created_at::text AS created_at
                    FROM datasets WHERE id = ANY(:ids)"""),
            {"ids": sorted(ids)})).mappings().all()]

    nodes, hidden = [], set()
    for row in rows:
        team_id = row.pop("team_id", None)
        if _visible(team_id, team_ids):
            nodes.append({**row, "is_root": row["id"] == dataset_id})
        else:
            hidden.add(row["id"])
    # An edge whose endpoint was withheld would still carry that dataset's
    # UUID, which is the existence the filter is meant to hide.
    visible_edges = [e for e in edges
                     if e["child_id"] not in hidden and e["parent_id"] not in hidden]
    return {
        "nodes": nodes, "edges": visible_edges,
        "truncated": truncated, "hidden_nodes": len(hidden),
    }


# ---------------------------------------------------------------------------
# Retention
# ---------------------------------------------------------------------------


async def list_expired_artifacts(*, limit: int) -> list[dict]:
    """Artifacts past their deadline, oldest first."""
    async with async_session_factory() as s:
        rows = (await s.execute(
            text("""
                SELECT id::text, storage_key, artifact_type, size_bytes
                FROM artifacts
                WHERE expires_at IS NOT NULL AND expires_at <= now()
                ORDER BY expires_at
                LIMIT :limit
            """),
            {"limit": limit})).mappings().all()
        return [dict(r) for r in rows]


async def delete_artifacts(ids: list[str]) -> int:
    """Drop artifact rows by id. Returns the number removed."""
    ids = [i for i in ids if is_uuid(i)]
    if not ids:
        return 0
    async with async_session_factory() as s:
        result = await s.execute(
            text("DELETE FROM artifacts WHERE id = ANY(:ids)"),
            {"ids": ids})
        await s.commit()
        return result.rowcount or 0


async def known_artifact_keys(storage_keys: list[str]) -> set[str]:
    """Which of *storage_keys* have an owning row (orphan detection)."""
    if not storage_keys:
        return set()
    async with async_session_factory() as s:
        rows = (await s.execute(
            text("SELECT storage_key FROM artifacts WHERE storage_key = ANY(:sks)"),
            {"sks": storage_keys})).scalars().all()
        return set(rows)
