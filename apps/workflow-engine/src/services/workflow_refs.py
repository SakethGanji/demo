"""The ``workflow_refs`` index: which workflows call which (DESIGN-STAGE8-REUSE section 5.1, 10.3).

Pure parsing (``refs_of``) plus the writes that keep the table in step with saves and publishes. Callers own the
transaction: nothing here commits, except ``backfill_if_empty`` which owns its own sessions.
"""

from __future__ import annotations

import logging
from typing import Any, Callable

from sqlalchemy import text
from sqlalchemy.exc import ProgrammingError

from ..core.exceptions import ConflictError
from ..db.models import WorkflowModel
from ..repositories.workflow_ref_repository import Ref, WorkflowRefRepository
from .workflow_io import agent_tool_calls

logger = logging.getLogger(__name__)

#: node types that call another workflow: the block, and the AI agent's tool (section 3.2, G9)
CALLER_TYPES = {"ExecuteWorkflow": "draft", "WorkflowTool": "latest"}  # type -> spec when the node pins nothing
BACKFILL_LOCK_ID = 839202  # advisory lock: one pod backfills, the others skip
BACKFILL_BATCH = 200


class WorkflowInUseError(ConflictError):
    """409 ``WORKFLOW_IN_USE``: a published parent or a live release still calls the workflow (section 10.3).

    ``used_by`` (also in ``details``) is the list of ``used_by`` rows that block the delete."""

    def __init__(self, used_by: list[dict[str, Any]]) -> None:
        names = sorted({r.get("parent_name") or r["parent_workflow_id"] for r in used_by})
        super().__init__("WORKFLOW_IN_USE", f"This workflow is called by {', '.join(names)}. Remove those calls first.")
        self.used_by = used_by
        self.details["used_by"] = used_by


def _spec(raw: Any, default: str) -> str:
    """'draft' | 'latest' | '<n>' from a node's ``version`` parameter (a legacy node has none)."""
    if raw is None or raw == "":
        return default
    if isinstance(raw, bool):
        return default
    if isinstance(raw, (int, float)) and float(raw).is_integer() and raw >= 1:
        return str(int(raw))
    if isinstance(raw, str):
        v = raw.strip().lower()
        if v in ("draft", "latest"):
            return v
        if v.isdigit() and int(v) >= 1:
            return str(int(v))
    return default


def is_expression(value: Any) -> bool:
    return isinstance(value, str) and (value.lstrip().startswith("=") or "{{" in value)


def refs_of(definition: dict[str, Any] | None) -> list[Ref]:
    """``(node_name, child_workflow_id, version_spec)`` per sub-workflow call in a definition, in node order.

    A call whose ``workflowId`` is empty or an expression has no static target and is not indexed (the checker reports it).
    """
    out: list[Ref] = []
    seen: set[str] = set()
    for n in (definition or {}).get("nodes") or []:
        if n.get("type") == "AIAgent" and n.get("name"):  # the agent's built-in Run Workflow tools call workflows too
            for _tool, tp in agent_tool_calls(n):
                wid = tp.get("workflowId")
                if isinstance(wid, dict):
                    wid = wid.get("value")
                if isinstance(wid, str) and wid.strip() and not is_expression(wid):
                    ref = (n["name"], wid.strip(), _spec(tp.get("version"), "latest"))
                    if ref not in out:
                        out.append(ref)
            continue
        default = CALLER_TYPES.get(n.get("type"))
        if default is None:
            continue
        params = n.get("parameters") or {}
        wid = params.get("workflowId")
        if isinstance(wid, dict):  # an n8n-style resource locator
            wid = wid.get("value")
        if not isinstance(wid, str) or not wid.strip() or is_expression(wid):
            continue
        name = n.get("name")
        if not name or name in seen:
            continue
        seen.add(name)
        out.append((name, wid.strip(), _spec(params.get("version"), default)))
    return out


async def _project_of(session: Any, workflow_id: str) -> str | None:
    return (await session.execute(text("SELECT project_id FROM workflows WHERE id = :w"), {"w": workflow_id})).scalar()


async def sync_refs(
    session: Any, workflow_id: str, version_id: int | None, definition: dict[str, Any] | None,
    project_id: str | None = None,
) -> int:
    """Replace the index rows of (workflow, version-or-draft) with the calls in ``definition``; returns the row count.

    Runs in a savepoint and never raises for a database that predates the migration: a save must not fail because the
    index is missing. Does not commit.
    """
    try:
        pid = project_id or await _project_of(session, workflow_id)
        if pid is None:
            return 0
        async with session.begin_nested():
            return await WorkflowRefRepository(session).replace(pid, workflow_id, version_id, refs_of(definition))
    except ProgrammingError as e:
        if "workflow_refs" not in str(e):
            raise
        logger.warning("workflow_refs table missing: apply migration 20261021120000_reuse")
        return 0


async def refresh_draft_index(session: Any, workflow_id: str) -> int:
    """Re-index a workflow's saved draft after code outside ``WorkflowService`` wrote it (AI Keep/Undo, installers).

    Reads the stored draft and replaces its draft rows; does not commit (the caller's transaction does)."""
    model = await session.get(WorkflowModel, workflow_id)
    if model is None:
        return 0
    return await sync_refs(session, workflow_id, None, model.draft_definition, project_id=model.project_id)


async def backfill_if_empty(session_factory: Callable[[], Any]) -> int:
    """Fill the index from every draft and every version when the table is empty (startup, idempotent, one pod wins).

    Returns the number of rows written (0 when the table already had rows, the lock was taken, or nothing calls anything).
    """
    written = 0
    async with session_factory() as session:
        try:
            if await WorkflowRefRepository(session).count() > 0:
                return 0
        except ProgrammingError:
            logger.warning("workflow_refs backfill skipped: migration 20261021120000_reuse is not applied")
            return 0
        locked = (await session.execute(text("SELECT pg_try_advisory_xact_lock(:k)"), {"k": BACKFILL_LOCK_ID})).scalar()
        if not locked:
            return 0
        repo = WorkflowRefRepository(session)
        like = "%ExecuteWorkflow%"
        like2 = "%WorkflowTool%"
        like3 = "%builtinTools%"  # an AI agent's Run Workflow tool
        rows = (await session.execute(
            text("""SELECT id, project_id, draft_definition FROM workflows
                    WHERE draft_definition::text LIKE :a OR draft_definition::text LIKE :b
                       OR draft_definition::text LIKE :c"""), {"a": like, "b": like2, "c": like3})).all()
        for wid, pid, definition in rows:
            written += await repo.replace(pid, wid, None, refs_of(definition))
        last = 0
        while True:
            batch = (await session.execute(
                text("""SELECT v.id, v.workflow_id, w.project_id, v.definition
                        FROM workflow_versions v JOIN workflows w ON w.id = v.workflow_id
                        WHERE v.id > :last AND (v.definition::text LIKE :a OR v.definition::text LIKE :b OR v.definition::text LIKE :c)
                        ORDER BY v.id LIMIT :n"""), {"last": last, "a": like, "b": like2, "c": like3, "n": BACKFILL_BATCH})).all()
            if not batch:
                break
            for vid, wid, pid, definition in batch:
                written += await repo.replace(pid, wid, vid, refs_of(definition))
            last = batch[-1][0]
        await session.commit()
    if written:
        logger.info("workflow_refs backfilled: %d rows", written)
    return written


async def used_by(session: Any, workflow_id: str) -> list[dict[str, Any]]:
    """Who calls ``workflow_id`` (section 11): one row per calling node with where it lives.

    ``where`` is ``published`` (the parent's live version), ``draft`` (the parent's working copy) or ``release`` (a version
    pinned by a non-archived release's snapshot). ``release_ids`` lists those releases.
    """
    repo = WorkflowRefRepository(session)
    out: dict[tuple[str, str], dict[str, Any]] = {}
    for r in await repo.callers_of(workflow_id):
        where = "draft" if r["parent_version_id"] is None else ("published" if r["parent_is_published"] else None)
        if where is None:
            continue
        key = (r["parent_workflow_id"], r["node"], where)
        out[key] = {"parent_workflow_id": r["parent_workflow_id"], "parent_name": r["parent_name"], "node": r["node"],
                    "version_spec": r["version_spec"], "where": where, "release_ids": []}
    for rel in await _releases_calling(session, workflow_id):
        key = (rel["parent_workflow_id"], rel["node"], "release")
        row = out.setdefault(key, {"parent_workflow_id": rel["parent_workflow_id"], "parent_name": rel["parent_name"],
                                   "node": rel["node"], "version_spec": rel["spec"], "where": "release",
                                   "release_ids": []})
        if rel["release_id"] not in row["release_ids"]:
            row["release_ids"].append(rel["release_id"])
    return sorted(out.values(), key=lambda r: (r["parent_workflow_id"], r["node"], r["where"]))


async def blocking_references(session: Any, workflow_id: str) -> list[dict[str, Any]]:
    """What stops ``workflow_id`` from being deleted (section 10.3): a parent's *published* version, or a non-archived
    release's snapshot, calls it. Draft-only references do not block. Self-calls are ignored."""
    return [r for r in await used_by(session, workflow_id)
            if r["where"] in ("published", "release") and r["parent_workflow_id"] != workflow_id]


async def _releases_calling(session: Any, workflow_id: str) -> list[dict[str, Any]]:
    """Release snapshots (any status but blocked/archived) whose ``subworkflows`` edges point at the workflow."""
    try:
        async with session.begin_nested():
            res = await session.execute(
                text("""SELECT r.id AS release_id, e->>'parent_workflow_id' AS parent_workflow_id,
                               e->>'node' AS node, e->>'spec' AS spec, w.name AS parent_name
                        FROM releases r
                        CROSS JOIN LATERAL jsonb_array_elements(COALESCE(r.snapshot->'subworkflows', '[]'::jsonb)) e
                        LEFT JOIN workflows w ON w.id = e->>'parent_workflow_id'
                        WHERE r.status NOT IN ('blocked', 'archived') AND e->>'child_workflow_id' = :c"""),
                {"c": workflow_id},
            )
            return [dict(r) | {"parent_name": r["parent_name"] or r["parent_workflow_id"]} for r in res.mappings().all()]
    except ProgrammingError:
        return []
