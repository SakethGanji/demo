"""NotebookService: notebooks and their append-only cells, plus the read model the studio renders.

- New cells go at the end of the notebook, or at the end of a for-each body (``parent_cell_id``).
- Editing a cell's ``spec`` or ``guard`` bumps its ``updated_at``; staleness is computed, never stored: a result is
  stale when the cell, or any cell above it, changed after the result's run started, or when a newer run that covered
  the cell stopped before reaching it.
- ``DELETE`` of a cell that has an applied write (a ``notebook_writes`` row) is refused with 409 ``cell_applied``.
- The lazy reaper (``reap``): a run still ``running`` whose heartbeat is older than ``STALE_HEARTBEAT_S`` is marked
  ``interrupted`` and the notebook lock is freed. It runs on every read of a notebook or run, so ``main.py`` needs no hook.
"""

from __future__ import annotations

import csv
import io
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from ...db.connection_models import ProjectConnectionModel
from ...db.models import ProjectModel
from ...db.notebook_models import (
    NotebookCellModel,
    NotebookCellResultModel,
    NotebookModel,
    NotebookRunModel,
    NotebookWriteModel,
)
from ...schemas.notebook import MAX_DATA_ROWS, REMOVED_KINDS, TOP_LEVEL_ONLY_KINDS, notebook_error, validate_spec
from ...utils.ids import generate_id
from . import effects as fx
from . import refs as R

STALE_HEARTBEAT_S = 30
RUNS_SHOWN = 20


# ── connections ───────────────────────────────────────────────────────────────────────────────────────────────────────


async def resolve_connection(session: AsyncSession, project_id: str, ref: str) -> ProjectConnectionModel | None:
    """A project connection by id or by key."""
    if not isinstance(ref, str) or not ref:
        return None
    c = await session.get(ProjectConnectionModel, ref)
    if c is not None and c.project_id == project_id:
        return c
    return (await session.execute(select(ProjectConnectionModel).where(
        ProjectConnectionModel.project_id == project_id, ProjectConnectionModel.key == ref))).scalars().first()


# ── serialization ─────────────────────────────────────────────────────────────────────────────────────────────────────


def result_out(r: NotebookCellResultModel, run_n: int | None = None) -> dict[str, Any]:
    return {"id": r.id, "run_id": r.run_id, "cell_id": r.cell_id, "row_index": r.row_index, "status": r.status,
            "output": r.output, "output_ref": r.output_ref, "error": r.error, "execution_id": r.execution_id,
            "job_id": r.job_id, "ms": r.ms, "created_at": r.created_at, "run_n": run_n}


def run_out(r: NotebookRunModel) -> dict[str, Any]:
    return {"id": r.id, "notebook_id": r.notebook_id, "n": r.n, "from_position": r.from_position, "status": r.status,
            "stop": r.stop, "on_applied": r.on_applied, "triggered_by": r.triggered_by, "run_as": r.run_as,
            "via_agent": r.via_agent, "heartbeat_at": r.heartbeat_at, "started_at": r.started_at,
            "finished_at": r.finished_at}


def notebook_base(nb: NotebookModel) -> dict[str, Any]:
    return {"id": nb.id, "project_id": nb.project_id, "team_id": nb.team_id, "title": nb.title,
            "description": nb.description, "status": nb.status, "environment": nb.environment,
            "template_slug": nb.template_slug, "run_as": nb.run_as, "created_by": nb.created_by,
            "active_run_id": nb.active_run_id, "kept": list(nb.kept or []), "created_at": nb.created_at,
            "updated_at": nb.updated_at}


MAX_WAIT_S = 7 * 86400
SPEC_ROWS_SHOWN = 50


def _spec_view(c: NotebookCellModel) -> dict[str, Any]:
    """The read model's spec: a data cell shows at most 50 of its rows (``rows_truncated`` + ``row_count``)."""
    spec = dict(c.spec or {})
    rows = spec.get("rows")
    if c.kind == "data" and isinstance(rows, list):
        spec["row_count"] = len(rows)
        spec["rows_truncated"] = len(rows) > SPEC_ROWS_SHOWN
        spec["rows"] = rows[:SPEC_ROWS_SHOWN]
    return spec


def _cell_base(c: NotebookCellModel) -> dict[str, Any]:
    return {"id": c.id, "notebook_id": c.notebook_id, "parent_cell_id": c.parent_cell_id, "position": c.position,
            "name": c.name, "kind": c.kind, "title": c.title, "spec": _spec_view(c), "guard": c.guard,
            "created_by": c.created_by, "created_via": c.created_via, "created_at": c.created_at,
            "updated_at": c.updated_at}


def parse_csv(text_: str) -> tuple[list[str], list[dict[str, Any]]]:
    rdr = csv.reader(io.StringIO(text_.strip()))
    rows = [r for r in rdr if r]
    if not rows:
        return [], []
    cols = [c.strip() for c in rows[0]]
    out = []
    for r in rows[1:]:
        d: dict[str, Any] = {}
        for i, c in enumerate(cols):
            v: Any = r[i].strip() if i < len(r) else None
            if isinstance(v, str):
                try:
                    v = int(v) if v.lstrip("-").isdigit() else float(v) if v.replace(".", "", 1).lstrip("-").isdigit() else v
                except ValueError:
                    pass
            d[c] = v
        out.append(d)
    return cols, out


def normalize_data_spec(spec: dict[str, Any]) -> dict[str, Any]:
    """Pasted CSV and list-of-list rows become ``{columns, rows: [dict]}``."""
    spec = dict(spec)
    if spec.get("csv"):
        cols, rows = parse_csv(spec["csv"])
        spec.update(columns=cols, rows=rows, csv=None)
    rows = spec.get("rows") or []
    if rows and isinstance(rows[0], list):
        cols = spec.get("columns") or [f"col{i + 1}" for i in range(len(rows[0]))]
        spec["rows"] = [{c: (r[i] if i < len(r) else None) for i, c in enumerate(cols)} for r in rows]
        spec["columns"] = cols
    if not spec.get("columns") and spec.get("rows"):
        from .storage import columns_of

        spec["columns"] = columns_of(spec["rows"])
    return spec


# ── the service ───────────────────────────────────────────────────────────────────────────────────────────────────────


class NotebookService:
    def __init__(self, session: AsyncSession) -> None:
        self.s = session

    # lookups
    async def require_project(self, ref: str) -> ProjectModel:
        from ..project_service import resolve_project

        p = await resolve_project(self.s, ref)
        if p is None:
            raise notebook_error(404, "project_not_found", f"Project '{ref}' not found")
        return p

    async def require_notebook(self, notebook_id: str) -> NotebookModel:
        nb = await self.s.get(NotebookModel, notebook_id)
        if nb is None:
            raise notebook_error(404, "notebook_not_found", "Notebook not found")
        return nb

    async def require_cell(self, cell_id: str) -> NotebookCellModel:
        c = await self.s.get(NotebookCellModel, cell_id)
        if c is None:
            raise notebook_error(404, "cell_not_found", "Cell not found")
        return c

    async def require_run(self, run_id: str) -> NotebookRunModel:
        r = await self.s.get(NotebookRunModel, run_id)
        if r is None:
            raise notebook_error(404, "run_not_found", "Run not found")
        return r

    async def cells(self, notebook_id: str) -> list[NotebookCellModel]:
        return list((await self.s.execute(select(NotebookCellModel).where(
            NotebookCellModel.notebook_id == notebook_id).order_by(NotebookCellModel.position))).scalars().all())

    async def written_cell_ids(self, cell_ids: list[str]) -> set[str]:
        if not cell_ids:
            return set()
        return set((await self.s.execute(select(NotebookWriteModel.cell_id).where(
            NotebookWriteModel.cell_id.in_(cell_ids)).distinct())).scalars().all())

    # reaper
    async def reap(self, nb: NotebookModel) -> None:
        """Free a dead lock, and keep ``notebook.status`` consistent with its runs: the status is derived from the
        run that holds (or last held) the lock, and a notebook is never 'running' without an active run."""
        from .runner import _RUN_STATUS_TO_NB

        if not nb.active_run_id:
            if nb.status == "running":  # a reader raced a finish: derive the status from the newest run
                last = (await self.s.execute(select(NotebookRunModel.status).where(
                    NotebookRunModel.notebook_id == nb.id).order_by(NotebookRunModel.n.desc()).limit(1))).scalar()
                st = _RUN_STATUS_TO_NB.get(last or "", "idle")
                await self.s.execute(update(NotebookModel).where(NotebookModel.id == nb.id,
                                                                 NotebookModel.active_run_id.is_(None),
                                                                 NotebookModel.status == "running").values(status=st))
                await self.s.commit()
                await self.s.refresh(nb)
            return
        run = await self.s.get(NotebookRunModel, nb.active_run_id)
        now = datetime.now()
        if run is not None and run.status in ("running", "waiting"):
            beat = run.heartbeat_at or run.started_at
            if beat and now - beat < timedelta(seconds=STALE_HEARTBEAT_S):
                return
            run.status, run.finished_at = "interrupted", now
            run.stop = {"reason": "cancelled", "cell_id": None, "interrupted": True}
        # a dead run, or a finished run that still holds the lock (a crash between the steps): free it, with the
        # notebook status taken from that run
        st = _RUN_STATUS_TO_NB.get(run.status, "idle") if run is not None else "idle"
        await self.s.execute(update(NotebookModel).where(NotebookModel.id == nb.id,
                                                         NotebookModel.active_run_id == nb.active_run_id)
                             .values(active_run_id=None, status=st))
        await self.s.commit()
        await self.s.refresh(nb)

    # notebooks
    async def list(self, project: ProjectModel, status: str | None, q: str | None) -> list[dict[str, Any]]:
        stmt = select(NotebookModel).where(NotebookModel.project_id == project.id)
        if status:
            stmt = stmt.where(NotebookModel.status == status)
        if q:
            if "\x00" in q:
                raise notebook_error(422, "invalid", "q must not contain NUL characters")
            esc = q.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
            stmt = stmt.where(NotebookModel.title.ilike(f"%{esc}%", escape="\\"))
        nbs = list((await self.s.execute(stmt.order_by(NotebookModel.updated_at.desc()))).scalars().all())
        out = []
        for nb in nbs:
            await self.reap(nb)
            cells = await self.cells(nb.id)
            effs = [await self._effect(c, [x for x in cells if x.parent_cell_id == c.id]) for c in cells
                    if c.parent_cell_id is None]
            last = (await self.s.execute(select(NotebookRunModel).where(NotebookRunModel.notebook_id == nb.id)
                                         .order_by(NotebookRunModel.n.desc()).limit(1))).scalars().first()
            top = [c for c in cells if c.parent_cell_id is None]
            needs_ai = sum(1 for c in top if c.kind == "ask")
            out.append({
                "id": nb.id, "title": nb.title, "status": nb.status, "cell_count": len(top),
                "no_ai_count": len(top) - needs_ai, "needs_ai_count": needs_ai, "needs_you": nb.status == "needs_you",
                "writes_env": (nb.environment or project.default_environment or "default") if "write" in effs else None,
                "last_run": {"n": last.n, "status": last.status, "at": last.started_at, "triggered_by": last.triggered_by,
                             "run_as": last.run_as, "via_agent": last.via_agent} if last else None,
                "kept": list(nb.kept or []), "check": await self._check_summary(nb),
                "updated_at": nb.updated_at, "created_by": nb.created_by,
            })
        return out

    async def _check_summary(self, nb: NotebookModel) -> dict[str, Any] | None:
        wids = [k.get("workflow_id") for k in nb.kept or [] if k.get("target") == "scheduled_check" and k.get("workflow_id")]
        if not wids:
            return None
        from ...db.models import ExecutionModel

        rows = (await self.s.execute(select(ExecutionModel.status).where(ExecutionModel.workflow_id == wids[-1])
                                     .order_by(ExecutionModel.start_time.desc()).limit(20))).scalars().all()
        rows = list(reversed(rows))
        return {"pass": sum(1 for r in rows if r == "success"), "fail": sum(1 for r in rows if r == "failed"),
                "last": rows[-10:]}

    async def create(self, project: ProjectModel, body: Any) -> NotebookModel:
        now = datetime.now()
        nb = NotebookModel(id=generate_id("nb"), project_id=project.id, team_id=project.team_id, title=body.title,
                           description=body.description, environment=body.environment, created_by=body.created_by,
                           template_slug=body.template or None, created_at=now, updated_at=now)
        cells: list[dict[str, Any]] = []
        if body.template and body.template != "blank":
            cells = await self._template_cells(project, body.template, body.connection)
        self.s.add(nb)
        await self.s.flush()
        for spec in cells:
            parent = await self.add_cell(nb, spec.get("kind"), spec.get("spec") or {}, title=spec.get("title"),
                                         guard=spec.get("guard"), created_by=body.created_by, created_via="template",
                                         commit=False)
            for child in spec.get("children") or []:
                await self.add_cell(nb, child.get("kind"), child.get("spec") or {}, title=child.get("title"),
                                    guard=child.get("guard"), parent_cell_id=parent.id, created_by=body.created_by,
                                    created_via="template", commit=False)
        await self.s.commit()
        return nb

    async def _template_cells(self, project: ProjectModel, slug: str, connection: str | None) -> list[dict[str, Any]]:
        """Cells of a template (W2's ``templates.py``: ``instantiate(slug, connection=…) -> [cell dict]``)."""
        try:
            from . import templates as T  # type: ignore[attr-defined]
        except ImportError:
            raise notebook_error(404, "template_not_found", f"template '{slug}' not found") from None
        fn = getattr(T, "instantiate", None)
        if fn is None:
            raise notebook_error(404, "template_not_found", f"template '{slug}' not found")
        try:
            out = fn(slug, connection=connection)
            if hasattr(out, "__await__"):
                out = await out
        except Exception as e:  # TemplateNotFound (W2) / KeyError
            if type(e).__name__ in ("TemplateNotFound", "KeyError"):
                raise notebook_error(404, "template_not_found", f"template '{slug}' not found") from None
            raise
        return list(out or [])

    async def patch(self, nb: NotebookModel, body: Any) -> NotebookModel:
        for k in ("title", "description", "environment", "run_as"):
            v = getattr(body, k)
            if v is not None or k in body.model_fields_set:
                if k == "title" and v is None:
                    continue
                setattr(nb, k, v)
        nb.updated_at = datetime.now()
        await self.s.commit()
        return nb

    async def delete(self, nb: NotebookModel) -> None:
        await self.s.delete(nb)
        await self.s.commit()

    # cells
    async def _validate(self, nb: NotebookModel, kind: str, spec: dict[str, Any], guard: Any,
                        parent: NotebookCellModel | None) -> dict[str, Any]:
        clean = validate_spec(kind, spec)
        if kind == "data":
            clean = normalize_data_spec(clean)
            if len(clean.get("rows") or []) > MAX_DATA_ROWS:
                msg = f"a data cell holds at most {MAX_DATA_ROWS} rows"
                raise notebook_error(422, "invalid_spec", msg, fields=[{"loc": ["rows"], "msg": msg}])
        if parent is not None:
            if parent.kind != "for_each" or parent.notebook_id != nb.id or parent.parent_cell_id is not None:
                raise notebook_error(422, "invalid_parent", "parent_cell_id must be a for-each cell of this notebook")
            if kind in TOP_LEVEL_ONLY_KINDS:
                raise notebook_error(422, "invalid_parent", f"a {kind} cell can not go inside a for-each")
        elif R.uses_row(clean) or R.uses_row(guard):
            raise notebook_error(422, "invalid_parent",
                                 "{{row.…}} references only work inside a for-each body (add it with parent_cell_id)")
        for r in R.refs_in(clean) + R.refs_in(guard):
            try:
                R.parse_path(r)
            except R.BadRef as e:
                raise notebook_error(422, "invalid_spec", e.message, fields=[{"loc": ["spec"], "msg": e.message}])
        if kind == "query":
            from .. import external_db as xdb

            try:
                xdb.check_select(clean["sql"])
            except xdb.ExternalDbError as e:
                raise notebook_error(422, "not_select", e.message) from None
        if kind in ("query", "change") and "{{" not in str(clean.get("connection")):
            conn = await resolve_connection(self.s, nb.project_id, clean["connection"])
            if conn is None:
                raise notebook_error(422, "connection_not_found", f"connection '{clean['connection']}' is not in this project")
            if kind == "change" and conn.readonly:
                raise notebook_error(422, "connection_readonly",
                                     f"connection '{conn.key}' is read-only; a change cell needs a writable connection")
        if kind == "change" and not clean.get("key"):
            raise notebook_error(422, "invalid_spec", "a change needs a key (which rows to change)",
                                 fields=[{"loc": ["key"], "msg": "must not be empty"}])
        if kind == "change" and not clean.get("set"):
            raise notebook_error(422, "invalid_spec", "a change needs set (the new values)",
                                 fields=[{"loc": ["set"], "msg": "must not be empty"}])
        if kind == "for_each" and clean.get("mode") == "child_task":
            raise notebook_error(422, "invalid_spec", "for-each mode child_task is coming; use sequential or parallel",
                                 fields=[{"loc": ["mode"], "msg": "child_task is not available yet"}])
        if kind == "wait":
            from .rule import RuleError, duration_seconds

            mode = clean.get("mode") or "duration"
            if mode == "duration" and not clean.get("duration"):
                raise notebook_error(422, "invalid_spec", "a duration wait needs duration (e.g. \"30s\", \"2h\")",
                                     fields=[{"loc": ["duration"], "msg": "required"}])
            if mode == "condition" and not clean.get("condition"):
                raise notebook_error(422, "invalid_spec", "a condition wait needs condition {connection, sql}",
                                     fields=[{"loc": ["condition"], "msg": "required"}])
            for f in ("duration", "poll_every", "timeout"):
                if clean.get(f) and "{{" not in str(clean[f]):
                    try:
                        secs = duration_seconds(clean[f])
                    except RuleError as e:
                        raise notebook_error(422, "invalid_spec", e.message, fields=[{"loc": [f], "msg": e.message}])
                    if secs > MAX_WAIT_S:
                        msg = f"{f} is longer than 7 days"
                        raise notebook_error(422, "invalid_spec", msg, fields=[{"loc": [f], "msg": msg}])
            cond = clean.get("condition") or None
            if mode == "condition" and cond:
                from .. import external_db as xdb

                try:
                    xdb.check_select(cond["sql"])
                except xdb.ExternalDbError as e:
                    raise notebook_error(422, "not_select", e.message) from None
                if await resolve_connection(self.s, nb.project_id, cond["connection"]) is None:
                    raise notebook_error(422, "connection_not_found",
                                         f"connection '{cond['connection']}' is not in this project")
        if kind == "store" and not (clean.get("table_id") or clean.get("new_table")):
            raise notebook_error(422, "invalid_spec", "a store cell needs table_id or new_table",
                                 fields=[{"loc": ["table_id"], "msg": "table_id or new_table is required"}])
        if kind == "http" and not (clean.get("url") or clean.get("api_ref")):
            raise notebook_error(422, "invalid_spec", "an http cell needs url or api_ref",
                                 fields=[{"loc": ["url"], "msg": "url or api_ref is required"}])
        return clean

    async def add_cell(self, nb: NotebookModel, kind: str | None, spec: dict[str, Any], *, title: str | None = None,
                       guard: Any = None, parent_cell_id: str | None = None, created_by: str | None = None,
                       created_via: str = "user", commit: bool = True) -> NotebookCellModel:
        parent = None
        if parent_cell_id:
            parent = await self.s.get(NotebookCellModel, parent_cell_id)
            if parent is None:
                raise notebook_error(422, "invalid_parent", "parent cell not found")
        if hasattr(guard, "model_dump"):
            guard = guard.model_dump(mode="json")
        clean = await self._validate(nb, kind or "", spec or {}, guard, parent)
        # lock the notebook row so two concurrent appends get distinct names / positions
        await self.s.execute(select(NotebookModel.id).where(NotebookModel.id == nb.id).with_for_update())
        names = (await self.s.execute(select(NotebookCellModel.name).where(
            NotebookCellModel.notebook_id == nb.id))).scalars().all()
        nums = [int(n[1:]) for n in names if n.startswith("c") and n[1:].isdigit()]
        pos_q = select(func.max(NotebookCellModel.position)).where(NotebookCellModel.notebook_id == nb.id)
        pos_q = pos_q.where(NotebookCellModel.parent_cell_id == parent.id) if parent else \
            pos_q.where(NotebookCellModel.parent_cell_id.is_(None))
        pos = ((await self.s.execute(pos_q)).scalar() or 0) + 1
        now = datetime.now()
        c = NotebookCellModel(id=generate_id("cell"), notebook_id=nb.id, parent_cell_id=parent.id if parent else None,
                              position=pos, name=f"c{max(nums, default=0) + 1}", kind=kind or "", title=title,
                              spec=clean, guard=guard, created_by=created_by, created_via=created_via,
                              created_at=now, updated_at=now)
        self.s.add(c)
        nb.updated_at = now
        if parent is not None:
            parent.updated_at = now  # the loop's body changed: the loop's result is stale
        if commit:
            await self.s.commit()
        else:
            await self.s.flush()
        return c

    async def patch_cell(self, c: NotebookCellModel, body: Any) -> tuple[NotebookCellModel, str | None]:
        if c.kind in REMOVED_KINDS:  # read-only card: no edit at all (a title-only patch would skip validate_spec)
            raise notebook_error(422, "kind_removed", f"{c.kind} cells were removed; delete this step instead")
        nb = await self.require_notebook(c.notebook_id)
        parent = await self.s.get(NotebookCellModel, c.parent_cell_id) if c.parent_cell_id else None
        changed = False
        new_spec = c.spec
        new_guard = c.guard
        if body.spec is not None:
            new_spec = body.spec
        if body.clear_guard:
            new_guard = None
        elif body.guard is not None:
            new_guard = body.guard.model_dump(mode="json")
        if body.spec is not None or body.guard is not None or body.clear_guard:
            clean = await self._validate(nb, c.kind, new_spec, new_guard, parent)
            if clean != (c.spec or {}) or new_guard != c.guard:
                changed = True
                c.spec, c.guard = clean, new_guard
        if body.title is not None:
            c.title = body.title
        now = datetime.now()
        if changed:
            c.updated_at = now
            if parent is not None:
                parent.updated_at = now
        nb.updated_at = now
        await self.s.commit()
        return c, (c.id if changed else None)

    async def delete_cell(self, c: NotebookCellModel) -> None:
        ids = [c.id] + list((await self.s.execute(select(NotebookCellModel.id).where(
            NotebookCellModel.parent_cell_id == c.id))).scalars().all())
        if await self.written_cell_ids(ids):
            raise notebook_error(409, "cell_applied", "this cell changed data (an applied write); it can not be deleted")
        nb = await self.require_notebook(c.notebook_id)
        if nb.active_run_id:
            raise notebook_error(409, "run_in_progress", "a run is in progress", run_id=nb.active_run_id)
        for cid in ids[1:]:
            child = await self.s.get(NotebookCellModel, cid)
            if child is not None:
                await self.s.delete(child)
        await self.s.flush()
        await self.s.delete(c)
        # the cells below lose an input: bump their updated_at (the staleness watermark) so they show stale
        now = datetime.now()
        below = (NotebookCellModel.parent_cell_id.is_(None) if c.parent_cell_id is None
                 else NotebookCellModel.parent_cell_id == c.parent_cell_id)
        await self.s.execute(update(NotebookCellModel).where(
            NotebookCellModel.notebook_id == c.notebook_id, below, NotebookCellModel.position > c.position)
            .values(updated_at=now))
        if c.parent_cell_id is not None:
            await self.s.execute(update(NotebookCellModel).where(NotebookCellModel.id == c.parent_cell_id)
                                 .values(updated_at=now))
        nb.updated_at = now
        await self.s.commit()

    # read model
    async def _effect(self, c: NotebookCellModel, children: list[NotebookCellModel]) -> str:
        return await fx.cell_effect(self.s, c.kind, c.spec or {}, [(x.kind, x.spec or {}) for x in children])

    async def latest_results(self, cell_ids: list[str]) -> dict[str, tuple[NotebookCellResultModel, NotebookRunModel]]:
        if not cell_ids:
            return {}
        sub = (select(NotebookCellResultModel.id).where(NotebookCellResultModel.cell_id.in_(cell_ids))
               .distinct(NotebookCellResultModel.cell_id)
               .order_by(NotebookCellResultModel.cell_id, NotebookCellResultModel.created_at.desc(),
                         NotebookCellResultModel.row_index.desc().nulls_first()))
        rows = (await self.s.execute(select(NotebookCellResultModel, NotebookRunModel)
                                     .join(NotebookRunModel, NotebookRunModel.id == NotebookCellResultModel.run_id)
                                     .where(NotebookCellResultModel.id.in_(sub)))).all()
        return {r.cell_id: (r, run) for r, run in rows}

    async def full(self, nb: NotebookModel) -> dict[str, Any]:
        await self.reap(nb)
        cells = await self.cells(nb.id)
        latest = await self.latest_results([c.id for c in cells])
        runs = list((await self.s.execute(select(NotebookRunModel).where(NotebookRunModel.notebook_id == nb.id)
                                          .order_by(NotebookRunModel.n.desc()).limit(RUNS_SHOWN))).scalars().all())
        newest = runs[0] if runs else None
        written = await self.written_cell_ids([c.id for c in cells])
        top = [c for c in cells if c.parent_cell_id is None]
        kids: dict[str, list[NotebookCellModel]] = {}
        for c in cells:
            if c.parent_cell_id:
                kids.setdefault(c.parent_cell_id, []).append(c)
        out_cells = []
        max_upd: datetime | None = None
        for c in top:
            children = sorted(kids.get(c.id, []), key=lambda x: x.position)
            upd = max([c.updated_at] + [x.updated_at for x in children])
            max_upd = upd if max_upd is None or upd > max_upd else max_upd
            d = await self._cell_out(c, children, latest, written, max_upd, newest)
            if c.kind == "for_each":
                d["children"] = [await self._cell_out(x, [], latest, written, max_upd, newest, top_position=c.position)
                                 for x in children]
            out_cells.append(d)
        return {**notebook_base(nb), "cells": out_cells, "runs": [run_out(r) for r in runs]}

    async def _cell_out(self, c: NotebookCellModel, children: list[NotebookCellModel], latest: dict, written: set[str],
                        max_upd: datetime | None, newest: NotebookRunModel | None,
                        top_position: int | None = None) -> dict[str, Any]:
        d = _cell_base(c)
        d["effect"] = await self._effect(c, children)
        d["needs_ai"] = c.kind == "ask"
        d["removed"] = c.kind in REMOVED_KINDS
        d["applied"] = c.id in written or any(x.id in written for x in children)
        lr = latest.get(c.id)
        d["latest"] = result_out(lr[0], lr[1].n) if lr else None
        stale = False
        if lr is not None:
            res, run = lr
            if max_upd is not None and run.started_at < max_upd:
                stale = True
            pos = top_position if top_position is not None else c.position
            if (newest is not None and newest.n > run.n and newest.status not in ("running", "waiting")
                    and newest.from_position <= pos):
                stale = True
        d["stale"] = stale
        d["children"] = None
        return d


    async def cell_view(self, c: NotebookCellModel) -> dict[str, Any]:
        """One cell as the notebook read model shows it (effect, latest, stale, children)."""
        nb = await self.require_notebook(c.notebook_id)
        full = await self.full(nb)
        for top in full["cells"]:
            if top["id"] == c.id:
                return top
            for ch in top.get("children") or []:
                if ch["id"] == c.id:
                    return ch
        raise notebook_error(404, "cell_not_found", "Cell not found")

    async def run_view(self, run: NotebookRunModel) -> dict[str, Any]:
        nb = await self.s.get(NotebookModel, run.notebook_id)
        if nb is not None:
            await self.reap(nb)
            await self.s.refresh(run)
        results = (await self.s.execute(select(NotebookCellResultModel).where(
            NotebookCellResultModel.run_id == run.id).order_by(NotebookCellResultModel.created_at))).scalars().all()
        return {**run_out(run), "results": [result_out(r, run.n) for r in results]}
