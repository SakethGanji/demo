"""Notebook integration routes (BUILD-PLAN §1.3, owner W2): capabilities, templates, ask, browser cell
frame/artifacts/control, report, trace, Keep (preview/commit), checks and the .spec.ts export.

Registered *before* ``notebooks.py`` so the literal ``/notebooks/capabilities`` never matches
``/notebooks/{notebook_id}``. Keep is deterministic (no LLM); only ``/ask`` calls a model.
"""

from __future__ import annotations

import logging

import asyncio
import base64
import json
import re
import shutil
import time
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from urllib.parse import urljoin

from fastapi import APIRouter, Depends, Query, Response
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from ..core.dependencies import get_db_session
from ..db import notebook_models  # noqa: F401  (registers the notebook tables)
from ..db.notebook_models import NotebookCellModel, NotebookCellResultModel, NotebookModel, NotebookRunModel
from ..schemas.notebook import (
    AskRequest,
    AskResponse,
    Capabilities,
    ChecksResponse,
    CellInputRequest,
    ControlRequest,
    ImportHttpRequest,
    KeepPreview,
    NotebookEnvironments,
    NotebookTemplate,
    ReportResponse,
    TraceEntry,
    notebook_error,
)
from ..services.notebook.keep import KeepRequestX, KeepResponseX
from ..utils.ids import generate_id

logger = logging.getLogger(__name__)
router = APIRouter()

SPEC_SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "notebook_spec.mts"
SPEC_TIMEOUT_S = 20.0


def _svc(session: AsyncSession):
    from ..services.notebook.service import NotebookService

    return NotebookService(session)


async def _latest(svc: Any, cells: list[NotebookCellModel]) -> dict[str, Any]:
    """``{cell_id: result-like}`` with ``status, output, output_ref, error, job_id, _run_n``."""
    got = await svc.latest_results([c.id for c in cells])
    return {cid: SimpleNamespace(status=r.status, output=r.output, output_ref=r.output_ref, error=r.error,
                                 job_id=r.job_id, execution_id=r.execution_id, _run_n=run.n)
            for cid, (r, run) in got.items()}


# -- capabilities / templates -----------------------------------------------------------------------------------------


@router.get("/notebooks/capabilities", response_model=Capabilities)
async def capabilities(project: str | None = Query(None), session: AsyncSession = Depends(get_db_session)):
    from ..services.notebook.capabilities import capabilities as caps

    project_id = None
    if project:
        project_id = (await _svc(session).require_project(project)).id
    return await caps(session, project_id)


@router.get("/notebook-templates", response_model=list[NotebookTemplate])
async def list_templates():
    from ..services.notebook import templates

    return templates.list_templates()


# -- ask --------------------------------------------------------------------------------------------------------------


async def _ask_sets(session: AsyncSession, nb: NotebookModel) -> tuple[set[str], set[str], set[str]]:
    from ..db.connection_models import ProjectConnectionModel as C
    from ..repositories.workflow_repository import WorkflowRepository

    conns = (await session.execute(select(C).where(C.project_id == nb.project_id))).scalars().all()
    writable = {c.key for c in conns if not c.readonly} | {c.id for c in conns if not c.readonly}
    readonly = {c.key for c in conns if c.readonly} | {c.id for c in conns if c.readonly}
    wfs = await WorkflowRepository(session).list(project_id=nb.project_id)
    return writable, readonly, {w.id for w in wfs}


async def _record_ask(session: AsyncSession, nb_id: str, cell_id: str, by: str | None,
                      status: str, output: dict[str, Any] | None, error: dict[str, Any] | None, ms: int) -> None:
    """The Ask's result as a one-cell ``done`` run (``via_agent="ask"``). Never takes the run lock."""
    # ids, not models: after a commit or rollback their attributes are expired (and can't lazy-load here)
    last = ""
    position = (await session.execute(select(NotebookCellModel.position).where(NotebookCellModel.id == cell_id))).scalar()
    for _ in range(5):
        n = ((await session.execute(select(func.max(NotebookRunModel.n)).where(NotebookRunModel.notebook_id == nb_id)))
             .scalar() or 0) + 1
        now = datetime.now()
        run_id = generate_id("nbr")
        try:
            session.add(NotebookRunModel(id=run_id, notebook_id=nb_id, n=n, from_position=position,
                                         status="done", triggered_by=by, via_agent="ask", heartbeat_at=now,
                                         started_at=now, finished_at=now))
            await session.flush()  # the run row first: the models declare no relationship to order the inserts
            session.add(NotebookCellResultModel(id=generate_id("nbres"), run_id=run_id, cell_id=cell_id, status=status,
                                                output=output, error=error, ms=ms, created_at=now))
            await session.commit()
            return
        except IntegrityError as e:  # another run took this n: take the next one
            await session.rollback()
            last = str(e.orig)[:300]
    logger.warning("could not record the Ask result for %s: %s", cell_id, last)
    raise notebook_error(409, "run_in_progress", "could not record the Ask result; try again")


@router.post("/notebooks/{notebook_id}/ask", response_model=AskResponse)
async def ask(notebook_id: str, body: AskRequest, session: AsyncSession = Depends(get_db_session)):
    """Always 200 when the notebook exists; LLM failure gives ``ask_cell`` error ``llm_unavailable`` and ``planned=[]``."""
    from ..services.notebook import capabilities as caps
    from ..services.notebook.executors import ask as A

    svc = _svc(session)
    nb = await svc.require_notebook(notebook_id)
    if body.selection is not None:
        names = {c.name for c in await svc.cells(nb.id)}
        if body.selection.cell not in names:
            raise notebook_error(422, "invalid", f"the selection names {body.selection.cell}, which is not a cell here")
    t0 = time.monotonic()
    cell = await svc.add_cell(nb, "ask", {"text": body.text}, title=body.text.strip()[:120] or None,
                              created_by=body.by, created_via="user")
    cell_id = cell.id
    cells = [c for c in await svc.cells(nb.id) if c.id != cell_id]
    latest = await _latest(svc, cells)
    nums = [int(c.name[1:]) for c in cells + [cell] if c.name.startswith("c") and c.name[1:].isdigit()]
    planned_ids: list[str] = []
    try:
        context = await A.build_context(session, nb, cells, latest)
        if body.selection is not None:
            context += "\n" + A.selection_line(body.selection)
        writable, readonly, wf_ids = await _ask_sets(session, nb)
        res = await A.plan(context, body.text, existing_names=[c.name for c in cells + [cell]],
                           next_index=max(nums, default=0) + 1, writable=writable, readonly=readonly,
                           workflow_ids=wf_ids)
    except A.AskFailed as e:
        if e.code == "llm_unavailable":
            caps.note_ai_failure(e.message)
        await _record_ask(session, notebook_id, cell_id, body.by, "error", None, {"code": e.code, "message": e.message},
                          int((time.monotonic() - t0) * 1000))
        return {"ask_cell": await svc.cell_view(await svc.require_cell(cell_id)), "planned": []}
    caps.note_ai_success()
    nb = await svc.require_notebook(notebook_id)
    by = body.by or "ask"
    names: list[str] = []
    for pc in res["cells"]:
        top = await svc.add_cell(nb, pc["kind"], pc["spec"], title=pc.get("title"), guard=pc.get("guard"),
                                 created_by=by, created_via="agent", commit=False)
        planned_ids.append(top.id)
        names.append(top.name)
        for ch in pc.get("children") or []:
            x = await svc.add_cell(nb, ch["kind"], ch["spec"], title=ch.get("title"), guard=ch.get("guard"),
                                   parent_cell_id=top.id, created_by=by, created_via="agent", commit=False)
            names.append(x.name)
    await session.commit()
    ms = int((time.monotonic() - t0) * 1000)
    await _record_ask(session, notebook_id, cell_id, body.by, "success",
                      {"summary": res["summary"], "planned": names, "calls": res["calls"], "llm_ms": res["ms"],
                       "rejected": res["rejected"][:20]}, None, ms)
    full = await svc.full(await svc.require_notebook(notebook_id))
    by_id = {c["id"]: c for c in full["cells"]}
    return {"ask_cell": by_id[cell_id], "planned": [by_id[i] for i in planned_ids if i in by_id]}


# -- browser cell -----------------------------------------------------------------------------------------------------


async def _browser_cell(session: AsyncSession, cell_id: str) -> tuple[NotebookCellModel, str | None]:
    """The browser cell and its job: the running one, else the latest result's."""
    from ..services.notebook.executors import browser

    svc = _svc(session)
    c = await svc.require_cell(cell_id)
    if c.kind != "browser":
        raise notebook_error(422, "invalid", f"{c.name} is a {c.kind} cell, not a browser cell")
    job = browser.LIVE.get(c.id)
    if job is None:
        got = await svc.latest_results([c.id])
        if c.id in got:
            job = got[c.id][0].job_id
    return c, job


def _jobs_error(e: Exception) -> Exception:
    code = getattr(e, "code", None)
    if code in ("jobs_unavailable", "jobs_not_configured", "jobs_timeout", "jobs_auth_failed"):
        return notebook_error(503, "browser_unavailable", f"the browser runner is unavailable: {e}")
    if code == "job_not_found":
        return notebook_error(404, "cell_not_found", "the browser job or artifact is gone")
    if code == "jobs_rejected":
        # the runner refused a control or input (not taken over, nothing live, a bad key): say why
        up = (getattr(e, "extra", None) or {}).get("upstream_status")
        msg = getattr(e, "message", None) or str(e)
        return notebook_error(409, "not_pending", msg) if up == 409 else notebook_error(422, "invalid", msg)
    return notebook_error(502, "browser_unavailable", f"the browser runner failed: {e}")


async def _jobs(params: dict[str, Any]) -> dict[str, Any]:
    from ..broker import jobs
    from ..broker.policy import PolicyError

    try:
        return (await jobs.call(params))["items"][0]
    except PolicyError as e:
        raise _jobs_error(e) from None


@router.get("/notebook-cells/{cell_id}/frame", responses={200: {"content": {"image/jpeg": {}}}, 204: {}})
async def cell_frame(cell_id: str, session: AsyncSession = Depends(get_db_session)):
    """The live screenshot while the recipe runs (image/jpeg); 204 when there is no job or no frame yet."""
    _, job = await _browser_cell(session, cell_id)
    if not job:
        return Response(status_code=204)
    try:
        got = await _jobs({"operation": "frame", "job_id": job})
    except Exception as e:  # noqa: BLE001 - a frame is best effort: no runner / no job = no picture
        if getattr(e, "status_code", None) in (404, 502, 503):
            return Response(status_code=204)
        raise
    f = got.get("frame")
    if not f or not f.get("base64"):
        return Response(status_code=204)
    return Response(content=base64.b64decode(f["base64"]), media_type=f.get("content_type") or "image/jpeg",
                    headers={"Cache-Control": "no-store"})


@router.get("/notebook-cells/{cell_id}/artifacts")
async def cell_artifacts(cell_id: str, session: AsyncSession = Depends(get_db_session)):
    """-> ``[{name, size, content_type}]`` (trace.zip, *.png, *.webm)."""
    _, job = await _browser_cell(session, cell_id)
    if not job:
        return []
    got = await _jobs({"operation": "artifacts", "job_id": job})
    return [{"name": a.get("name"), "size": a.get("bytes", a.get("size")), "content_type": a.get("content_type")}
            for a in got.get("artifacts") or [] if isinstance(a, dict) and a.get("name")]


@router.get("/notebook-cells/{cell_id}/artifacts/{name}")
async def cell_artifact(cell_id: str, name: str, session: AsyncSession = Depends(get_db_session)):
    _, job = await _browser_cell(session, cell_id)
    if not job:
        raise notebook_error(404, "cell_not_found", "this browser cell has not run")
    if not re.match(r"^[A-Za-z0-9][A-Za-z0-9_.@ -]{0,199}$", name):
        raise notebook_error(422, "invalid", "bad artifact name")
    got = await _jobs({"operation": "artifact", "job_id": job, "name": name})
    if got.get("too_large"):
        raise notebook_error(422, "invalid", f"{name} is larger than the engine relays "
                                             f"({got.get('max_bytes', 0) // (1024 * 1024)} MB)")
    safe = re.sub(r"[^A-Za-z0-9_.-]", "_", name)
    return Response(content=base64.b64decode(got.get("base64") or ""),
                    media_type=got.get("content_type") or "application/octet-stream",
                    headers={"Content-Disposition": f'attachment; filename="{safe}"'})


@router.post("/notebook-cells/{cell_id}/control")
async def cell_control(cell_id: str, body: ControlRequest, session: AsyncSession = Depends(get_db_session)):
    """pause / resume / step the running recipe; stop cancels it; takeover pauses it so the user can drive the live
    page (``/input``), handback resumes the scripted steps."""
    from ..services.notebook.executors import browser

    c, _ = await _browser_cell(session, cell_id)
    job = browser.LIVE.get(c.id)
    if not job:
        raise notebook_error(409, "not_pending", f"{c.name} is not running")
    if body.action == "stop":
        await _jobs({"operation": "cancel", "job_id": job})
    else:
        await _jobs({"operation": "control", "job_id": job, "action": body.action})
    return {"ok": True, "action": body.action, "job_id": job}


@router.post("/notebook-cells/{cell_id}/input")
async def cell_input(cell_id: str, body: CellInputRequest, session: AsyncSession = Depends(get_db_session)):
    """Take over: relay one click (x, y as fractions of the page), typed text, key or scroll to the live page. The
    runner refuses it (409 not_pending) unless the cell was taken over. Typed text is never logged."""
    from ..services.notebook.executors import browser

    c, _ = await _browser_cell(session, cell_id)
    job = browser.LIVE.get(c.id)
    if not job:
        raise notebook_error(409, "not_pending", f"{c.name} is not running")
    await _jobs({"operation": "input", "job_id": job, "input": body.model_dump(exclude_none=True)})
    return {"ok": True, "type": body.type, "job_id": job}


# -- report / trace ---------------------------------------------------------------------------------------------------


@router.get("/notebooks/{notebook_id}/report", response_model=ReportResponse)
async def report(notebook_id: str, session: AsyncSession = Depends(get_db_session)):
    from ..services.notebook import report as rep

    svc = _svc(session)
    nb = await svc.require_notebook(notebook_id)
    cells = await svc.cells(nb.id)
    latest = await _latest(svc, cells)
    run_n = max((x._run_n for x in latest.values()), default=None)
    return {"markdown": rep.render(nb.title, rep.cells_for_report(cells, latest), run_n=run_n)}


def _result_text(name: str, r: NotebookCellResultModel) -> str:
    bits = [f"{name} {r.status}"]
    if r.row_index is not None:
        bits[0] = f"{name}[{r.row_index}] {r.status}"
    out, err = r.output or {}, r.error or {}
    if err:
        bits.append(f"{err.get('code')}: {str(err.get('message'))[:200]}")
    elif isinstance(out, dict):
        if out.get("summary"):
            bits.append(str(out["summary"])[:200])
        elif "row_count" in out:
            bits.append(f"{out['row_count']} rows")
        elif "count" in out:
            bits.append(f"count {out['count']}")
    return " · ".join(bits)


@router.get("/notebooks/{notebook_id}/trace", response_model=list[TraceEntry])
async def trace(notebook_id: str, limit_runs: int = Query(20, ge=1, le=100),
                session: AsyncSession = Depends(get_db_session)):
    """What happened, oldest first: each run's start/stop and every cell result (with execution / job ids)."""
    svc = _svc(session)
    nb = await svc.require_notebook(notebook_id)
    names = {c.id: c.name for c in await svc.cells(nb.id)}
    runs = list((await session.execute(select(NotebookRunModel).where(NotebookRunModel.notebook_id == nb.id)
                                       .order_by(NotebookRunModel.n.desc()).limit(limit_runs))).scalars().all())
    by_id = {r.id: r for r in runs}
    results = (await session.execute(select(NotebookCellResultModel).where(
        NotebookCellResultModel.run_id.in_(list(by_id) or [""])))).scalars().all()
    out: list[dict[str, Any]] = []
    for r in runs:
        who = f" by {r.triggered_by}" if r.triggered_by else ""
        label = "Ask" if r.via_agent == "ask" else f"Run {r.n}"
        out.append({"ts": r.started_at, "run_n": r.n, "kind": "run_started", "text": f"{label} started{who}"})
        if r.finished_at:
            stop = f" ({r.stop.get('reason')})" if isinstance(r.stop, dict) and r.stop.get("reason") else ""
            out.append({"ts": r.finished_at, "run_n": r.n, "kind": "run_finished",
                        "text": f"{label} {r.status}{stop}",
                        "ms": int((r.finished_at - r.started_at).total_seconds() * 1000)})
    for x in results:
        run = by_id[x.run_id]
        out.append({"ts": x.created_at, "cell_id": x.cell_id, "run_n": run.n, "kind": "cell",
                    "text": _result_text(names.get(x.cell_id, x.cell_id), x), "ms": x.ms,
                    "execution_id": x.execution_id, "job_id": x.job_id})
    order = {"run_started": 0, "cell": 1, "run_finished": 2}
    out.sort(key=lambda e: (e["ts"], e.get("run_n") or 0, order[e["kind"]]))
    return out


# -- keep -------------------------------------------------------------------------------------------------------------


async def _keep_plan(session: AsyncSession, notebook_id: str, body: KeepRequestX):
    from ..services.notebook import keep as K

    svc = _svc(session)
    nb = await svc.require_notebook(notebook_id)
    cells = await svc.cells(nb.id)
    latest = await _latest(svc, cells)
    return nb, await K.build(session, nb, cells, body, latest)


@router.post("/notebooks/{notebook_id}/keep/preview", response_model=KeepPreview)
async def keep_preview(notebook_id: str, body: KeepRequestX, session: AsyncSession = Depends(get_db_session)):
    """The strip items and the workflow definition Keep would save. Writes nothing."""
    from ..services.notebook import keep as K

    _, plan = await _keep_plan(session, notebook_id, body)
    return K.preview_out(plan)


@router.post("/notebooks/{notebook_id}/keep", response_model=KeepResponseX, status_code=201)
async def keep(notebook_id: str, body: KeepRequestX, session: AsyncSession = Depends(get_db_session)):
    """409 ``unbound_credential {items}`` when it would be scheduled with an unbound slot; 422 ``invalid``."""
    from ..services.notebook import keep as K

    nb, plan = await _keep_plan(session, notebook_id, body)
    return await K.commit(session, nb, plan, body)


@router.get("/notebooks/{notebook_id}/checks", response_model=ChecksResponse)
async def checks(notebook_id: str, session: AsyncSession = Depends(get_db_session)):
    """Recent executions of the workflow this notebook was most recently kept as."""
    from ..db.models import ExecutionModel as E

    nb = await _svc(session).require_notebook(notebook_id)
    kept = [k for k in (nb.kept or []) if isinstance(k, dict) and k.get("workflow_id")]
    if not kept:
        return {"workflow_id": None, "runs": [], "pass": 0, "fail": 0}
    wid = kept[-1]["workflow_id"]
    rows = (await session.execute(select(E).where(E.workflow_id == wid, E.parent_execution_id.is_(None))
                                  .order_by(E.start_time.desc()).limit(50))).scalars().all()
    runs = [{"execution_id": e.id, "status": e.status, "start_time": e.start_time,
             "ms": int((e.end_time - e.start_time).total_seconds() * 1000) if e.end_time and e.start_time else None}
            for e in rows]
    return {"workflow_id": wid, "runs": runs, "pass": sum(1 for e in rows if e.status == "success"),
            "fail": sum(1 for e in rows if e.status in ("failed", "error"))}


def _spec_input(nb: NotebookModel, c: NotebookCellModel) -> dict[str, Any]:
    from ..services.notebook.executors.browser import _cred_placeholders

    spec = c.spec or {}
    base = str(spec.get("base_url") or "")
    steps = []
    for s in spec.get("steps") or []:
        s = _cred_placeholders(dict(s))
        if s.get("action") == "goto" and isinstance(s.get("url"), str) and base:
            s["url"] = urljoin(base.rstrip("/") + "/", s["url"].lstrip("/")) if not re.match(r"^\w+://", s["url"]) \
                else s["url"]
        steps.append(s)
    keys = sorted(set(re.findall(r"\{\{(?:totp:)?(\w+)\}\}", json.dumps(steps))))
    return {"name": c.name, "title": c.title or f"{c.name} browser recipe", "base_url": base, "steps": steps,
            "credential_keys": keys, "notebook": nb.title}


@router.get("/notebooks/{notebook_id}/export.spec.ts", responses={200: {"content": {"text/plain": {}}}})
async def export_spec(notebook_id: str, session: AsyncSession = Depends(get_db_session)):
    """The notebook's browser cells as one Playwright test file (test-builder's codegen). 501
    ``spec_export_unavailable`` when node is missing."""
    svc = _svc(session)
    nb = await svc.require_notebook(notebook_id)
    cells = [c for c in await svc.cells(nb.id) if c.kind == "browser"]
    if not cells:
        raise notebook_error(422, "invalid", "this notebook has no browser cells to export")
    node = shutil.which("node")
    if node is None or not SPEC_SCRIPT.exists():
        raise notebook_error(501, "spec_export_unavailable", "exporting a .spec.ts needs node on the engine host")
    payload = json.dumps({"title": nb.title, "generated_at": datetime.now().isoformat(timespec="seconds"),
                          "cells": [_spec_input(nb, c) for c in cells]}).encode()
    proc = await asyncio.create_subprocess_exec(node, str(SPEC_SCRIPT), stdin=asyncio.subprocess.PIPE,
                                                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
    try:
        out, err = await asyncio.wait_for(proc.communicate(payload), timeout=SPEC_TIMEOUT_S)
    except asyncio.TimeoutError:
        proc.kill()
        raise notebook_error(501, "spec_export_unavailable", "the spec export timed out") from None
    if proc.returncode != 0:
        raise notebook_error(501, "spec_export_unavailable",
                             f"the spec export failed: {err.decode('utf-8', 'replace')[-400:]}")
    fname = re.sub(r"[^A-Za-z0-9_-]+", "-", nb.title).strip("-")[:60] or "notebook"
    return Response(content=out, media_type="text/plain; charset=utf-8",
                    headers={"Content-Disposition": f'attachment; filename="{fname}.spec.ts"'})


# -- API testing: environments and import ---------------------------------------------------------------------------


@router.get("/notebooks/{notebook_id}/environments", response_model=NotebookEnvironments,
            response_model_exclude_none=True)
async def get_environments(notebook_id: str, session: AsyncSession = Depends(get_db_session)):
    """Named variable sets (``dev``, ``staging``…) and the active one. A secret entry shows only its key."""
    from ..db.notebook_models import NotebookEnvironmentModel

    nb = await _svc(session).require_notebook(notebook_id)
    row = await session.get(NotebookEnvironmentModel, nb.id)
    return {"active": row.active if row else None, "sets": (row.sets if row else []) or []}


@router.put("/notebooks/{notebook_id}/environments", response_model=NotebookEnvironments,
            response_model_exclude_none=True)
async def put_environments(notebook_id: str, body: NotebookEnvironments, session: AsyncSession = Depends(get_db_session)):
    from ..db.notebook_models import NotebookEnvironmentModel

    nb = await _svc(session).require_notebook(notebook_id)
    data = body.model_dump(mode="json")
    sets = [{"name": s["name"], "vars": [{k: v[k] for k in ("name", "value", "secret") if v.get(k) is not None}
                                         for v in s["vars"]]} for s in data["sets"]]
    row = await session.get(NotebookEnvironmentModel, nb.id)
    if row is None:
        row = NotebookEnvironmentModel(notebook_id=nb.id)
        session.add(row)
    row.active, row.sets, row.updated_at = data["active"], sets, datetime.now()
    await session.commit()
    return {"active": row.active, "sets": sets}


def _curl_to_spec(text: str) -> tuple[dict[str, Any], list[str]]:
    """A pasted cURL command -> an http spec. A credential in it (``-u user:pass``, ``Authorization``, a key-like
    header) is never kept: it is dropped with a warning so the user picks a secret instead."""
    import shlex
    from urllib.parse import parse_qsl, urlsplit, urlunsplit

    try:
        argv = shlex.split(text.replace("\\\n", " ").replace("\\\r\n", " "))
    except ValueError as e:
        raise notebook_error(422, "invalid", f"can't read that cURL command: {e}") from None
    if not argv or argv[0] != "curl":
        raise notebook_error(422, "invalid", "paste a command that starts with curl")
    method, url, headers, data, warnings, as_query = None, None, {}, [], [], False
    i = 1
    flags_with_value = {"-X", "--request", "-H", "--header", "-d", "--data", "--data-raw", "--data-binary",
                        "--data-urlencode", "--json", "-u", "--user", "--url", "-A", "--user-agent", "-b", "--cookie",
                        "-e", "--referer", "-o", "--output", "-m", "--max-time", "--connect-timeout"}
    while i < len(argv):
        a = argv[i]
        v = argv[i + 1] if a in flags_with_value and i + 1 < len(argv) else None
        if a in ("-X", "--request"):
            method = (v or "").upper()
        elif a in ("-H", "--header"):
            name, _, val = (v or "").partition(":")
            name, val = name.strip(), val.strip()
            if name.lower() in ("authorization", "proxy-authorization", "cookie") or \
                    re.search(r"(api[-_]?key|token|secret)", name, re.I):
                warnings.append(f"the {name} header held a credential; it was left out, pick a secret under Auth")
            elif name:
                headers[name] = val
        elif a in ("-d", "--data", "--data-raw", "--data-binary", "--data-urlencode"):
            data.append(v or "")
        elif a == "--json":
            data.append(v or "")
            headers.setdefault("Content-Type", "application/json")
        elif a in ("-u", "--user"):
            warnings.append("the -u password was left out; pick a secret for Basic auth")
        elif a == "--url":
            url = v
        elif a in ("-G", "--get"):
            as_query = True
        elif a in ("-b", "--cookie"):
            warnings.append("cookies were left out")
        elif not a.startswith("-") and url is None:
            url = a
        if v is not None:
            i += 1
        i += 1
    if not url:
        raise notebook_error(422, "invalid", "the cURL command has no URL")
    parts = urlsplit(url)
    params = dict(parse_qsl(parts.query, keep_blank_values=True))
    for k in list(params):
        if re.search(r"(api[-_]?key|token|secret|password)", k, re.I):
            params.pop(k)
            warnings.append(f"the {k} query value held a credential; it was left out, pick a secret under Auth")
    spec: dict[str, Any] = {"method": method or ("POST" if data and not as_query else "GET"),
                            "url": urlunsplit((parts.scheme, parts.netloc, parts.path, "", "")),
                            "params": params, "headers": headers}
    joined = "&".join(data)
    if data and as_query:
        spec["params"].update(dict(parse_qsl(joined, keep_blank_values=True)))
    elif data:
        try:
            spec["body"] = json.loads(joined)
        except ValueError:
            spec["body"] = dict(parse_qsl(joined, keep_blank_values=True)) or joined
            spec["body_type"] = "form"
            if headers.get("Content-Type", "").startswith("application/x-www-form-urlencoded"):
                headers.pop("Content-Type")
    if spec["method"] not in ("GET", "HEAD", "POST", "PUT", "PATCH", "DELETE"):
        raise notebook_error(422, "invalid", f"method {spec['method']} is not supported")
    spec["assertions"] = [{"kind": "status", "op": "lt", "value": 400}]
    return spec, warnings


def _openapi_to_specs(detail: dict[str, Any], ids: list[str], spec_id: str) -> tuple[list[dict[str, Any]], list[str]]:
    ops = detail.get("operations") or []
    want = ids or [o.get("operation_id") for o in ops]
    out, warnings = [], []
    for oid in want:
        op = next((o for o in ops if o.get("operation_id") == oid), None)
        if op is None:
            warnings.append(f"{oid} is not an endpoint of this API")
            continue
        ok = sorted(int(c) for c in (op.get("responses") or {}) if str(c).isdigit() and 200 <= int(c) < 300)
        spec: dict[str, Any] = {"method": str(op.get("method") or "GET").upper(),
                                "assertions": [{"kind": "status", "op": "eq", "value": ok[0]} if ok else
                                               {"kind": "status", "op": "lt", "value": 400}]}
        if op.get("path_params"):
            path = re.sub(r"\{(\w+)\}", lambda m: "{{%s}}" % m.group(1), str(op.get("path") or ""))
            spec["url"] = str(detail.get("base_url") or "").rstrip("/") + path
            warnings.append(f"{oid}: set {', '.join(op['path_params'])} as variables (an environment, or extract "
                            "them from an earlier request)")
        else:
            spec["api_ref"] = f"{spec_id}:{oid}"
        if spec["method"] in ("POST", "PUT", "PATCH") and op.get("request_shape"):
            spec["body"] = {}
            warnings.append(f"{oid}: fill in the request body")
        out.append({"kind": "http", "title": (op.get("summary") or f"{spec['method']} {op.get('path')}")[:200],
                    "spec": spec})
    return out, warnings


@router.post("/notebooks/{notebook_id}/import-http")
async def import_http(notebook_id: str, body: ImportHttpRequest, session: AsyncSession = Depends(get_db_session)):
    """Turn a pasted cURL command, or endpoints of an API in the catalog, into http cells. ``add: false`` previews."""
    from ..db.models import ApiSpecModel
    from ..schemas.notebook import validate_spec
    from ..services.api_catalog import detail_of

    svc = _svc(session)
    nb = await svc.require_notebook(notebook_id)
    if body.curl is not None:
        spec, warnings = _curl_to_spec(body.curl)
        planned = [{"kind": "http", "title": f"{spec['method']} {spec['url']}"[:200], "spec": spec}]
    else:
        row = await session.get(ApiSpecModel, body.spec_id)
        if row is None or row.project_id != nb.project_id:
            raise notebook_error(404, "not_found", "that API is not in this project's catalog")
        planned, warnings = _openapi_to_specs(detail_of(row), body.operation_ids, row.id)
    for p in planned:
        p["spec"] = validate_spec("http", p["spec"])
    added = []
    if body.add:
        for p in planned:
            c = await svc.add_cell(nb, "http", p["spec"], title=p["title"], created_by=body.by, created_via="user",
                                   commit=False)
            added.append(c.id)
        await session.commit()
        full = await svc.full(await svc.require_notebook(notebook_id))
        by_id = {c["id"]: c for c in full["cells"]}
        return {"planned": planned, "warnings": warnings, "cells": [by_id[i] for i in added if i in by_id]}
    return {"planned": planned, "warnings": warnings, "cells": []}
