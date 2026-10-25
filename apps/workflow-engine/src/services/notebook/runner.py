"""The notebook run loop (BUILD-PLAN §1.2).

- ``start_run`` takes the notebook lock (a conditional UPDATE of ``notebooks.active_run_id``), creates the run row and
  starts ``_drive`` as an asyncio task with its own sessions. A second start while a run holds the lock gets 409
  ``run_in_progress {run_id}``; a lock whose run's heartbeat is older than 30 s is reaped first (run -> ``interrupted``).
- The loop goes over top-level cells with ``position >= from_position``:
  note -> nothing; guard false -> ``guard_false``; a cell that already wrote (a ``notebook_writes`` row) and is not in
  ``reapply`` -> stop (``stopped_at_write``) or, with ``on_applied: skip``, ``kept_earlier`` (the earlier output);
  ``needs_approval`` / ``waiting`` -> the run pauses as ``needs_you`` and the lock is freed; an error -> ``failed``.
- ``apply`` / ``discard`` act on a paused change cell and resume the same run from the next cell.
- Cells of a removed kind (approval / person, ``schemas.notebook.REMOVED_KINDS``) are skipped like notes.
- Writes recorded in ``notebook_writes``: an applied change (idempotency key = ``spec.idempotency_key`` or
  sha256(cell, key, set)), a store (sha256(cell, run, rows)), and a successful write-classified http (method not
  GET/HEAD) or workflow cell (sha256(cell, resolved inputs); before = the request, after = the response summary), so a
  rerun stops before every kind of write that already happened.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
from datetime import datetime
from typing import Any

from sqlalchemy import func, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.exc import IntegrityError

from ...db.notebook_models import (
    NotebookCellModel,
    NotebookCellResultModel,
    NotebookModel,
    NotebookRunModel,
    NotebookWriteModel,
)
from ...db.session import async_session_factory
from ...schemas.notebook import REMOVED_KINDS, notebook_error
from ...utils.ids import generate_id
from . import refs as R
from . import rule as rule_mod
from . import effects as fx
from . import storage
from .executors import CellContext, CellOutcome, ExecutorUnavailable, get_executor

log = logging.getLogger(__name__)

HEARTBEAT_S = 2.0
USABLE = ("success", "kept_earlier", "applied")
LIVE = ("running", "waiting")
_TASKS: dict[str, asyncio.Task] = {}
_RUN_STATUS_TO_NB = {"done": "done", "stopped_at_write": "done", "failed": "failed", "needs_you": "needs_you",
                     "cancelled": "idle", "interrupted": "idle"}


# ── lock ──────────────────────────────────────────────────────────────────────────────────────────────────────────────


async def _take_lock(s: Any, nb: NotebookModel, run_id: str) -> bool:
    res = await s.execute(update(NotebookModel).where(NotebookModel.id == nb.id, NotebookModel.active_run_id.is_(None))
                          .values(active_run_id=run_id, status="running"))
    await s.commit()
    return (res.rowcount or 0) == 1


async def _open_run(s: Any, nb: NotebookModel, build: Any) -> NotebookRunModel:
    """Insert the run row and take the lock in ONE transaction, so whoever sees the lock also sees its run (a lock
    with no run row would look reapable to a racing start). ``build(n)`` makes the run. 409 when another run holds it."""
    from .service import NotebookService

    nb_id = nb.id
    for attempt in range(2):
        n = ((await s.execute(select(func.max(NotebookRunModel.n)).where(NotebookRunModel.notebook_id == nb_id)))
             .scalar() or 0) + 1
        run = build(n)
        s.add(run)
        try:
            await s.flush()
            res = await s.execute(update(NotebookModel).where(NotebookModel.id == nb_id,
                                                              NotebookModel.active_run_id.is_(None))
                                  .values(active_run_id=run.id, status="running"))
            if (res.rowcount or 0) == 1:
                await s.commit()
                return run
        except IntegrityError:
            pass
        await s.rollback()
        nb = await s.get(NotebookModel, nb_id, populate_existing=True)
        if attempt == 0:
            await NotebookService(s).reap(nb)
    raise notebook_error(409, "run_in_progress", "a run of this notebook is in progress", run_id=nb.active_run_id)


async def _settle_notebook(s: Any, run_id: str, run_status: str) -> None:
    """Free the lock and set the notebook status from the run's final status, in the caller's transaction (the same
    commit as the run's final status: a reader never sees a notebook 'running' with no active run)."""
    await s.execute(update(NotebookModel).where(NotebookModel.active_run_id == run_id)
                    .values(active_run_id=None, status=_RUN_STATUS_TO_NB.get(run_status, "idle"),
                            updated_at=datetime.now()))


async def _free_lock(run_id: str, run_status: str) -> None:
    async with async_session_factory() as s:
        await s.execute(update(NotebookModel).where(NotebookModel.active_run_id == run_id)
                        .values(active_run_id=None, status=_RUN_STATUS_TO_NB.get(run_status, "idle"),
                                updated_at=datetime.now()))
        await s.commit()


# ── start / cancel ────────────────────────────────────────────────────────────────────────────────────────────────────


async def start_run(s: Any, nb: NotebookModel, body: Any) -> NotebookRunModel:
    from_position = 0
    if body.from_cell_id:
        c = await s.get(NotebookCellModel, body.from_cell_id)
        if c is None or c.notebook_id != nb.id:
            raise notebook_error(404, "cell_not_found", "from_cell_id is not a cell of this notebook")
        if c.parent_cell_id:
            c = await s.get(NotebookCellModel, c.parent_cell_id)
        from_position = c.position
    run_id, nb_id, nb_run_as = generate_id("nbr"), nb.id, nb.run_as  # plain values: a rollback expires nb

    def build(n: int) -> NotebookRunModel:
        now = datetime.now()
        return NotebookRunModel(id=run_id, notebook_id=nb_id, n=n, from_position=from_position, status="running",
                                on_applied=body.on_applied, reapply=list(body.reapply or []),
                                triggered_by=body.triggered_by, run_as=body.run_as or nb_run_as,
                                via_agent=body.via_agent, heartbeat_at=now, started_at=now,
                                continue_on_fail=bool(getattr(body, "continue_on_fail", False)))

    run = await _open_run(s, nb, build)
    _spawn(run_id, from_position)
    return run


def _spawn(run_id: str, from_position: int, only: tuple[str, list[int]] | None = None) -> None:
    task = asyncio.create_task(_drive(run_id, from_position, only))
    _TASKS[run_id] = task
    task.add_done_callback(lambda _t: _TASKS.pop(run_id, None))


async def cancel_run(s: Any, run: NotebookRunModel) -> NotebookRunModel:
    task = _TASKS.get(run.id)
    if task is not None and not task.done():
        task.cancel()
        try:
            await asyncio.wait_for(asyncio.shield(task), timeout=10)
        except BaseException:  # noqa: BLE001 - CancelledError / timeout: the row is settled below either way
            pass
    await s.refresh(run)
    if run.status in ("running", "waiting", "needs_you"):
        pending_cell = (run.stop or {}).get("cell_id")
        run.status, run.finished_at = "cancelled", datetime.now()
        run.stop = {"reason": "cancelled", "cell_id": pending_cell}
        await s.execute(update(NotebookCellResultModel).where(
            NotebookCellResultModel.run_id == run.id,
            NotebookCellResultModel.status.in_(("needs_approval", "waiting"))).values(status="cancelled"))
        await _settle_notebook(s, run.id, "cancelled")
        await s.commit()
        await s.refresh(run)
    return run


# ── the loop ──────────────────────────────────────────────────────────────────────────────────────────────────────────


async def _heartbeat(run_id: str) -> None:
    while True:
        await asyncio.sleep(HEARTBEAT_S)
        try:
            async with async_session_factory() as s:
                await s.execute(update(NotebookRunModel).where(NotebookRunModel.id == run_id,
                                                               NotebookRunModel.status.in_(LIVE))
                                .values(heartbeat_at=datetime.now()))
                await s.commit()
        except Exception:  # noqa: BLE001
            log.exception("notebook heartbeat failed")


async def _finish(run_id: str, status: str, stop: dict[str, Any] | None) -> None:
    async with async_session_factory() as s:
        values: dict[str, Any] = {"status": status, "stop": stop}
        if status != "needs_you":
            values["finished_at"] = datetime.now()
        await s.execute(update(NotebookRunModel).where(NotebookRunModel.id == run_id,
                                                       NotebookRunModel.status.in_(LIVE)).values(**values))
        st = (await s.execute(select(NotebookRunModel.status).where(NotebookRunModel.id == run_id))).scalar()
        await _settle_notebook(s, run_id, st or status)  # same transaction as the run's final status
        await s.commit()


async def _still_running(run_id: str) -> bool:
    async with async_session_factory() as s:
        st = (await s.execute(select(NotebookRunModel.status).where(NotebookRunModel.id == run_id))).scalar()
        return st in LIVE


async def _drive(run_id: str, from_position: int, only: tuple[str, list[int]] | None = None) -> None:
    hb = asyncio.create_task(_heartbeat(run_id))
    final = "failed"
    try:
        final, stop = await _loop(run_id, from_position, only)
        await _finish(run_id, final, stop)
    except asyncio.CancelledError:
        final = "cancelled"
        await asyncio.shield(_finish(run_id, "cancelled", {"reason": "cancelled", "cell_id": None}))
        raise
    except Exception as e:  # noqa: BLE001
        log.exception("notebook run %s crashed", run_id)
        await _finish(run_id, "failed", {"reason": "cell_failed", "cell_id": None, "message": str(e)[:300]})
    finally:
        hb.cancel()
        async with async_session_factory() as s:
            st = (await s.execute(select(NotebookRunModel.status).where(NotebookRunModel.id == run_id))).scalar()
        await asyncio.shield(_free_lock(run_id, st or final))


class _Values:
    """Values for ``{{cells.<name>.<key>}}``: the newest usable result of each cell (this run first)."""

    def __init__(self, by_name: dict[str, NotebookCellModel]) -> None:
        self.by_name = by_name
        self.results: dict[str, NotebookCellResultModel] = {}  # cell name -> result
        self.env: dict[str, Any] = {}  # the active environment's variables (a secret is ``{{ $vars.KEY }}``)

    def var(self, name: str, local: dict[str, NotebookCellResultModel] | None = None,
            visible: set[str] | None = None) -> Any:
        """``{{vars.name}}``: the newest value a visible http cell saved (``output.vars``), else the environment."""
        cells = sorted((c for c in self.by_name.values() if visible is None or c.name in visible),
                       key=lambda c: (c.parent_cell_id is not None, c.position), reverse=True)
        for c in cells:
            res = (local or {}).get(c.name) or self.results.get(c.name)
            got = ((res.output or {}).get("vars") if res is not None and isinstance(res.output, dict) else None) or {}
            if name in got:
                return got[name]
        if name in self.env:
            return self.env[name]
        raise KeyError(name)

    def resolver(self, visible: set[str] | None, local: dict[str, NotebookCellResultModel] | None = None,
                 row: Any = None, has_row: bool = False) -> R.Resolver:
        return R.Resolver(lambda n, k: self.get(n, k, local, visible), row=row, has_row=has_row,
                          var=lambda n: self.var(n, local, visible))

    def visible(self, cell: NotebookCellModel) -> set[str]:
        """Names a cell may reference: the cells above it (position order). A for-each body cell sees the cells above
        its loop, the loop itself and the body cells above it. Never itself, never a cell below."""
        cells = list(self.by_name.values())
        if cell.parent_cell_id is None:
            return {c.name for c in cells if c.parent_cell_id is None and c.position < cell.position}
        parent = next((c for c in cells if c.id == cell.parent_cell_id), None)
        top = {c.name for c in cells if c.parent_cell_id is None and parent is not None and c.position < parent.position}
        sib = {c.name for c in cells if c.parent_cell_id == cell.parent_cell_id and c.position < cell.position}
        return top | sib | ({parent.name} if parent is not None else set())

    def get(self, name: str, key: str, local: dict[str, NotebookCellResultModel] | None = None,
            visible: set[str] | None = None) -> Any:
        if name not in self.by_name:
            raise KeyError(f"there is no cell {name}")
        if visible is not None and name not in visible:
            raise KeyError(f"cell {name} is not above this cell (a cell can only use the cells above it)")
        res = (local or {}).get(name) or self.results.get(name)
        if res is None:
            raise KeyError(f"cell {name} has no result yet (run it first)")
        out = res.output or {}
        if key == "output":
            return out
        if key == "rows":
            if res.output_ref:
                full = storage.read_rows(res.output_ref)
                if full is not None:
                    return full.get("rows") or []
            return out.get("rows") or []
        if key == "count":
            # a rule's own count (count rule: {count: 2, row_count: 0}) wins over the size of its row preview
            if out.get("count") is not None:
                return out["count"]
            return out.get("row_count", len(out.get("rows") or []))
        if key == "status":
            return res.status
        if key in out:
            return out[key]
        raise KeyError(f"cell {name} has no '{key}'")


async def load_env(s: Any, nb_id: str) -> dict[str, Any]:
    """The active environment's variables: ``{name: value}``; a secret entry becomes ``{{ $vars.KEY }}``."""
    from ...db.notebook_models import NotebookEnvironmentModel

    row = await s.get(NotebookEnvironmentModel, nb_id)
    if row is None or not row.active:
        return {}
    for st in row.sets or []:
        if st.get("name") == row.active:
            return {v["name"]: (v["value"] if v.get("secret") is None else "{{ $vars.%s }}" % v["secret"])
                    for v in st.get("vars") or [] if v.get("name")}
    return {}


async def _load_values(s: Any, nb_id: str, cells: list[NotebookCellModel], run_id: str) -> _Values:
    v = _Values({c.name: c for c in cells})
    v.env = await load_env(s, nb_id)
    ids = [c.id for c in cells]
    if ids:
        rows = (await s.execute(select(NotebookCellResultModel).where(
            NotebookCellResultModel.cell_id.in_(ids), NotebookCellResultModel.row_index.is_(None),
            NotebookCellResultModel.status.in_(USABLE + ("needs_approval",)))
            .order_by(NotebookCellResultModel.created_at))).scalars().all()
        name_of = {c.id: c.name for c in cells}
        for r in rows:
            if r.status == "needs_approval" and r.run_id != run_id:
                continue
            v.results[name_of[r.cell_id]] = r
    return v


async def _save(s: Any, run: NotebookRunModel, cell: NotebookCellModel, out: CellOutcome,
                row_index: int | None = None) -> NotebookCellResultModel:
    ref = None
    if out.full_rows is not None and len(out.full_rows) > storage.PREVIEW_ROWS:
        cols = (out.output or {}).get("columns") or storage.columns_of(out.full_rows)
        ref = storage.write_rows(run.notebook_id, run.id, cell.id, row_index, out.full_rows, cols)
    error = out.error
    if error is not None:
        error = {"code": error.get("code") or "internal", "message": str(error.get("message") or ""),
                 **{k: v for k, v in error.items() if k not in ("code", "message")}}
    r = NotebookCellResultModel(id=generate_id("nbres"), run_id=run.id, cell_id=cell.id, row_index=row_index,
                                status=out.status, output=_jsonable(out.output), output_ref=ref,
                                error=_jsonable(error), execution_id=out.execution_id, job_id=out.job_id, ms=out.ms,
                                created_at=datetime.now())
    s.add(r)
    await s.commit()
    return r


def _jsonable(v: Any) -> Any:
    return None if v is None else json.loads(json.dumps(v, default=str))


async def _written(s: Any, cell_ids: list[str]) -> NotebookWriteModel | None:
    return (await s.execute(select(NotebookWriteModel).where(NotebookWriteModel.cell_id.in_(cell_ids))
                            .order_by(NotebookWriteModel.applied_at.desc()).limit(1))).scalars().first()


async def _record_store_write(s: Any, run: NotebookRunModel, cell: NotebookCellModel, inputs: dict[str, Any],
                              out: CellOutcome, row_index: int | None) -> None:
    raw = json.dumps({"cell": cell.id, "run": run.id, "row": row_index, "rows": inputs.get("rows")},
                     sort_keys=True, default=str)
    key = "store_" + hashlib.sha256(raw.encode()).hexdigest()[:40]
    await s.execute(pg_insert(NotebookWriteModel.__table__).values(
        id=generate_id("nbw"), cell_id=cell.id, run_id=run.id, row_index=row_index, idempotency_key=key,
        before=None, after=_jsonable(out.output), applied_by=run.triggered_by, applied_at=datetime.now())
        .on_conflict_do_nothing(index_elements=["idempotency_key"]))
    await s.commit()


_CALL_WRITE_KINDS = ("http", "workflow")


def resends(cell: NotebookCellModel) -> bool:
    """An http cell marked ``rerun: "always"``: re-sending it on every run is intended."""
    return cell.kind == "http" and (cell.spec or {}).get("rerun") == "always"


def continues(run: NotebookRunModel, cell: NotebookCellModel) -> bool:
    """A failure of this cell does not stop the run (the run option, or the cell's ``continue_on_fail``)."""
    return bool(getattr(run, "continue_on_fail", False)) or (cell.spec or {}).get("continue_on_fail") is True


async def _record_call_write(s: Any, run: NotebookRunModel, cell: NotebookCellModel, inputs: dict[str, Any],
                             out: CellOutcome, row_index: int | None) -> None:
    """A successful http/workflow cell that writes: one ``notebook_writes`` row keyed by cell + resolved inputs."""
    if cell.kind not in _CALL_WRITE_KINDS or out.status != "success":
        return
    if resends(cell):  # safe to send again (a login, a test call): not an applied write, so a rerun goes on
        return
    if not fx.is_write(await fx.cell_effect(s, cell.kind, inputs)):
        return
    raw = json.dumps({"cell": cell.id, "inputs": inputs}, sort_keys=True, default=str)
    key = cell.kind + "_" + hashlib.sha256(raw.encode()).hexdigest()[:40]
    o = out.output if isinstance(out.output, dict) else {}
    if cell.kind == "http":
        before = {"method": str(inputs.get("method") or "GET").upper(), "url": inputs.get("url"),
                  "body": inputs.get("body")}
        after = {"status": o.get("status"), "row_count": o.get("row_count"), "execution_id": out.execution_id}
    else:
        before = {"workflow_id": inputs.get("workflow_id"), "version": inputs.get("version"),
                  "inputs": inputs.get("inputs")}
        after = {"status": o.get("status"), "row_count": o.get("row_count"), "execution_id": out.execution_id}
    await s.execute(pg_insert(NotebookWriteModel.__table__).values(
        id=generate_id("nbw"), cell_id=cell.id, run_id=run.id, row_index=row_index, idempotency_key=key,
        before=_jsonable(before), after=_jsonable(after), applied_by=run.triggered_by, applied_at=datetime.now())
        .on_conflict_do_nothing(index_elements=["idempotency_key"]))
    await s.commit()


async def _exec_one(ctx: CellContext, cell: NotebookCellModel, values: _Values,
                    local: dict[str, NotebookCellResultModel] | None = None) -> tuple[CellOutcome, dict[str, Any]]:
    """Guard, references, executor. Returns ``(outcome, resolved inputs)``."""
    visible = values.visible(cell)
    resolver = values.resolver(visible, local, row=ctx.row, has_row=ctx.row_index is not None)
    try:
        if cell.guard:
            g = cell.guard
            left = resolver.resolve(g.get("left"))
            right = resolver.resolve(g.get("right"))
            if not rule_mod.guard_holds(left, g.get("op") or "nonempty", right):
                return CellOutcome(status="guard_false", output={"guard": g, "left": _short(left)}), {}
        inputs = resolver.resolve(cell.spec or {})
    except R.BadRef as e:
        return CellOutcome.fail("bad_ref", e.message, ref=e.ref), {}
    try:
        mod = get_executor(cell.kind)
    except ExecutorUnavailable as e:
        code = "browser_unavailable" if cell.kind == "browser" else "llm_unavailable" if cell.kind == "ask" else "internal"
        return CellOutcome.fail(code, str(e)), inputs
    try:
        return await mod.run(ctx, cell, inputs), inputs
    except asyncio.CancelledError:
        raise
    except Exception as e:  # noqa: BLE001 - an executor bug is a cell error, never a dead run
        log.exception("notebook executor %s crashed", cell.kind)
        return CellOutcome.fail("internal", f"{type(e).__name__}: {str(e)[:300]}"), inputs


def _short(v: Any) -> Any:
    if isinstance(v, list):
        return {"rows": len(v)}
    return v


async def _loop(run_id: str, from_position: int,
                only: tuple[str, list[int]] | None = None) -> tuple[str, dict[str, Any] | None]:
    """``only = (for_each cell id, row indexes)``: a retry of some rows of one loop; nothing else runs."""
    async with async_session_factory() as s:
        run = await s.get(NotebookRunModel, run_id)
        nb = await s.get(NotebookModel, run.notebook_id)
        cells = list((await s.execute(select(NotebookCellModel).where(NotebookCellModel.notebook_id == nb.id)
                                      .order_by(NotebookCellModel.position))).scalars().all())
        top = [c for c in cells if c.parent_cell_id is None]
        kids: dict[str, list[NotebookCellModel]] = {}
        for c in cells:
            if c.parent_cell_id:
                kids.setdefault(c.parent_cell_id, []).append(c)
        values = await _load_values(s, nb.id, cells, run_id)
        attribution = {"triggered_by": run.triggered_by, "run_as": run.run_as, "via_agent": run.via_agent}
        reapply = set(run.reapply or [])
        done_here = set((await s.execute(select(NotebookCellResultModel.cell_id).where(
            NotebookCellResultModel.run_id == run_id, NotebookCellResultModel.row_index.is_(None)))).scalars().all())
        continued = False

        for cell in top:
            if cell.position < from_position or cell.id in done_here or (only and cell.id != only[0]):
                continue
            if not await _still_running(run_id):
                st = (await s.execute(select(NotebookRunModel.status).where(NotebookRunModel.id == run_id))).scalar()
                return st or "cancelled", None
            if cell.kind == "note" or cell.kind in REMOVED_KINDS:
                continue
            children = sorted(kids.get(cell.id, []), key=lambda x: x.position)
            # stop before a write that already happened
            if (cell.kind in ("change", "store", "for_each") + _CALL_WRITE_KINDS and cell.id not in reapply and not only
                    and not resends(cell)):
                w = await _written(s, [cell.id] + [x.id for x in children if not resends(x)])
                if w is not None:
                    if run.on_applied == "skip":
                        prev = values.results.get(cell.name)
                        r = await _save(s, run, cell, CellOutcome(
                            status="kept_earlier", output=(prev.output if prev else None) or {
                                "note": "kept the earlier result"}))
                        if prev is not None and prev.output_ref:
                            r.output_ref = prev.output_ref
                            await s.commit()
                        values.results[cell.name] = r
                        continue
                    return "stopped_at_write", {"reason": "applied_write", "cell_id": cell.id,
                                                "applied_at": w.applied_at.isoformat(), "applied_by": w.applied_by}
            ctx = CellContext(notebook_id=nb.id, project_id=nb.project_id, environment=nb.environment, run_id=run_id,
                              run_n=run.n, attribution=attribution)
            if cell.kind == "for_each":
                ctx.run_body = _body_runner(run, ctx, children, values)
                if only:
                    prev = (await s.execute(select(NotebookCellResultModel).where(
                        NotebookCellResultModel.cell_id == cell.id, NotebookCellResultModel.row_index.is_(None),
                        NotebookCellResultModel.run_id != run_id)
                        .order_by(NotebookCellResultModel.created_at.desc()).limit(1))).scalars().first()
                    ctx.only_rows, ctx.previous = list(only[1]), (prev.output if prev else None)
            out, inputs = await _exec_one(ctx, cell, values)
            r = await _save(s, run, cell, out)
            if out.status in USABLE or out.status == "needs_approval":
                values.results[cell.name] = r
            if cell.kind == "store" and out.status == "success":
                await _record_store_write(s, run, cell, inputs, out, None)
            await _record_call_write(s, run, cell, inputs, out, None)
            if out.status == "needs_approval":
                return "needs_you", {"reason": "needs_approval", "cell_id": cell.id}
            if out.status == "waiting":
                return "needs_you", {"reason": "waiting", "cell_id": cell.id}
            if out.status == "error":
                if continues(run, cell):
                    continued = True
                    # later cells must see this run's outcome, never an older run's success: the failed result when
                    # it has output (an http whose checks failed still has the response), else no result at all
                    if out.output:
                        values.results[cell.name] = r
                    else:
                        values.results.pop(cell.name, None)
                    continue
                return "failed", {"reason": "cell_failed", "cell_id": cell.id,
                                  "code": (out.error or {}).get("code")}
        if continued or run.continue_on_fail or any((c.spec or {}).get("continue_on_fail") is True for c in top):
            # a run that kept going past a failure still ends failed, at its first failing cell (this run's results,
            # so a failure before a pause for approval still counts after the resume)
            first = (await s.execute(select(NotebookCellResultModel.cell_id).join(
                NotebookCellModel, NotebookCellModel.id == NotebookCellResultModel.cell_id).where(
                NotebookCellResultModel.run_id == run_id, NotebookCellResultModel.row_index.is_(None),
                NotebookCellResultModel.status == "error", NotebookCellModel.parent_cell_id.is_(None))
                .order_by(NotebookCellModel.position).limit(1))).scalar()
            if first is not None:
                n_failed = (await s.execute(select(func.count()).select_from(NotebookCellResultModel).where(
                    NotebookCellResultModel.run_id == run_id, NotebookCellResultModel.row_index.is_(None),
                    NotebookCellResultModel.status == "error"))).scalar() or 0
                return "failed", {"reason": "cell_failed", "cell_id": first, "continued": True, "failed": n_failed}
        return "done", None


def _body_runner(run: NotebookRunModel, parent_ctx: CellContext, children: list[NotebookCellModel],
                 values: _Values):
    async def run_body(row: Any, i: int) -> CellOutcome:
        async with async_session_factory() as s:  # one session per row: rows may run in parallel
            return await _one_row(s, row, i)

    async def _one_row(s: Any, row: Any, i: int) -> CellOutcome:
        local: dict[str, NotebookCellResultModel] = {}
        ctx = CellContext(notebook_id=parent_ctx.notebook_id, project_id=parent_ctx.project_id,
                          environment=parent_ctx.environment, run_id=parent_ctx.run_id, run_n=parent_ctx.run_n,
                          attribution=parent_ctx.attribution, row=row, row_index=i)
        for child in children:
            if child.kind == "note" or child.kind in REMOVED_KINDS:
                continue
            out, inputs = await _exec_one(ctx, child, values, local)
            r = await _save(s, run, child, out, i)
            if out.status in USABLE:
                local[child.name] = r
            if child.kind == "store" and out.status == "success":
                await _record_store_write(s, run, child, inputs, out, i)
            await _record_call_write(s, run, child, inputs, out, i)
            if out.status == "error":
                return CellOutcome(status="error", error={**(out.error or {}), "cell_id": child.id,
                                                          "cell": child.name})
        return CellOutcome()

    return run_body


# ── apply / discard ─────────────────────────────────────────────────────────────────────────────────────────────────


async def _pending(s: Any, cell: NotebookCellModel, status: str) -> tuple[NotebookCellResultModel, NotebookRunModel]:
    r = (await s.execute(select(NotebookCellResultModel).where(
        NotebookCellResultModel.cell_id == cell.id, NotebookCellResultModel.row_index.is_(None))
        .order_by(NotebookCellResultModel.created_at.desc()).limit(1))).scalars().first()
    if r is None or r.status != status:
        raise notebook_error(409, "not_pending", f"this cell is not waiting for you (status: {r.status if r else 'none'})")
    run = await s.get(NotebookRunModel, r.run_id)
    return r, run


async def _resume(s: Any, nb: NotebookModel, run: NotebookRunModel, cell: NotebookCellModel) -> None:
    """Continue a paused run after ``cell``: re-take the lock with the same run id and drive from the next cell."""
    if run.status != "needs_you":
        return
    if not await _take_lock(s, nb, run.id):
        await s.refresh(nb)
        from .service import NotebookService

        await NotebookService(s).reap(nb)
        if not await _take_lock(s, nb, run.id):
            raise notebook_error(409, "run_in_progress", "another run of this notebook is in progress",
                                 run_id=nb.active_run_id)
    run.status, run.stop, run.heartbeat_at = "running", None, datetime.now()
    await s.commit()
    _spawn(run.id, cell.position + 1)


async def apply(s: Any, cell: NotebookCellModel, body: Any) -> tuple[NotebookCellResultModel, NotebookRunModel]:
    from .executors.change import apply_change, default_key
    from .executors._common import Fail, connection_url
    from .. import external_db as xdb

    if cell.kind != "change":
        raise notebook_error(409, "not_pending", "only a change cell can be applied")
    latest = (await s.execute(select(NotebookCellResultModel).where(
        NotebookCellResultModel.cell_id == cell.id, NotebookCellResultModel.row_index.is_(None))
        .order_by(NotebookCellResultModel.created_at.desc()).limit(1))).scalars().first()
    if latest is None:
        raise notebook_error(409, "not_pending", "run the notebook first; this change has no proposal yet")
    out = latest.output or {}
    idem = body.idempotency_key or out.get("idempotency_key") or default_key(cell.id, out.get("key") or {},
                                                                            out.get("set") or {})
    existing = (await s.execute(select(NotebookWriteModel).where(NotebookWriteModel.idempotency_key == idem))
                ).scalars().first()
    if latest.status == "applied":
        return await _same_result(s, latest)
    if latest.status == "needs_approval":
        # the proposal was computed from the cells as they were; an edit since (this cell or one above) makes it stale
        changed = (await s.execute(select(func.count()).select_from(NotebookCellModel).where(
            NotebookCellModel.notebook_id == cell.notebook_id,
            NotebookCellModel.updated_at > latest.created_at,
            (NotebookCellModel.id == cell.id) | ((NotebookCellModel.parent_cell_id.is_(None))
                                                 & (NotebookCellModel.position < cell.position))))).scalar() or 0
        if changed:
            raise notebook_error(409, "proposal_stale", "this cell or a cell above it changed after the proposal was "
                                 "made; rerun from this cell to get a fresh proposal, then apply that")
    from .service import resolve_connection

    conn0 = await resolve_connection(s, (await s.get(NotebookModel, cell.notebook_id)).project_id,
                                     out.get("connection_id") or (cell.spec or {}).get("connection"))
    if conn0 is not None and conn0.readonly:
        raise notebook_error(422, "connection_readonly", f"connection '{conn0.key}' is read-only")
    claim = await s.execute(update(NotebookCellResultModel).where(
        NotebookCellResultModel.id == latest.id, NotebookCellResultModel.status == "needs_approval")
        .values(status="applied"))
    await s.commit()
    if (claim.rowcount or 0) != 1:
        # another apply won the claim: wait for its write, then answer the same
        for _ in range(100):
            w = (await s.execute(select(NotebookWriteModel).where(NotebookWriteModel.cell_id == cell.id))).scalars().first()
            await s.refresh(latest)
            if w is not None or latest.status != "applied":
                break
            await asyncio.sleep(0.1)
        await s.refresh(latest)
        if latest.status == "applied":
            return await _same_result(s, latest)
        raise notebook_error(409, "not_pending", f"this cell is not waiting for you (status: {latest.status})")
    run = await s.get(NotebookRunModel, latest.run_id)
    nb = await s.get(NotebookModel, cell.notebook_id)
    if existing is not None:  # this key was applied before (a reapply with the same key): no second write
        latest.output = {**out, "already_applied": True, "applied_by": existing.applied_by,
                         "applied_at": existing.applied_at.isoformat(), "idempotency_key": idem}
        await s.commit()
        await _resume(s, nb, run, cell)
        await s.refresh(run)
        return latest, run
    ctx = CellContext(notebook_id=nb.id, project_id=nb.project_id, environment=nb.environment, run_id=run.id,
                      run_n=run.n)
    try:
        conn, url = await connection_url(ctx, out.get("connection_id") or (cell.spec or {}).get("connection"))
        if conn.readonly:
            raise xdb.ExternalDbError("connection_readonly", f"connection '{conn.key}' is read-only", 422)
        after = await apply_change(url, out.get("table") or "", out.get("key") or {}, out.get("set") or {})
    except (Fail, xdb.ExternalDbError, ValueError) as e:
        msg = e.outcome.error["message"] if isinstance(e, Fail) else getattr(e, "message", str(e))
        latest.status = "error"
        latest.error = {"code": "change_failed", "message": msg}
        await s.commit()
        if run.status == "needs_you":
            run.status, run.finished_at = "failed", datetime.now()
            run.stop = {"reason": "cell_failed", "cell_id": cell.id, "code": "change_failed"}
            await s.commit()
            await s.execute(update(NotebookModel).where(NotebookModel.id == nb.id, NotebookModel.active_run_id.is_(None))
                            .values(status="failed"))
            await s.commit()
        return latest, run
    now = datetime.now()
    w = NotebookWriteModel(id=generate_id("nbw"), cell_id=cell.id, run_id=run.id, idempotency_key=idem,
                           before=_jsonable({"rows": out.get("before") or out.get("rows") or []}),
                           after=_jsonable({"rows": after}), applied_by=body.approved_by, applied_at=now)
    s.add(w)
    try:
        await s.commit()
    except IntegrityError:
        await s.rollback()
    latest.output = {**out, "after": _jsonable(after[:50]), "applied_rows": len(after), "applied_by": body.approved_by,
                     "applied_at": now.isoformat(), "idempotency_key": idem}
    await s.commit()
    await _resume(s, nb, run, cell)
    await s.refresh(run)
    return latest, run


async def _same_result(s: Any, r: NotebookCellResultModel) -> tuple[NotebookCellResultModel, NotebookRunModel]:
    await s.refresh(r)
    return r, await s.get(NotebookRunModel, r.run_id)


async def discard(s: Any, cell: NotebookCellModel, by: str | None) -> tuple[NotebookCellResultModel, NotebookRunModel]:
    if cell.kind != "change":
        raise notebook_error(409, "not_pending", "only a change cell can be discarded")
    r, run = await _pending(s, cell, "needs_approval")
    claim = await s.execute(update(NotebookCellResultModel).where(
        NotebookCellResultModel.id == r.id, NotebookCellResultModel.status == "needs_approval")
        .values(status="discarded", output={**(r.output or {}), "discarded_by": by}))
    await s.commit()
    if (claim.rowcount or 0) != 1:
        raise notebook_error(409, "not_pending", "this cell is not waiting for you")
    nb = await s.get(NotebookModel, cell.notebook_id)
    await _resume(s, nb, run, cell)
    await s.refresh(r)
    await s.refresh(run)
    return r, run


async def retry_rows(s: Any, cell: NotebookCellModel, body: Any) -> NotebookRunModel:
    """A new run that reruns only some rows of one for-each (default: the failed rows of its latest result) and
    merges them into the loop's output; the cells below do not run."""
    if cell.kind != "for_each":
        raise notebook_error(422, "invalid", "only a for-each cell has rows to retry")
    latest = (await s.execute(select(NotebookCellResultModel).where(
        NotebookCellResultModel.cell_id == cell.id, NotebookCellResultModel.row_index.is_(None))
        .order_by(NotebookCellResultModel.created_at.desc()).limit(1))).scalars().first()
    if latest is None or not isinstance((latest.output or {}).get("rows"), list):
        raise notebook_error(409, "not_pending", "run the loop first; there are no rows to retry")
    rows = body.rows if body.rows is not None else [
        r["i"] for r in latest.output["rows"] if r.get("status") == "error"]
    if not rows:
        raise notebook_error(409, "not_pending", "no failed rows to retry")
    nb = await s.get(NotebookModel, cell.notebook_id)
    run_id, nb_id, nb_run_as, pos, cell_id = generate_id("nbr"), nb.id, nb.run_as, cell.position, cell.id

    def build(n: int) -> NotebookRunModel:
        now = datetime.now()
        return NotebookRunModel(id=run_id, notebook_id=nb_id, n=n, from_position=pos, status="running",
                                triggered_by=body.triggered_by, run_as=body.run_as or nb_run_as,
                                via_agent=body.via_agent, heartbeat_at=now, started_at=now, stop=None, reapply=[])

    run = await _open_run(s, nb, build)
    _spawn(run_id, pos, (cell_id, sorted(set(int(i) for i in rows))))
    return run


async def preview_rows(s: Any, cell: NotebookCellModel, n: int) -> dict[str, Any]:
    """The first ``n`` rows of a for-each with each body cell's spec resolved (nothing runs)."""
    if cell.kind != "for_each":
        raise notebook_error(422, "invalid", "only a for-each cell has a batch preview")
    cells = list((await s.execute(select(NotebookCellModel).where(NotebookCellModel.notebook_id == cell.notebook_id)
                                  .order_by(NotebookCellModel.position))).scalars().all())
    values = await _load_values(s, cell.notebook_id, cells, "")
    children = sorted([c for c in cells if c.parent_cell_id == cell.id], key=lambda c: c.position)
    try:
        vis = values.visible(cell)
        over = values.resolver(vis).resolve((cell.spec or {}).get("over"))
    except R.BadRef as e:
        return {"total": 0, "rows": [], "error": {"code": "bad_ref", "message": e.message}}
    if isinstance(over, dict) and isinstance(over.get("rows"), list):
        over = over["rows"]
    if not isinstance(over, list):
        return {"total": 0, "rows": [], "error": {"code": "bad_ref", "message": "over is not a list of rows"}}
    out = []
    for i, row in enumerate(over[:n]):
        items = []
        for c in children:
            vis_c = values.visible(c)
            res = values.resolver(vis_c, row=row, has_row=True)
            try:
                items.append({"cell_id": c.id, "name": c.name, "kind": c.kind, "inputs": res.resolve(c.spec or {})})
            except R.BadRef as e:
                items.append({"cell_id": c.id, "name": c.name, "kind": c.kind,
                              "error": {"code": "bad_ref", "message": e.message}})
        out.append({"i": i, "row": row, "cells": items})
    return {"total": len(over), "rows": out}
