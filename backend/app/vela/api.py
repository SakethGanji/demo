"""Vela routes: findings computed from a run, each with one patch for the Gym, and their optional LLM wording."""

from __future__ import annotations

import asyncio

import hashlib
import json
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from fastapi.concurrency import run_in_threadpool
from ..authz import path, require# P19

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


@router.get("/runs/{run_id}/vela", dependencies=[require("run.read", path("run_id", "run"))])
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


@router.post("/runs/{run_id}/vela/wording", dependencies=[require("run.read", path("run_id", "run"))])
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
    from . import doctor as D

    def one(c):
        rx = c.get("prescription") or {}
        return {"id": c["id"], "title": c["title"], "severity": c["severity"], "reason": c["reason"],
                "prescription": {"kind": rx.get("kind"), "title": rx.get("title")} if rx else None}
    return {"schema": d["schema"], "computed_at": d["computed_at"], "latency_budget_ms": d["inputs"]["latency_budget_ms"],
            "primary": one(d["primary"]) if d["primary"] else None, "secondary": [one(c) for c in d["secondary"]],
            "missing": [c["id"] for c in d["checks"] if c["status"] == "missing"],
            "no_evidence": [c["id"] for c in d["checks"] if c["status"] == "no-evidence"], "headline": d["headline"],
            "open_critical": D.critical_questions(d)}


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
    fp = await run_in_threadpool(_diagnosis_fp, request, rec, latency_ms, measure_latency, served_p95_ms)
    hit = ((rec.get("result") or {}).get("doctor_cache") or {})
    if fp and hit.get("key") == fp and hit.get("data"):  # A2 m8: nothing the diagnosis reads has changed
        return {**hit["data"], "cache": {"hit": True, "key": fp, "computed_at": hit["data"].get("computed_at")}}
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
    from . import review as R

    rv = await run_in_threadpool(R.latest_done, rec)
    ctx = {"review": rv and {"id": rv["id"], "decider": rv.get("decider"), "model": rv.get("model"), "mode": rv.get("mode"),
                             "finished_at": rv.get("finished_at"), "domain_gaps": rv.get("domain_gaps") or []},
           "run": rec, "monitoring": mon, "vela": vela["findings"], "vela_errors": vela["errors"], "measured": measured, "split_comparison": sc,
           "oot_se": oot_se, "split_options": opts, "shift_actions": shift_actions, "latency_budget_ms": budget, "latency": lat,
           "base": run_body(rec), "gaps": gaps, "experiments": exps, "feature_lab": fl, "known": known}
    d = await run_in_threadpool(D.diagnose, ctx)
    d["next"] = await run_in_threadpool(_next_steps, request, rec)
    store.merge_result(run_id, doctor=_doctor_summary(d), **({"doctor_cache": {"key": fp, "data": d}} if fp else {}))
    return {**d, "cache": {"hit": False, "key": fp}}


VOLATILE = ("doctor", "doctor_measured", "doctor_gaps", "doctor_cache", "vela_wording")


def _diagnosis_fp(request: Request, rec: dict[str, Any], latency_ms, measure_latency, served_p95_ms) -> str | None:
    """A2 m8: a fingerprint of everything the diagnosis reads that can change after the fit: the run's result (minus the
    Doctor's own caches), its experiments and their states, the decision-time answers, the reviews, the registry
    batches of its versions, the runs of the same dataset (split twins) and the request's latency arguments. The same
    fingerprint → the stored diagnosis is returned as is."""
    from ..data_doctor_api import answers
    from . import experiments as X
    from . import review as R

    try:
        store = request.app.state.runs
        res = {k: v for k, v in (rec.get("result") or {}).items() if k not in VOLATILE}
        exps = [(e.get("run_id"), e.get("kind"), e.get("status"), json.dumps(e.get("spec"), sort_keys=True, default=str))
                for e in X.list_for(store, rec)]
        reg = request.app.state.registry
        vers = [(v["id"], v.get("status"), v.get("stage"), [(b["id"], b.get("status"), bool(((b.get("result") or {}).get("labels") or {}).get("n_joined")))
                                                           for b in reg.list_batches(v["id"])])
                for v in reg.list_versions() if v.get("run_id") == rec["id"]]
        sibs = [(r["id"], r.get("status")) for r in store.list(dataset_id=rec["dataset_id"], status="succeeded")]
        parts = [res, exps, answers(request.app.state.datasets, rec["dataset_id"]), [r["id"] for r in R.list_reviews(rec) if r["status"] == "done"][:1],
                 vers, sibs, [latency_ms, measure_latency, served_p95_ms], GAPS_PROCEDURE, D_VERSION]
        return hashlib.sha256(json.dumps(parts, sort_keys=True, default=str).encode()).hexdigest()[:20]
    except Exception:  # noqa: BLE001 — no fingerprint, no cache
        return None


D_VERSION = "doctor-f-a3/1"  # bump when diagnose() changes, so stored diagnoses are recomputed


def _next_steps(request: Request, rec: dict[str, Any]) -> list[dict[str, Any]]:
    """The doctor's "also worth checking" list (doctor.next_steps), from Smart Intake on the run's own table."""
    from ..intake import intake
    from . import doctor as D

    try:
        from .. import frames

        found = intake(frames.read(frames.table(rec["dataset_id"])))
    except Exception:  # noqa: BLE001 — a deleted or unreadable dataset: no suggestions rather than no diagnosis
        found = None
    reg = request.app.state.registry
    ver = next((v for v in reg.list_versions() if v.get("run_id") == rec["id"] and v.get("status") == "ready"), None)
    return D.next_steps(rec, found, ver)


_doctor_locks: dict[str, asyncio.Lock] = {}


async def warm_doctor(app, run_id: str) -> None:
    """A1 #12: compute the Model Doctor (its measurements and the gap analysis, both cached in the run's result) as soon
    as a fit succeeds, so the first page view reads the cache. Experiment children are skipped (the review reads them)."""
    from starlette.requests import Request as _Req

    rec = app.state.runs.get(run_id) or {}
    cfg = rec.get("config") or {}
    if cfg.get("source") in ("doctor-experiment", "split-twin") or cfg.get("kind") or cfg.get("import"):
        return
    try:
        req = _Req({"type": "http", "app": app, "headers": [], "method": "GET", "path": f"/api/runs/{run_id}/doctor", "query_string": b""})
        async with _doctor_locks.setdefault(run_id, asyncio.Lock()):
            await run_doctor(req, run_id)
    except Exception:  # noqa: BLE001 — a warm-up that fails leaves the page to compute it
        import logging

        logging.getLogger("ensemble.doctor").exception("warming the doctor for %s failed", run_id)


@router.get("/runs/{run_id}/doctor", dependencies=[require("run.read", path("run_id", "run"))])
async def model_doctor(run_id: str, request: Request, latency_ms: float | None = None, measure_latency: bool = False,
                       served_p95_ms: float | None = None) -> dict[str, Any]:
    """F17 · Model Doctor: every check (status, evidence numbers, the rule, a reason), the primary limiting factor and
    the ranked secondary ones, each with a one-click prescription that reuses an existing action, and offers to run the
    expensive checks that are missing (learning curve, shift, slices, latency). `latency_ms` is the one-row budget
    (default: the run's infer_limit); `measure_latency=true` measures the served latency when it is missing (F3); `served_p95_ms` passes a served one-row p95 measured elsewhere (Autopilot does)."""
    if latency_ms is not None and latency_ms <= 0:
        raise HTTPException(422, "latency_ms must be > 0.")
    async with _doctor_locks.setdefault(run_id, asyncio.Lock()):  # a page view waits for a warm-up already running
        out = await run_doctor(request, run_id, latency_ms, measure_latency, served_p95_ms)
    return await run_in_threadpool(_doctor_masked, request, run_id, out)


def _doctor_masked(request: Request, run_id: str, out: dict[str, Any]) -> dict[str, Any]:
    """P20 (C §4.3): the Model Doctor's examples and segments ("zip = 02139") with PII values masked, and the PII
    features this run uses flagged (`pii_features`)."""
    from ..lake import mask as M

    rec = run_or_404(request, run_id)
    out = M.run_scrub(rec, out)
    out["pii_features"] = M.pii_features(rec, list((rec.get("result") or {}).get("features") or []))
    return out


# --------------------------------------------------------------------------- D3 · What's missing, typed experiments


GAPS_PROCEDURE = "missing/14"  # A3 (F-A3): the label check leaves out the open timing questions and near-duplicates


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
    try:  # F-A2: unanswered Data Doctor suspects, left out of the label check's reference model (A3: and the answers)
        from ..data_doctor_api import answers as _ans, suspects as _sus

        kn = {c for c, v in (_ans(request.app.state.datasets, rec["dataset_id"]).get("known") or {}).items() if v is True}
        sus_h = [x["column"] for x in (_sus(request.app.state.datasets, rec["dataset_id"]) or []) if x.get("column") not in kn]
    except Exception:  # noqa: BLE001
        kn, sus_h = set(), []
    key = await run_in_threadpool(_gaps_key, rec)
    key += ":" + hashlib.sha1(json.dumps([sorted(sus_h), sorted(kn)]).encode()).hexdigest()[:8]
    cached = res.get("doctor_gaps") or {}
    if cached.get("key") == key:
        gaps = cached["data"]
    else:
        dc = ((cfg.get("split") or {}).get("time_column") or (cfg.get("split_spec") or {}).get("time_column")
              or next((d["column"] for d in (opts or {}).get("date_columns") or []), None))
        try:
            gaps = await request.app.state.heavy.run(_gaps_job, cfg, rec, {"date_column": dc, "suspects": sus_h, "known": sorted(kn)})
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


def _record_extra(request: Request, run_id: str, item: dict[str, Any]) -> None:
    store = request.app.state.runs
    cur = ((store.get(run_id) or {}).get("result") or {}).get("experiments_extra") or []
    store.merge_result(run_id, experiments_extra=[*cur, item])


def _flag_job(cfg, label):
    from .experiments import flag_training_labels

    return flag_training_labels(cfg, label)


async def _prepare(request: Request, rec: dict[str, Any], spec: dict[str, Any], by: str) -> tuple[str | None, dict[str, Any]]:
    """A dataset version with the parent's holdout rows for the data experiments; (None, why) when nothing changes."""
    import asyncio

    from .. import featurelab as fl
    from ..data_doctor_api import _apply
    from ..featurelab_api import start_feature_lab

    kind = spec["kind"]
    ds = rec["dataset_id"]
    if kind == "apply_data_fix":
        out = await run_in_threadpool(_apply, request, ds, spec["transforms"], by)
        eff = out["effects"]
        what = "; ".join(f"{e.get('op')} {e.get('column') or ''}".strip() for e in eff)
        return out["dataset"]["id"], {"dataset_id": out["dataset"]["id"], "effects": eff, "what": what,
                                      "rows_before": out["rows_before"], "rows_after": out["rows_after"],
                                      "columns_added": out["columns_added"], "columns_removed": out["columns_removed"]}
    if kind == "add_features":
        body = {"target": rec["label"], "apply": True, "by": by, "time_limit": 300, "max_candidates": 20}
        if spec.get("candidates"):
            body["candidates"] = spec["candidates"]
        job = await run_in_threadpool(start_feature_lab, ds, request, body)
        d = request.app.state.datasets.dir(ds)
        while True:
            await asyncio.sleep(2)
            j = await run_in_threadpool(fl.read_job, d, job["id"]) or {}
            if j.get("status") != "running":
                break
        res = j.get("result") or {}
        kept = res.get("kept") or []
        jr = [e for e in res.get("journal") or [] if e.get("delta") is not None]
        best = max(jr, key=lambda e: e["delta"]) if jr else None
        info = {"featurelab_job": job["id"], "tried": len(res.get("journal") or []), "kept": [t.get("name") for t in kept],
                "metric": res.get("metric_label"), "best": best and {"name": best.get("name"), "delta": best["delta"], "ci": best.get("ci")},
                "holdout": res.get("holdout"), "status": j.get("status"), "error": j.get("error")}
        if j.get("status") != "succeeded" or not j.get("version"):
            info["why"] = ("Feature Lab failed: " + str(j.get("error"))) if j.get("status") != "succeeded" else \
                f"Feature Lab kept none of the {info['tried']} candidates (scored on the training folds only; kept only on a clear gain)"
            return None, info
        info["what"] = ", ".join(info["kept"])
        info["dataset_id"] = j["version"]["id"] if isinstance(j["version"], dict) and "id" in j["version"] else (j["version"].get("dataset") or {}).get("id")
        return info["dataset_id"], info
    if kind == "clean_labels":
        fl_ = await request.app.state.heavy.run(_flag_job, rec["config"], rec["label"])
        rows = fl_.get("rows") or []
        if len(rows) < 10:
            return None, {**{k: v for k, v in fl_.items() if k != "rows"}, "why": f"cleanlab flags {len(rows)} training rows: nothing to clean"}
        relabel = spec.get("mode") == "relabel"
        tf = ({"op": "relabel", "rows": rows, "labels": fl_.get("suggested") or [], "reason": "cleanlab's suggested label on the "
               "training rows (out of fold)"} if relabel else
              {"op": "drop_rows", "rows": rows, "reason": "cleanlab label issues on the training rows (out of fold)"})
        out = await run_in_threadpool(_apply, request, ds, [tf], by)
        return out["dataset"]["id"], {"dataset_id": out["dataset"]["id"], "flagged": len(rows), "of": fl_.get("of"), "mode": "relabel" if relabel else "drop",
                                      "model": fl_.get("model"), "method": fl_.get("method"),
                                      "what": (f"{len(rows):,} of {fl_.get('of'):,} training labels set to cleanlab's suggestion" if relabel
                                               else f"{len(rows):,} of {fl_.get('of'):,} training rows dropped"),
                                      "rows_before": out["rows_before"], "rows_after": out["rows_after"]}
    return None, {}


async def start_experiment_impl(request: Request, run_id: str, body: dict[str, Any]) -> dict[str, Any]:
    from ..engine.api import RunCreate, _create_run
    from ..gym.api import check_run_body
    from ..datasets import now
    from . import experiments as X

    rec = run_or_404(request, run_id)
    cfg = rec["config"]
    if rec["status"] != "succeeded":
        raise HTTPException(409, f"Run {run_id} is {rec['status']}; experiments start from a finished run.")
    if cfg.get("kind") in ("forecast", "anomaly", "expected_loss", "segmented", "survival", "reject_inference", "multilabel") or cfg.get("import"):
        raise HTTPException(400, "Experiments run on trained tabular runs.")
    body = body or {}
    by = str(body.get("by") or "you")
    try:
        spec = X.validate(rec, {k: v for k, v in body.items() if k not in ("by", "review_id")})
    except ValueError as e:
        raise HTTPException(400, str(e)) from e
    kind = spec["kind"]
    base = {"id": "x_" + __import__("secrets").token_hex(4), "kind": kind, "spec": spec, "created_at": now(), "by": by,
            "review_id": body.get("review_id")}
    if kind in X.INSTANT:
        try:
            res = await run_in_threadpool(X.serve_rung_result if kind == "serve_rung" else X.cost_ratio_result, rec,
                                          *((spec["rung"],) if kind == "serve_rung" else (spec["c_fn"], spec["c_fp"])))
        except Exception as e:  # noqa: BLE001
            raise HTTPException(400, f"{kind}: {type(e).__name__}: {e}") from e
        title = (f"serve rung {spec['rung']} ({res['rung']['name']})" if kind == "serve_rung"
                 else f"cost ratio {spec['c_fn'] / spec['c_fp']:g}:1 (the same model, its cost-optimal threshold)")
        item = {**base, "title": title, "status": "succeeded", "result": res, "run_id": None}
        await run_in_threadpool(_record_extra, request, run_id, item)
        return {**item, "parent_run_id": run_id}
    if kind == "segment":
        from ..segmented.api import SegCreate, _create as seg_create

        k = len(set(((rec.get("result") or {}).get("features") or [])))
        tl = float(cfg.get("time_limit") or 120)
        sb = SegCreate(dataset_id=rec["dataset_id"], label=rec["label"], segment_column=spec["column"],
                       drop_columns=[c for c in (cfg.get("drop_columns") or []) if c != spec["column"]], split="random",
                       cost=({"c_fp": cfg["cost"]["c_fp"], "c_fn": cfg["cost"]["c_fn"]} if (cfg.get("cost") or {}).get("c_fn") else None),
                       presets="medium_quality", time_limit=max(30.0, round(tl / 4)),
                       name=f"{rec['name']} · segmented by {spec['column']}")
        try:
            new = await run_in_threadpool(seg_create, sb, request)
        except HTTPException:
            raise
        _ = k
        item = {**base, "title": f"one model per {spec['column']} (G5) vs one global model", "status": new.get("status"),
                "run_id": new["id"]}
        await run_in_threadpool(_record_extra, request, run_id, item)
        return {**item, "parent_run_id": run_id}
    if kind == "group_split":
        from ..engine.api import SplitTwin, split_twin

        new = await split_twin(run_id, SplitTwin(split_spec={"strategy": "group", "group_column": spec["column"]},
                                                 name=f"{rec['name']} · group split by {spec['column']}"), request)
        item = {**base, "title": f"group split by {spec['column']} (each entity on one side) vs the random split",
                "status": new.get("status"), "run_id": new["id"], "columns": [spec["column"]]}
        await run_in_threadpool(_record_extra, request, run_id, item)
        return {**item, "parent_run_id": run_id}
    ds_id, prep = None, None
    if kind in ("apply_data_fix", "add_features", "clean_labels"):
        ds_id, prep = await _prepare(request, rec, spec, by)
        if ds_id is None:
            item = {**base, "title": {"add_features": "Feature Lab features", "clean_labels": "cleaned training labels"}.get(kind, kind),
                    "status": "no-change", "prepared": prep, "run_id": None, "result": None}
            await run_in_threadpool(_record_extra, request, run_id, item)
            return {**item, "parent_run_id": run_id}
    try:
        b, what = X.body_for(rec, spec, ds_id, prep)
    except ValueError as e:
        raise HTTPException(400, str(e)) from e
    if body.get("review_id"):
        b["patch"]["review_id"] = body["review_id"]
    await check_run_body(request, b)
    new = await run_in_threadpool(_create_run, RunCreate(**b), request)
    return {"id": new["id"], "run_id": new["id"], "parent_run_id": run_id, "kind": kind, "title": what, "status": new.get("status"),
            "prepared": prep, "dataset_id": b["dataset_id"]}


@router.post("/runs/{run_id}/experiments", status_code=201, dependencies=[require("run.train", path("run_id", "run"))])
async def start_experiment(run_id: str, request: Request, body: dict[str, Any]) -> dict[str, Any]:
    """D3 + D4: a typed experiment on a finished run (`experiments.KINDS`, the whitelist; never free-form):
    - a child run with one change (ablate, budget, preset, monotone), or on a prepared dataset version with the parent's
      holdout rows (apply_data_fix → D1 fixes, add_features → D2 Feature Lab, clean_labels → cleanlab on the training
      rows only); lineage parent_run_id, source doctor-experiment;
    - instant, from the run's own files (serve_rung → the D2 ladder, cost_ratio → the same model at another ratio's
      Bayes threshold);
    - segment: a G5 segmented run against its global model.
    GET …/experiments gives Δ with a paired-bootstrap CI on the shared holdout rows, in the run's own metric."""
    return await start_experiment_impl(request, run_id, body)


@router.get("/runs/{run_id}/experiments", dependencies=[require("run.read", path("run_id", "run"))])
async def list_experiments(run_id: str, request: Request) -> dict[str, Any]:
    from . import experiments as X

    rec = run_or_404(request, run_id)
    items = await run_in_threadpool(X.list_for, request.app.state.runs, rec)
    return {"run_id": run_id, "experiments": items, "kinds": list(X.KINDS), "describe": X.DESCRIBE}


# --------------------------------------------------------------------------- D4 · Review


@router.post("/runs/{run_id}/review", status_code=202, dependencies=[require("run.train", path("run_id", "run"))])
async def start_review(run_id: str, request: Request, body: dict[str, Any] | None = None) -> dict[str, Any]:
    """D4: start the review loop on a finished run: the four LLM roles (perceive → task → errors → propose), each
    hypothesis proved or rejected by a typed experiment on the same holdout, the journal, the improvement plan. Body:
    {by, max_experiments (1–8, default 5), max_minutes (5–240, default 30), offline (the deterministic Doctor proposes)}.
    Returns at once; GET …/review/{id} has the live journal."""
    from . import review as R

    rec = run_or_404(request, run_id)
    cfg = rec["config"]
    if rec["status"] != "succeeded":
        raise HTTPException(409, f"Run {run_id} is {rec['status']}; a review reads a finished run.")
    if cfg.get("kind") in ("forecast", "anomaly", "expected_loss", "segmented", "survival", "reject_inference", "multilabel") or cfg.get("import"):
        raise HTTPException(400, "The review reads trained tabular runs.")
    body = body or {}
    try:
        mx = int(body.get("max_experiments") or R.MAX_EXPERIMENTS)
        mm = float(body.get("max_minutes") or R.MAX_MINUTES)
    except (TypeError, ValueError):
        raise HTTPException(400, "max_experiments and max_minutes are numbers.") from None
    if not 1 <= mx <= 8 or not 5 <= mm <= 240:
        raise HTTPException(400, "max_experiments is 1–8 and max_minutes 5–240.")
    cur = await run_in_threadpool(R.running, rec)
    if cur:
        raise HTTPException(409, f"Review {cur} is already running on this run.")
    rv = R.start(request.app, rec, str(body.get("by") or "you"), {"max_experiments": mx, "max_minutes": mm,
                                                                  "offline": bool(body.get("offline"))})
    return {k: rv[k] for k in ("id", "run_id", "status", "mode", "mode_note", "options", "llm_route", "model")}


@router.get("/runs/{run_id}/review", dependencies=[require("run.read", path("run_id", "run"))])
async def list_run_reviews(run_id: str, request: Request) -> dict[str, Any]:
    from . import review as R

    rec = run_or_404(request, run_id)
    return {"run_id": run_id, "reviews": await run_in_threadpool(R.list_reviews, rec), "rules": R.RULES}


@router.get("/runs/{run_id}/review/{review_id}", dependencies=[require("run.read", path("run_id", "run"))])
async def get_run_review(run_id: str, review_id: str, request: Request) -> dict[str, Any]:
    from . import review as R

    rec = run_or_404(request, run_id)
    rv = await run_in_threadpool(R.load, rec, review_id)
    if not rv:
        raise HTTPException(404, f"No review {review_id} on run {run_id}.")
    return rv
