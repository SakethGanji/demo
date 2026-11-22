"""LLM labeling (labeling plan §6.2, §5.10; LB-B L08): prompts, preview, calibration, runs, unmasked requests."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Body, Request
from fastapi.concurrency import run_in_threadpool

from ..authz import path, require
from . import api_tasks as _tasks  # noqa: F401  (registers the LB-B scope kinds)
from . import lbb_common as C
from . import llm_label as LL

router = APIRouter(prefix="/api")
LL.register_job()


def _b(body: Any) -> dict[str, Any]:
    return _tasks._b(body)


@router.post("/label-layers/{layer_id}/prompts", status_code=201, dependencies=[require("label.llm_run", path("layer_id", "label_layer"))])
async def create_prompt(layer_id: str, request: Request, body: Any = Body(None)) -> dict[str, Any]:
    return await run_in_threadpool(LL.create_prompt, layer_id, _b(body), C.caller(request))


@router.get("/label-layers/{layer_id}/prompts", dependencies=[require("label.read", path("layer_id", "label_layer"))])
async def list_prompts(layer_id: str) -> dict[str, Any]:
    return {"prompts": await run_in_threadpool(LL.list_prompts, layer_id)}


@router.get("/label-prompts/{prompt_id}", dependencies=[require("label.read", path("prompt_id", "label_prompt"))])
async def get_prompt(prompt_id: str) -> dict[str, Any]:
    def go():
        with LL._ensure().connect() as con:
            return LL.get_prompt(con, prompt_id)
    return await run_in_threadpool(go)


@router.post("/label-prompts/{prompt_id}/preview", dependencies=[require("label.llm_run", path("prompt_id", "label_prompt"))])
async def preview(prompt_id: str, request: Request, body: Any = Body(None)) -> dict[str, Any]:
    return await run_in_threadpool(LL.preview, prompt_id, _b(body), C.caller(request))


@router.post("/label-prompts/{prompt_id}/calibrate", status_code=202, dependencies=[require("label.llm_run", path("prompt_id", "label_prompt"))])
async def calibrate(prompt_id: str, request: Request, body: Any = Body(None)) -> dict[str, Any]:
    return await run_in_threadpool(LL.start_run, prompt_id, "calibrate", _b(body), C.caller(request), request.app.state.db)


@router.post("/label-prompts/{prompt_id}/runs", status_code=202, dependencies=[require("label.llm_run", path("prompt_id", "label_prompt"))])
async def start_run(prompt_id: str, request: Request, body: Any = Body(None)) -> dict[str, Any]:
    return await run_in_threadpool(LL.start_run, prompt_id, "label", _b(body), C.caller(request), request.app.state.db)


@router.get("/label-llm-runs/{run_id}", dependencies=[require("label.read", path("run_id", "label_llm_run"))])
async def get_run(run_id: str) -> dict[str, Any]:
    return await run_in_threadpool(LL.get_run, run_id)


@router.post("/label-layers/{layer_id}/llm-unmasked-requests", status_code=201,
             dependencies=[require("label.llm_unmasked", path("layer_id", "label_layer"))])
async def request_unmasked(layer_id: str, request: Request, body: Any = Body(None)) -> dict[str, Any]:
    return await run_in_threadpool(LL.request_unmasked, layer_id, _b(body), C.caller(request))


@router.post("/label-llm-unmasked-requests/{request_id}/decide",
             dependencies=[require("label.llm_unmasked_decide", path("request_id", "label_unmask_request"))])
async def decide_unmasked(request_id: str, request: Request, body: Any = Body(None)) -> dict[str, Any]:
    b = _b(body)
    return await run_in_threadpool(LL.decide_unmasked, request_id, bool(b.get("approve")), str(b.get("comment") or ""), C.caller(request))


@router.get("/label-layers/{layer_id}/llm-policy", dependencies=[require("label.read", path("layer_id", "label_layer"))])
async def llm_policy(layer_id: str) -> dict[str, Any]:
    return await run_in_threadpool(LL.policy_view, layer_id)


@router.get("/label-layers/{layer_id}/llm-runs", dependencies=[require("label.read", path("layer_id", "label_layer"))])
async def llm_runs(layer_id: str) -> dict[str, Any]:
    return {"runs": await run_in_threadpool(LL.list_runs, layer_id)}


@router.post("/label-llm-runs/{run_id}/cancel", dependencies=[require("label.llm_run", path("run_id", "label_llm_run"))])
async def cancel_run(run_id: str, request: Request) -> dict[str, Any]:
    return await run_in_threadpool(LL.cancel_run, run_id, C.caller(request), request.app.state.db)


@router.post("/label-llm-runs/{run_id}/accept", dependencies=[require("label.llm_run", path("run_id", "label_llm_run"))])
async def accept_run(run_id: str) -> dict[str, Any]:
    return await run_in_threadpool(LL.accept_view, run_id)


@router.post("/label-llm-runs/{run_id}/send-to-task", dependencies=[require("label.task_manage", path("run_id", "label_llm_run"))])
async def send_to_task(run_id: str, request: Request, body: Any = Body(None)) -> dict[str, Any]:
    """The run's pre-labels below τ (default: everything not auto-accepted) become a `llm_low_conf` fill of the task:
    people confirm (Enter) or change them."""
    from . import tasks as T

    b = _b(body)
    r = await run_in_threadpool(LL.get_run, run_id)
    sel = {"strategy": "llm_low_conf", "n": int(b.get("n") or 500), "version_id": (r["selector"] or {}).get("version_id")}
    if b.get("tau") is not None:
        sel["below"] = b["tau"]
    out = await run_in_threadpool(T.fill, b.get("task_id"), sel, C.caller(request))
    return {**out, "added": out.get("rows")}


@router.post("/label-layers/{layer_id}/llm/preview", dependencies=[require("label.llm_run", path("layer_id", "label_layer"))])
async def llm_query_preview(layer_id: str, request: Request, body: Any = Body(None)) -> dict[str, Any]:
    """Quick test before saving: {version_id, query, prompt_template, limit ≤ 20, call_llm?, destination?, model?} →
    the query rows, the rendered prompts and (call_llm: true) the LLM's answers for those rows. Bad SQL → 400 with
    DuckDB's message; timeout 408; row cap 20."""
    return await run_in_threadpool(LL.query_preview, layer_id, _b(body), C.caller(request))


@router.post("/label-layers/{layer_id}/sql/preview", dependencies=[require("label.llm_run", path("layer_id", "label_layer"))])
async def sql_preview(layer_id: str, request: Request, body: Any = Body(None)) -> dict[str, Any]:
    """The query editor's "run": {version_id, query, limit ≤ 200} over the version's tables (views by name), read-only."""
    from . import llm_sql

    b = _b(body)
    by = C.caller(request)

    def go():
        with C.db().connect() as con:
            lay = C.layer(con, layer_id)
            C.check_version_of_layer(con, lay, b.get("version_id") or "")
        res = llm_sql.run(b["version_id"], b.get("query") or "", limit=max(1, min(int(b.get("limit") or 50), llm_sql.PREVIEW_CAP)))
        with C.db().connect() as con:
            C.audit(con, by, "label_sql_preview", {"layer_id": layer_id, "version_id": b["version_id"], "query_sha256": res["query_sha256"],
                                                   "rows": res["n"], "columns": res["columns"]}, object_kind="label_layer", object_id=layer_id)
        return res
    return await run_in_threadpool(go)
