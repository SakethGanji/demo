"""Vela routes: findings computed from a run, each with one patch for the Gym, and their optional LLM wording."""

from __future__ import annotations

import hashlib
import json
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from fastapi.concurrency import run_in_threadpool

from ..gym.api import run_body

router = APIRouter(prefix="/api")


def _findings_job(rec, base, events, ds, batches):
    from .findings import compute

    return compute(rec, base, events, ds, batches)


def _batches_for(request: Request, run_id: str) -> list[dict[str, Any]]:
    reg = request.app.state.registry
    out = []
    for v in reg.list_versions():
        if v.get("run_id") != run_id:
            continue
        for b in reg.list_batches(v["id"]):
            res = b.get("result") or {}
            if b.get("status") in ("done", "succeeded", "ready") or res.get("drift"):
                out.append({"id": b["id"], "version": f"{v['model_name']} v{v['version']}", "n_rows": b.get("n_rows"),
                            "drift": res.get("drift")})
    return out


def _key(findings: list[dict[str, Any]]) -> str:
    return hashlib.sha256(json.dumps([(f["id"], f.get("computed_text")) for f in findings], ensure_ascii=False).encode()).hexdigest()[:16]


async def compute_findings(request: Request, run_id: str) -> tuple[dict[str, Any], dict[str, Any]]:
    store = request.app.state.runs
    rec = store.get(run_id)
    if not rec:
        raise HTTPException(404, f"No run with id '{run_id}'.")
    if rec["status"] != "succeeded":
        raise HTTPException(409, f"Run {run_id} is {rec['status']}; Vela reads finished runs.")
    events = await run_in_threadpool(store.events, run_id, 0, 100_000)
    events = [e for e in events if e["type"] in ("model_failed", "log", "post_fit")]
    ds = request.app.state.db.get_dataset(rec["dataset_id"])
    batches = await run_in_threadpool(_batches_for, request, run_id)
    out = await request.app.state.heavy.run(_findings_job, rec, run_body(rec), events, ds, batches)
    return rec, out


@router.get("/runs/{run_id}/vela")
async def vela_findings(run_id: str, request: Request) -> dict[str, Any]:
    """Findings computed from the run (numbers, the formula behind each, one patch each) and the cached wording,
    if `POST …/vela/wording` produced one for these exact findings."""
    rec, out = await compute_findings(request, run_id)
    key = _key(out["findings"])
    cached = ((rec.get("result") or {}).get("vela_wording") or {})
    wording = cached if cached.get("key") == key else None
    if wording:
        for f in out["findings"]:
            f["text"] = wording["texts"].get(f["id"], f["text"])
    return {"run_id": run_id, "run_name": rec["name"], "findings": out["findings"], "errors": out["errors"],
            "base": run_body(rec), "wording": wording, "key": key}


@router.post("/runs/{run_id}/vela/wording")
async def vela_wording(run_id: str, request: Request) -> dict[str, Any]:
    """Word the findings with the claude CLI, else the Anthropic API, else keep the computed text (30 s each)."""
    from .wording import word

    rec, out = await compute_findings(request, run_id)
    res = rec.get("result") or {}
    summary = {"name": rec["name"], "problem_type": res.get("problem_type"), "metric": (res.get("eval_metric") or {}).get("name"),
               "best_model": res.get("best_model")}
    w = await run_in_threadpool(word, out["findings"], summary)
    w["key"] = _key(out["findings"])
    request.app.state.runs.merge_result(run_id, vela_wording=w)
    return w
