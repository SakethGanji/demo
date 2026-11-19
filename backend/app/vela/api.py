"""Vela routes: findings computed from a run, each with one patch for the Gym, and their optional LLM wording."""

from __future__ import annotations

import hashlib
import json
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from fastapi.concurrency import run_in_threadpool

from ..gym.api import run_body
from ..http import run_or_404

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
    rec = run_or_404(request, run_id)
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
    """Word the findings through the F5 router (Anthropic SDK, else the claude CLI, else the computed text); awaited on
    the event loop, cached by (prompt hash, facts hash)."""
    from .wording import word_async

    rec, out = await compute_findings(request, run_id)
    res = rec.get("result") or {}
    summary = {"name": rec["name"], "problem_type": res.get("problem_type"), "metric": (res.get("eval_metric") or {}).get("name"),
               "best_model": res.get("best_model")}
    w = await word_async(out["findings"], summary)
    w["key"] = _key(out["findings"])
    request.app.state.runs.merge_result(run_id, vela_wording=w)
    return w


# --------------------------------------------------------------------------- F17 · Model Doctor


def _measure_job(cfg, rec):
    from .doctor import measure

    return measure(cfg, rec)


def _twin_se_job(recs):
    from .doctor import twin_se

    return [twin_se(r) for r in recs]


def _doctor_summary(d: dict[str, Any]) -> dict[str, Any]:
    """The small summary on the run payload (`doctor`), for lists, Autopilot and Vela's layer 2."""
    def one(c):
        rx = c.get("prescription") or {}
        return {"id": c["id"], "title": c["title"], "severity": c["severity"], "reason": c["reason"],
                "prescription": {"kind": rx.get("kind"), "title": rx.get("title")} if rx else None}
    return {"schema": d["schema"], "computed_at": d["computed_at"], "latency_budget_ms": d["inputs"]["latency_budget_ms"],
            "primary": one(d["primary"]) if d["primary"] else None, "secondary": [one(c) for c in d["secondary"]],
            "missing": [c["id"] for c in d["checks"] if c["status"] == "missing"],
            "no_evidence": [c["id"] for c in d["checks"] if c["status"] == "no-evidence"], "headline": d["headline"]}


async def run_doctor(request: Request, run_id: str, latency_ms: float | None = None, measure_latency: bool = False,
                     served_p95_ms: float | None = None) -> dict[str, Any]:
    from ..engine.api import split_comparison
    from . import doctor as D

    store = request.app.state.runs
    rec = run_or_404(request, run_id)
    cfg = rec["config"]
    if rec["status"] != "succeeded":
        raise HTTPException(409, f"Run {run_id} is {rec['status']}; the doctor reads finished runs.")
    if cfg.get("kind") in ("forecast", "anomaly", "expected_loss", "segmented", "survival", "reject_inference", "multilabel") or cfg.get("import"):
        raise HTTPException(400, "The Model Doctor reads trained tabular runs (not forecast, anomaly or imported runs).")
    rec, vela = await compute_findings(request, run_id)
    res = rec.get("result") or {}
    key = await run_in_threadpool(D.cache_key, rec)
    cached = res.get("doctor_measured") or {}
    if cached.get("key") == key:
        measured = cached["data"]
    else:
        measured = await request.app.state.heavy.run(_measure_job, cfg, rec)
        store.merge_result(run_id, doctor_measured={"key": key, "data": measured})
    sc = await run_in_threadpool(split_comparison, run_id, request)
    oot = next((r for r in sc.get("runs") or [] if r.get("strategy") in ("time", "time-group") and r.get("optimism") is not None
                and (r.get("same_config") or r.get("this_run"))), None)
    oot_se = None
    if oot and sc.get("random_run_id"):
        recs = [store.get(sc["random_run_id"]), store.get(oot["run_id"])]
        ses = await request.app.state.heavy.run(_twin_se_job, recs)
        if all(s is not None for s in ses):
            oot_se = float((ses[0] ** 2 + ses[1] ** 2) ** 0.5)
    try:
        opts = await run_in_threadpool(request.app.state.datasets.split_options, rec["dataset_id"], rec["label"])
    except Exception:  # noqa: BLE001
        opts = {}
    shift_actions = []
    sh = res.get("shift") or {}
    if sh.get("key") and sh.get("status") == "succeeded":
        try:
            from ..engine import shift_api

            st = shift_api._state(rec, sh["key"], store)
            if st:
                shift_actions = shift_api.actions(request, rec, st)
        except Exception:  # noqa: BLE001
            shift_actions = []
    budget = latency_ms
    if budget is None:
        il = (((res.get("resolved") or {}).get("fit_kwargs") or {}).get("infer_limit")
              or ((cfg.get("fit_kwargs") or {}).get("infer_limit")))
        budget = float(il) * 1000 if il else None
    lat = ((res.get("latency_profile") or {}).get("1000"))
    if budget and (not lat or not all("served_ms_p95" in m for m in lat.get("models", []))):
        lat = None
        if measure_latency:
            from ..registry.api import latency_profile

            lat = await latency_profile(run_id, request, 1000)
        elif served_p95_ms is not None:  # measured by the caller (Autopilot's one-row timing of the served model)
            lat = {"models": [{"model": res.get("best_model"), "best": True, "served_ms_p95": float(served_p95_ms),
                               "score_rank": None, "score_val_natural": None}], "natural_greater_is_better": True,
                   "source": "caller"}
    from ..registry import monitoring_api

    try:
        mon = await run_in_threadpool(monitoring_api.doctor_context, request, run_id)
    except Exception:  # noqa: BLE001 — the doctor diagnoses without monitoring rather than fail
        mon = []
    ctx = {"run": rec, "monitoring": mon, "vela": vela["findings"], "vela_errors": vela["errors"], "measured": measured, "split_comparison": sc,
           "oot_se": oot_se, "split_options": opts, "shift_actions": shift_actions, "latency_budget_ms": budget, "latency": lat,
           "base": run_body(rec)}
    d = await run_in_threadpool(D.diagnose, ctx)
    store.merge_result(run_id, doctor=_doctor_summary(d))
    return d


@router.get("/runs/{run_id}/doctor")
async def model_doctor(run_id: str, request: Request, latency_ms: float | None = None, measure_latency: bool = False,
                       served_p95_ms: float | None = None) -> dict[str, Any]:
    """F17 · Model Doctor: every check (status, evidence numbers, the rule, a reason), the primary limiting factor and
    the ranked secondary ones, each with a one-click prescription that reuses an existing action, and offers to run the
    expensive checks that are missing (learning curve, shift, slices, latency). `latency_ms` is the one-row budget
    (default: the run's infer_limit); `measure_latency=true` measures the served latency when it is missing (F3); `served_p95_ms` passes a served one-row p95 measured elsewhere (Autopilot does)."""
    if latency_ms is not None and latency_ms <= 0:
        raise HTTPException(422, "latency_ms must be > 0.")
    return await run_doctor(request, run_id, latency_ms, measure_latency, served_p95_ms)
