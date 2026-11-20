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
    gaps, exps, fl, known = await _d3_inputs(request, rec, opts)
    ctx = {"run": rec, "monitoring": mon, "vela": vela["findings"], "vela_errors": vela["errors"], "measured": measured, "split_comparison": sc,
           "oot_se": oot_se, "split_options": opts, "shift_actions": shift_actions, "latency_budget_ms": budget, "latency": lat,
           "base": run_body(rec), "gaps": gaps, "experiments": exps, "feature_lab": fl, "known": known}
    d = await run_in_threadpool(D.diagnose, ctx)
    d["next"] = await run_in_threadpool(_next_steps, request, rec)
    store.merge_result(run_id, doctor=_doctor_summary(d))
    return d


def _next_steps(request: Request, rec: dict[str, Any]) -> list[dict[str, Any]]:
    """The doctor's "also worth checking" list (doctor.next_steps), from Smart Intake on the run's own table."""
    from ..intake import intake
    from . import doctor as D

    try:
        found = intake(request.app.state.datasets.load_frame(rec["dataset_id"]))
    except Exception:  # noqa: BLE001 — a deleted or unreadable dataset: no suggestions rather than no diagnosis
        found = None
    reg = request.app.state.registry
    ver = next((v for v in reg.list_versions() if v.get("run_id") == rec["id"] and v.get("status") == "ready"), None)
    return D.next_steps(rec, found, ver)


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


# --------------------------------------------------------------------------- D3 · What's missing, typed experiments


GAPS_PROCEDURE = "missing/2"


def _gaps_job(cfg, rec, hints):
    from .missing import measure

    return measure(cfg, rec, hints)


def _gaps_key(rec: dict[str, Any]) -> str:
    from . import doctor as D

    fi = json.dumps((rec.get("result") or {}).get("feature_importance") or [], sort_keys=True, default=str)
    return GAPS_PROCEDURE + ":" + D.cache_key(rec) + ":" + hashlib.sha1(fi.encode()).hexdigest()[:10]


def _feature_lab_summary(request: Request, rec: dict[str, Any]) -> dict[str, Any] | None:
    """The newest finished Feature Lab job on the run's dataset (or the dataset it was made from), as a measured Δ."""
    from .. import featurelab as fl

    store = request.app.state.datasets
    ids = [rec["dataset_id"]]
    try:
        lin = store.lineage(rec["dataset_id"]) or {}
        if lin.get("parent"):
            ids.append(lin["parent"])
    except Exception:  # noqa: BLE001
        pass
    for ds in ids:
        try:
            jobs = fl.list_jobs(store.dir(ds))
        except Exception:  # noqa: BLE001
            continue
        for jb in jobs:
            if jb.get("status") != "succeeded":
                continue
            full = fl.read_job(store.dir(ds), jb["id"]) or {}
            res = full.get("result") or {}
            if res.get("target") not in (None, rec["label"]):
                continue
            jr = [e for e in res.get("journal") or [] if e.get("delta") is not None]
            ho = res.get("holdout")
            if ho:
                d, ci, src = ho["delta"], ho["ci"], f"job {jb['id']}: holdout {ho['before']:.4f} → {ho['after']:.4f} with the {len(res.get('kept') or [])} kept feature(s)"
            elif jr:
                b = max(jr, key=lambda e: e["delta"])
                d, ci, src = b["delta"], b["ci"], f"job {jb['id']}: best candidate {b.get('name') or b.get('op')} out of fold (nothing kept)"
            else:
                continue
            metric = res.get("metric_label")
            return {"job": jb["id"], "dataset_id": ds, "kept": len(res.get("kept") or []), "tried": len(res.get("journal") or []),
                    "delta": d if metric == "AUC" else None, "delta_raw": d, "ci": ci if metric == "AUC" else None, "metric": metric,
                    "source": src + ("" if metric == "AUC" else f" ({metric}; not on the gap scale)")}
    return None


async def _d3_inputs(request: Request, rec: dict[str, Any], opts: dict[str, Any]):
    from ..data_doctor_api import answers
    from . import experiments as X

    store = request.app.state.runs
    cfg = rec["config"]
    res = rec.get("result") or {}
    key = await run_in_threadpool(_gaps_key, rec)
    cached = res.get("doctor_gaps") or {}
    if cached.get("key") == key:
        gaps = cached["data"]
    else:
        dc = ((cfg.get("split") or {}).get("time_column") or (cfg.get("split_spec") or {}).get("time_column")
              or next((d["column"] for d in (opts or {}).get("date_columns") or []), None))
        try:
            gaps = await request.app.state.heavy.run(_gaps_job, cfg, rec, {"date_column": dc})
        except Exception as e:  # noqa: BLE001 — the doctor diagnoses without the gap analysis rather than fail
            gaps = {"errors": [f"gap analysis: {type(e).__name__}: {e}"[:300]]}
        store.merge_result(rec["id"], doctor_gaps={"key": key, "data": gaps})
    exps = await run_in_threadpool(X.list_for, store, rec)
    fl = await run_in_threadpool(_feature_lab_summary, request, rec)
    try:
        a = await run_in_threadpool(answers, request.app.state.datasets, rec["dataset_id"])
        known = [c for c, v in (a.get("known") or {}).items() if v is True]
    except Exception:  # noqa: BLE001
        known = []
    return gaps, exps, fl, known


@router.post("/runs/{run_id}/experiments", status_code=201)
async def start_experiment(run_id: str, request: Request, body: dict[str, Any]) -> dict[str, Any]:
    """D3: a typed experiment on a finished run: {kind: ablate, columns: [...]} | {kind: budget} | {kind: preset}. It trains
    the run's own body with that one change as a child run (lineage: parent_run_id, source doctor-experiment), on the same
    dataset and holdout; GET …/experiments gives Δ with a paired-bootstrap CI once it succeeds."""
    from ..engine.api import RunCreate, _create_run
    from ..gym.api import check_run_body
    from . import experiments as X

    rec = run_or_404(request, run_id)
    cfg = rec["config"]
    if rec["status"] != "succeeded":
        raise HTTPException(409, f"Run {run_id} is {rec['status']}; experiments start from a finished run.")
    if cfg.get("kind") in ("forecast", "anomaly", "expected_loss", "segmented", "survival", "reject_inference", "multilabel") or cfg.get("import"):
        raise HTTPException(400, "Experiments run on trained tabular runs.")
    try:
        b, what = X.body_for(rec, body or {})
    except ValueError as e:
        raise HTTPException(400, str(e)) from e
    await check_run_body(request, b)
    new = await run_in_threadpool(_create_run, RunCreate(**b), request)
    return {"id": new["id"], "run_id": new["id"], "parent_run_id": run_id, "kind": body.get("kind"), "title": what, "status": new.get("status")}


@router.get("/runs/{run_id}/experiments")
async def list_experiments(run_id: str, request: Request) -> dict[str, Any]:
    from . import experiments as X

    rec = run_or_404(request, run_id)
    items = await run_in_threadpool(X.list_for, request.app.state.runs, rec)
    return {"run_id": run_id, "experiments": items, "kinds": list(X.KINDS)}
