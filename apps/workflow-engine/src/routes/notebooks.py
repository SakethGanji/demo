"""Notebook core routes (BUILD-PLAN §1.3): notebooks, cells, runs, apply/discard, rows.

Phase 0: every handler is a 501 stub with the final path, body and response model, so the studio and W2 can code
against it. Keep, ask, browser (frame/artifacts/control), report, trace, templates and capabilities are in
``notebook_keep.py``. Errors use ``{"detail": {"code", "message", ...}}`` (``schemas.notebook.notebook_error``).
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from ..core.dependencies import get_db_session
from ..db import notebook_models  # noqa: F401  (registers the notebook tables on SQLModel.metadata)
from ..schemas.notebook import (
    ApplyRequest,
    CellActionResponse,
    CellCreate,
    CellPatch,
    CellPatchResponse,
    DiscardRequest,
    ForEachPreview,
    Notebook,
    NotebookCreate,
    NotebookListItem,
    NotebookPatch,
    NotebookStatus,
    RowsPage,
    RetryRowsRequest,
    Run,
    RunCreate,
    notebook_error,
)
from ..schemas.notebook import Cell as CellOut

router = APIRouter()


def _todo(what: str):
    return notebook_error(501, "not_implemented", f"{what} is not implemented yet")


def _svc(session: AsyncSession):
    from ..services.notebook.service import NotebookService

    return NotebookService(session)


async def _action(session: AsyncSession, r, run) -> dict:
    from ..services.notebook.service import result_out, run_out

    await session.refresh(r)
    await session.refresh(run)
    return {"result": result_out(r, run.n), "run": run_out(run)}


# -- notebooks --------------------------------------------------------------------------------------------------------


@router.get("/projects/{project}/notebooks", response_model=list[NotebookListItem])
async def list_notebooks(
    project: str,
    status: NotebookStatus | None = Query(None),
    q: str | None = Query(None, max_length=200),
    session: AsyncSession = Depends(get_db_session),
):
    svc = _svc(session)
    p = await svc.require_project(project)
    return await svc.list(p, status, q)


@router.post("/projects/{project}/notebooks", response_model=Notebook, status_code=201)
async def create_notebook(project: str, body: NotebookCreate, session: AsyncSession = Depends(get_db_session)):
    svc = _svc(session)
    p = await svc.require_project(project)
    nb = await svc.create(p, body)
    return await svc.full(nb)


@router.get("/notebooks/{notebook_id}", response_model=Notebook)
async def get_notebook(notebook_id: str, session: AsyncSession = Depends(get_db_session)):
    svc = _svc(session)
    return await svc.full(await svc.require_notebook(notebook_id))


@router.patch("/notebooks/{notebook_id}", response_model=Notebook)
async def patch_notebook(notebook_id: str, body: NotebookPatch, session: AsyncSession = Depends(get_db_session)):
    svc = _svc(session)
    nb = await svc.patch(await svc.require_notebook(notebook_id), body)
    return await svc.full(nb)


@router.delete("/notebooks/{notebook_id}", status_code=204)
async def delete_notebook(notebook_id: str, session: AsyncSession = Depends(get_db_session)):
    svc = _svc(session)
    nb = await svc.require_notebook(notebook_id)
    await svc.reap(nb)
    if nb.active_run_id:
        raise notebook_error(409, "run_in_progress", "a run of this notebook is in progress", run_id=nb.active_run_id)
    await svc.delete(nb)


# -- cells ------------------------------------------------------------------------------------------------------------


@router.post("/notebooks/{notebook_id}/cells", response_model=CellOut, status_code=201)
async def create_cell(notebook_id: str, body: CellCreate, session: AsyncSession = Depends(get_db_session)):
    """422 ``invalid_spec {fields}`` / ``unknown_kind`` / ``not_select`` / ``connection_readonly`` / ``invalid_parent``."""
    svc = _svc(session)
    nb = await svc.require_notebook(notebook_id)
    c = await svc.add_cell(nb, body.kind, body.spec, title=body.title, guard=body.guard,
                           parent_cell_id=body.parent_cell_id, created_by=body.created_by)
    return await svc.cell_view(c)


@router.patch("/notebook-cells/{cell_id}", response_model=CellPatchResponse)
async def patch_cell(cell_id: str, body: CellPatch, session: AsyncSession = Depends(get_db_session)):
    svc = _svc(session)
    c, stale_from = await svc.patch_cell(await svc.require_cell(cell_id), body)
    return {"cell": await svc.cell_view(c), "stale_from": stale_from}


@router.delete("/notebook-cells/{cell_id}", status_code=204)
async def delete_cell(cell_id: str, session: AsyncSession = Depends(get_db_session)):
    """409 ``cell_applied`` when the cell has an applied write."""
    svc = _svc(session)
    await svc.delete_cell(await svc.require_cell(cell_id))


# -- runs -------------------------------------------------------------------------------------------------------------


@router.post("/notebooks/{notebook_id}/runs", response_model=Run, status_code=202)
async def start_run(notebook_id: str, body: RunCreate, session: AsyncSession = Depends(get_db_session)):
    """409 ``run_in_progress {run_id}`` when the notebook's lock is held."""
    from ..services.notebook import runner
    from ..services.notebook.service import run_out

    svc = _svc(session)
    nb = await svc.require_notebook(notebook_id)
    run = await runner.start_run(session, nb, body)
    return {**run_out(run), "results": []}


@router.get("/notebook-runs/{run_id}", response_model=Run)
async def get_run(run_id: str, session: AsyncSession = Depends(get_db_session)):
    svc = _svc(session)
    return await svc.run_view(await svc.require_run(run_id))


@router.post("/notebook-runs/{run_id}/cancel", response_model=Run, status_code=202)
async def cancel_run(run_id: str, session: AsyncSession = Depends(get_db_session)):
    from ..services.notebook import runner

    svc = _svc(session)
    run = await runner.cancel_run(session, await svc.require_run(run_id))
    return await svc.run_view(run)


# -- pending cells: change ----------------------------------------------------------------------------------------------


@router.post("/notebook-cells/{cell_id}/apply", response_model=CellActionResponse)
async def apply_cell(cell_id: str, body: ApplyRequest, session: AsyncSession = Depends(get_db_session)):
    """Same ``idempotency_key`` returns the same write. 409 ``not_pending``; 422 ``connection_readonly``."""
    from ..services.notebook import runner

    r, run = await runner.apply(session, await _svc(session).require_cell(cell_id), body)
    return await _action(session, r, run)


@router.post("/notebook-cells/{cell_id}/discard", response_model=CellActionResponse)
async def discard_cell(cell_id: str, body: DiscardRequest | None = None, session: AsyncSession = Depends(get_db_session)):
    from ..services.notebook import runner

    r, run = await runner.discard(session, await _svc(session).require_cell(cell_id), body.by if body else None)
    return await _action(session, r, run)


# -- for_each: retry failed rows, batch preview -----------------------------------------------------------------------


@router.post("/notebook-cells/{cell_id}/retry-rows", response_model=Run, status_code=202)
async def retry_rows(cell_id: str, body: RetryRowsRequest | None = None, session: AsyncSession = Depends(get_db_session)):
    """A new run of one for-each for some rows only (default: its failed rows). 409 ``run_in_progress`` / ``not_pending``."""
    from ..services.notebook import runner
    from ..services.notebook.service import run_out

    run = await runner.retry_rows(session, await _svc(session).require_cell(cell_id), body or RetryRowsRequest())
    return {**run_out(run), "results": []}


@router.get("/notebook-cells/{cell_id}/preview", response_model=ForEachPreview)
async def preview_rows(cell_id: str, n: int = Query(5, ge=1, le=50), session: AsyncSession = Depends(get_db_session)):
    """The first ``n`` rows of a for-each with each body cell's inputs resolved; nothing runs."""
    from ..services.notebook import runner

    return await runner.preview_rows(session, await _svc(session).require_cell(cell_id), n)


# -- rows -------------------------------------------------------------------------------------------------------------


@router.get("/notebook-cells/{cell_id}/rows", response_model=RowsPage)
async def cell_rows(
    cell_id: str,
    run: str | None = Query(None, description="run id; default = the cell's latest result"),
    row: int | None = Query(None, description="for_each body row index"),
    offset: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=500),
    session: AsyncSession = Depends(get_db_session),
):
    from sqlalchemy import select

    from ..services.notebook import storage

    M = notebook_models.NotebookCellResultModel
    await _svc(session).require_cell(cell_id)
    stmt = select(M).where(M.cell_id == cell_id)
    if run:
        stmt = stmt.where(M.run_id == run)
    stmt = stmt.where(M.row_index == row) if row is not None else stmt.where(M.row_index.is_(None))
    r = (await session.execute(stmt.order_by(M.created_at.desc()).limit(1))).scalars().first()
    if r is None:
        raise notebook_error(404, "run_not_found", "this cell has no result for that run/row")
    out = r.output or {}
    full = storage.read_rows(r.output_ref) if r.output_ref else None
    rows = (full or {}).get("rows") if full else out.get("rows")
    rows = rows if isinstance(rows, list) else []
    cols = (full or {}).get("columns") or out.get("columns") or storage.columns_of(rows)
    return {"columns": cols, "rows": rows[offset:offset + limit], "total": len(rows)}
