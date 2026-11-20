"""D4 · Changes after deployment (routes; the numbers are in changes.py).

GET  /registry/versions/{vid}/changes                   eligibility, the newest labelled batch, its drift, the experiments
POST /registry/versions/{vid}/changes                   {kind: threshold | recalibrate | retrain_window | drop_feature, column?, actor}
POST /registry/versions/{vid}/changes/{cid}/request     {actor}: a result better beyond noise becomes a new registered
                                                        version (approval pending): the usual four-eyes review and
                                                        promotion follow. Nothing changes the live version.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
from fastapi import APIRouter, HTTPException, Request
from fastapi.concurrency import run_in_threadpool

from ..datasets import now
from ..http import version_or_404
from . import changes as C

router = APIRouter(prefix="/api", tags=["changes"])
SPECIAL = ("forecast", "anomaly", "expected_loss", "segmented", "survival", "reject_inference", "multilabel")


def _regdir(request: Request) -> Path:
    return request.app.state.settings.data_dir / "registry"


def _ctx(request: Request, vid: str) -> dict[str, Any]:
    reg = request.app.state.registry
    v = version_or_404(request, vid)
    run = request.app.state.runs.get(v["run_id"]) or {}
    ver = ((v.get("result") or {}).get("version")) or {}
    out: dict[str, Any] = {"version": v, "run": run, "eligible": False, "why": None}
    if v.get("status") != "ready":
        out["why"] = f"The version is {v.get('status')}."
        return out
    if (run.get("config") or {}).get("kind") in SPECIAL or (run.get("config") or {}).get("import") or ver.get("problem_type") != "binary":
        out["why"] = "Post-deployment changes are for trained yes/no (binary) versions."
        return out
    lab = [b for b in reg.list_batches(vid) if b["status"] == "succeeded" and ((b.get("result") or {}).get("labels") or {}).get("n_joined")]
    lab.sort(key=lambda b: (b.get("data_date") or b["created_at"][:10], b["created_at"]))
    if not lab:
        out["why"] = "No scored batch has labels yet: attach the labels of a newer batch (Monitoring) to evaluate changes on it."
        return out
    newest = lab[-1]
    n = int(newest["result"]["labels"]["n_joined"])
    pos = str(ver.get("positive_class"))
    t0 = float(ver.get("decision_threshold") if ver.get("decision_threshold") is not None else 0.5)
    drift = (newest.get("result") or {}).get("drift") or {}
    out.update(newest=newest, older=lab[:-1], positive=pos, t0=t0, cost=C.cost_of(run, t0), n=n,
               drifted=sorted([{"feature": f["feature"], "psi": f.get("psi"), "band": f.get("band")} for f in drift.get("features") or []
                               if (f.get("psi") or 0) >= 0.1], key=lambda f: -(f["psi"] or 0)),
               features=list(ver.get("features") or (run.get("result") or {}).get("features") or []))
    try:  # A2 M6: a drifted number whose whole distribution moved by one constant factor is a unit change at the source
        out["scale_changes"] = _scale_changes(request, run, newest, [d["feature"] for d in out["drifted"]])
    except Exception:  # noqa: BLE001 — the changes card works without it
        out["scale_changes"] = []
    try:  # A3 M6: a date column drifts by construction (new months): never the feature to drop
        inp = _regdir(request) / "batches" / newest["id"] / "input.csv"
        dcols = [d["feature"] for d in out["drifted"]]
        bt = pd.read_csv(inp, usecols=lambda c_: c_ in dcols, nrows=500) if dcols and inp.exists() else pd.DataFrame()
        out["date_features"] = [c_ for c_ in bt.columns if date_like(bt[c_])]
    except Exception:  # noqa: BLE001
        out["date_features"] = []
    if n < C.MIN_ROWS:
        out["why"] = f"The newest labelled batch has {n} labelled rows; changes need ≥ {C.MIN_ROWS}."
        return out
    out["eligible"] = True
    return out


def _scale_changes(request: Request, run: dict[str, Any], batch: dict[str, Any], feats: list[str]) -> list[dict[str, Any]]:
    return scale_changes_for(run, _regdir(request) / "batches" / batch["id"] / "input.csv", feats)


def date_like(s: pd.Series) -> bool:
    """A3 M6: a text column whose values read as dates (a date column drifts by construction: new months)."""
    if s.dtype != object:
        return False
    v = s.dropna().astype(str).head(200)
    if len(v) < 5 or pd.to_numeric(v, errors="coerce").notna().mean() >= 0.5:
        return False
    return bool(pd.to_datetime(v, errors="coerce", format="mixed").notna().mean() >= 0.9)


def scale_changes_for(run: dict[str, Any], inp: Path, feats: list[str]) -> list[dict[str, Any]]:
    """A2 M6, the Data Doctor's factor logic on a scored batch: for each drifted numeric feature, the ratio of the batch's
    quantiles (10–90 %, non-zero absolute values) to the training rows'. A unit change when the median ratio is ≥ 3 or
    ≤ 1/3 and every quantile ratio stays within a factor 1.5 of it (the whole distribution moved by one constant).
    A3: shared by Changes, the monitoring alerts and the retrain proposal."""
    from ..data_doctor import UNIT_FACTORS

    cfg = run.get("config") or {}
    if not feats or not cfg.get("csv_path"):
        return []
    inp = Path(inp)
    if not inp.exists():
        return []
    head = pd.read_csv(inp, nrows=0).columns
    cols = [f for f in feats if f in head]
    if not cols:
        return []
    tr = pd.read_csv(cfg["csv_path"], usecols=lambda c: c in cols)
    bt = pd.read_csv(inp, usecols=lambda c: c in cols)
    qs = [0.1, 0.25, 0.5, 0.75, 0.9]
    out = []
    for f in cols:
        a = pd.to_numeric(tr[f], errors="coerce").abs()
        b = pd.to_numeric(bt[f], errors="coerce").abs()
        a, b = a[(a > 0) & a.notna()], b[(b > 0) & b.notna()]
        if len(a) < 50 or len(b) < 30 or a.nunique() < 10:
            continue
        r = b.quantile(qs).to_numpy() / a.quantile(qs).to_numpy()
        if not np.all(np.isfinite(r)) or np.any(r <= 0):
            continue
        k = float(np.median(r))
        if not (k >= 3 or k <= 1 / 3) or float(np.max(np.abs(np.log(r / k)))) > np.log(1.5):
            continue
        near = min(UNIT_FACTORS, key=lambda u: abs(np.log(k / u)))
        known = abs(np.log(k / near)) <= np.log(1.25)
        fix = 1 / near if known else 1 / k
        out.append({"feature": f, "factor": round(k, 4), "unit": UNIT_FACTORS[near] if known else None, "rescale": round(fix, 6),
                    "quantile_ratios": [round(float(x), 4) for x in r], "snapped": near if known else None,
                    "short": f"{f} changed scale (× {(near if known else k):.4g}): fix the feed (rescale × {fix:,.6g} before scoring)",
                    "text": (f"{f} arrives × {k:.4g} its training scale" + (f" ({UNIT_FACTORS[near]})" if known else "")
                             + f": every quantile of the batch moved by the same factor (10–90 %: {', '.join(f'{x:.3g}' for x in r)}). "
                             f"A unit change at the source, not new behaviour: fix the feed (or rescale × {fix:,.6g} before scoring) "
                             "first; retraining on it or dropping the feature is the last resort.")})
    return out


def _batch_rows(request: Request, b: dict[str, Any], pos: str) -> tuple[np.ndarray, np.ndarray, list[int], str]:
    bdir = _regdir(request) / "batches" / b["id"]
    lab = pd.read_csv(bdir / "labels.csv")
    sc = pd.read_csv(bdir / "scored.csv")
    rows = lab["row"].to_numpy().astype(int)
    y = (lab["y"].astype(str).to_numpy() == pos).astype(int)
    col = f"p_{pos}" if f"p_{pos}" in sc.columns else next(c for c in sc.columns if c.startswith("p_") and c.endswith(pos))
    p = sc[col].to_numpy(dtype=float)[rows]
    return y, p, [int(r) for r in rows], str(bdir / "input.csv")


def _public(c: dict[str, Any]) -> dict[str, Any]:
    nb = c.get("newest")
    return {"eligible": c["eligible"], "why": c["why"],
            "newest_batch": nb and {"batch_id": nb["id"], "filename": nb["filename"], "data_date": nb.get("data_date"),
                                    "labelled_rows": c.get("n"), "label_date": ((nb.get("result") or {}).get("labels") or {}).get("label_date")},
            "older_batches": [{"batch_id": b["id"], "data_date": b.get("data_date"),
                               "labelled_rows": b["result"]["labels"]["n_joined"]} for b in c.get("older") or []],
            "cost": c.get("cost"), "threshold": c.get("t0"), "drifted": c.get("drifted") or [],
            "scale_changes": c.get("scale_changes") or [],
            # A2 M6: a feature whose drift is a unit change is fixed at the feed, never suggested for dropping
            "suggested_drop": next((d["feature"] for d in c.get("drifted") or []
                                    if d["feature"] not in {x["feature"] for x in c.get("scale_changes") or []}
                                    and d["feature"] not in (c.get("date_features") or [])), None),
            "suggested_fix": next(({"kind": "fix_feed", "feature": x["feature"], "rescale": x["rescale"], "title":
                                    f"Fix the feed: rescale {x['feature']} × {x['rescale']:,.6g}", "why": x["text"]}
                                   for x in c.get("scale_changes") or []), None)}


async def _refresh(request: Request, vid: str, c: dict[str, Any], items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Finish the run-based experiments whose run has succeeded: score the newest batch with it and compare."""
    changed = False
    for it in items:
        if it.get("status") not in ("running", "queued") or not it.get("run_id"):
            continue
        r = request.app.state.runs.get(it["run_id"]) or {}
        if r.get("status") in ("failed", "cancelled"):
            it.update(status="failed", error=r.get("error"), finished_at=now())
            changed = True
        elif r.get("status") == "succeeded":
            try:
                nb = request.app.state.registry.get_batch(it["batch_id"])
                y, p, rows, inp = await run_in_threadpool(_batch_rows, request, nb, c["positive"])
                pn = await request.app.state.heavy.run(C.score_with_run, r["config"]["predictor_path"], inp, rows, c["positive"])
                res = await run_in_threadpool(C.compare_models, y, p, np.asarray(pn), c["cost"])
                res["proposal"] = {"run_id": it["run_id"]}
                it.update(status="succeeded", result=res, verdict=C.verdict(res), finished_at=now())
            except Exception as e:  # noqa: BLE001
                it.update(status="failed", error=f"{type(e).__name__}: {e}"[:300], finished_at=now())
            changed = True
    if changed:
        await run_in_threadpool(C.save_all, _regdir(request), vid, items)
    return items


@router.get("/registry/versions/{vid}/changes")
async def get_changes(vid: str, request: Request) -> dict[str, Any]:
    """D4: the post-deployment experiments of a version, evaluated on its newest labelled batch against the version as
    it serves today; each better result can become a request (four-eyes review, then promotion)."""
    c = await run_in_threadpool(_ctx, request, vid)
    items = await run_in_threadpool(C.load_all, _regdir(request), vid)
    if c["eligible"]:
        items = await _refresh(request, vid, c, items)
    v = c["version"]
    return {"version_id": vid, "label": f"{v['model_name']} v{v['version']}", "stage": v.get("stage"), **_public(c),
            "kinds": C.KINDS, "experiments": sorted(items, key=lambda i: i.get("created_at") or "", reverse=True),
            "rules": {"evaluation": "Every experiment is scored on the newest labelled batch, the rows the version has not seen, "
                                    "against the version as it serves today; paired bootstrap (B = 1,000) over the labelled rows.",
                      "out_of_sample": "A threshold or a calibration is fitted on one half of the batch and applied to the other, "
                                       "and back (cross-fitted). Retrained models never see the newest batch.",
                      "request": "Only a result better beyond noise (CI above 0) can be requested. A request registers a new "
                                 "version (approval pending): a reviewer other than the requester and the experiment's author "
                                 "approves it (names as typed; case, spaces and punctuation ignored), then someone promotes "
                                 "it. Nothing changes the live version on its own.",
                      "cost": (c.get("cost") or {}).get("source")}}


@router.post("/registry/versions/{vid}/changes", status_code=201)
async def start_change(vid: str, request: Request, body: dict[str, Any]) -> dict[str, Any]:
    from ..engine.api import RunCreate, _create_run
    from ..gym.api import check_run_body, run_body
    from .monitoring_api import _window_dataset

    body = body or {}
    kind = body.get("kind")
    if kind not in C.KINDS:
        raise HTTPException(400, f"kind is one of {', '.join(C.KINDS)}; changes are typed.")
    actor = str(body.get("actor") or "").strip()
    if not actor:
        raise HTTPException(400, "actor is required (changes are audited).")
    c = await run_in_threadpool(_ctx, request, vid)
    if not c["eligible"]:
        raise HTTPException(409, c["why"])
    v, run, nb = c["version"], c["run"], c["newest"]
    it: dict[str, Any] = {"id": C.new_id(), "kind": kind, "title": C.KINDS[kind], "by": actor, "created_at": now(),
                          "batch_id": nb["id"], "batch": {"filename": nb["filename"], "data_date": nb.get("data_date"), "labelled_rows": c["n"]},
                          "status": "running", "result": None, "verdict": None, "request": None, "cost": c["cost"],
                          "review_id": body.get("review_id")}
    if kind in ("threshold", "recalibrate"):
        y, p, _, _ = await run_in_threadpool(_batch_rows, request, nb, c["positive"])
        fn = C.threshold_move if kind == "threshold" else C.recalibrate
        res = await run_in_threadpool(fn, y, p, c["t0"], c["cost"])
        it.update(status="succeeded", result=res, verdict=C.verdict(res), finished_at=now())
    else:
        b = run_body(run)
        b.update(parent_run_id=run["id"], source="change-request")
        if kind == "drop_feature":
            col = body.get("column") or (c["drifted"][0]["feature"] if c["drifted"] else None)
            if not col or col not in c["features"]:
                raise HTTPException(400, "drop_feature needs column: a feature of the version (default: the most drifted one).")
            b["drop_columns"] = list(dict.fromkeys(list(b.get("drop_columns") or []) + [col]))
            b["name"] = f"{v['model_name']} v{v['version']} · without {col} (change)"
            b["patch"] = {"id": "change:drop_feature", "title": f"without {col}", "changes": [], "change": {"kind": kind, "column": col}}
            it.update(column=col, title=f"Retrain without {col}",
                      psi=next((d["psi"] for d in c["drifted"] if d["feature"] == col), None))
        else:
            older = c["older"]
            n_old = sum(int(x["result"]["labels"]["n_joined"]) for x in older)
            include_ref = n_old < 300
            prop = {"batches": [{"batch_id": x["id"], "filename": x["filename"], "data_date": x.get("data_date"),
                                 "rows": x["result"]["labels"]["n_joined"]} for x in older],
                    "include_reference": include_ref,
                    "window": {"start": (older[0].get("data_date") or older[0]["created_at"][:10]) if older else (nb.get("data_date") or "")[:10] or "reference",
                               "end": (older[-1].get("data_date") or older[-1]["created_at"][:10]) if older else "reference"}}
            ds = await run_in_threadpool(_window_dataset, request, v, run, prop, actor)
            b.update(dataset_id=ds["id"], split_spec=None, name=f"{v['model_name']} v{v['version']} · recent window (change)",
                     patch={"id": "change:retrain_window", "title": "recent labelled window", "changes": [], "change": {"kind": kind}})
            it.update(window={"batches": [x["batch_id"] for x in prop["batches"]], "rows": n_old, "include_reference": include_ref,
                              "dataset_id": ds["id"], "note": "the newest batch is left out: it is the evaluation"})
        await check_run_body(request, b)
        new = await run_in_threadpool(_create_run, RunCreate(**{k: x for k, x in b.items() if x is not None or k == "time_limit"}), request)
        it["run_id"] = new["id"]
    items = await run_in_threadpool(C.load_all, _regdir(request), vid)
    items.append(it)
    await run_in_threadpool(C.save_all, _regdir(request), vid, items)
    request.app.state.registry.audit(actor=actor, action="change_experiment", version=v, run_id=it.get("run_id"),
                                     comment=f"{it['title']} on {nb['filename']} ({c['n']:,} labelled rows)" +
                                             (f": {it['verdict']}" if it.get("verdict") else ""),
                                     details={"change_id": it["id"], "kind": kind, "batch_id": nb["id"],
                                              "delta": (it.get("result") or {}).get("delta"), "ci": (it.get("result") or {}).get("ci")})
    return it


@router.post("/registry/versions/{vid}/changes/{cid}/request", status_code=201)
async def request_change(vid: str, cid: str, request: Request, body: dict[str, Any]) -> dict[str, Any]:
    from .api import RegisterRequest, register

    body = body or {}
    actor = str(body.get("actor") or "").strip()
    if not actor:
        raise HTTPException(400, "actor is required (the request is audited and its author cannot approve it).")
    c = await run_in_threadpool(_ctx, request, vid)
    items = await run_in_threadpool(C.load_all, _regdir(request), vid)
    it = next((i for i in items if i["id"] == cid), None)
    if not it:
        raise HTTPException(404, f"No change {cid} on version {vid}.")
    if it.get("request"):
        raise HTTPException(409, f"Change {cid} was already requested as version {it['request']['version_id']}.")
    if it.get("status") != "succeeded" or it.get("verdict") != "better":
        raise HTTPException(409, f"Only a result better beyond noise can be requested (this one is {it.get('verdict') or it.get('status')}).")
    v, run = c["version"], c["run"]
    opts = dict(v.get("options") or {})
    keep = {k: opts.get(k) for k in ("refit_full", "infer_limit", "infer_limit_batch_size", "clone_for_deployment", "prune_pipeline")
            if opts.get(k) is not None}
    prop = (it.get("result") or {}).get("proposal") or {}
    if it["kind"] in ("threshold", "recalibrate"):
        if not prop.get("decision_threshold"):
            raise HTTPException(409, "This result has no threshold to serve.")
        run_id = run["id"]
        if opts.get("serve_model"):
            keep["serve_model"] = opts["serve_model"]
        keep["decision_threshold"] = float(prop["decision_threshold"])
        if it["kind"] == "recalibrate":
            if not prop.get("platt") or "raw_threshold_equivalent" not in prop:
                raise HTTPException(409, "This recalibration was evaluated before calibrated serving; run the experiment again.")
            # The new version serves the calibrated probabilities: the Platt map (composed with the one the version
            # already serves, since it was fitted on the served p) is stored with it, and its threshold is on that scale.
            from ensemble_models.calibration import formula

            cal = C.compose_platt(opts.get("calibration"), prop["platt"])
            keep["calibration"] = cal
            desc = (f"{it['title']}: serves recalibrated probabilities p′ = {formula(cal['a'], cal['b'])}; "
                    f"threshold t {c['t0']:.4f} → {prop['decision_threshold']:.4f} on the calibrated scale (change {cid})")
        else:
            if opts.get("calibration"):  # a threshold move on a recalibrated version keeps serving its calibration
                keep["calibration"] = opts["calibration"]
            desc = f"{it['title']}: t {c['t0']:.4f} → {prop['decision_threshold']:.4f} (change {cid})"
    else:
        run_id = prop.get("run_id") or it.get("run_id")
        desc = f"{it['title']} (change {cid}, run {run_id})"
    r = it.get("result") or {}
    desc += f"; on {it['batch']['filename']}: {r.get('metric')} {r.get('current'):.4f} → {r.get('proposed'):.4f}, Δ {r['delta']:+.4f} [{r['ci'][0]:+.4f}, {r['ci'][1]:+.4f}]"
    if it["kind"] == "recalibrate" and r.get("cost"):
        rc = r["cost"]
        desc += (f"; cost / 1,000 at the new threshold {rc['current']:.1f} → {rc['proposed']:.1f}, Δ {rc['delta']:+.1f} "
                 f"[{rc['ci'][0]:+.1f}, {rc['ci'][1]:+.1f}] ({C.verdict(rc)})")
    out = await register(run_id, RegisterRequest(created_by=actor, model_name=v["model_name"], description=desc, change_id=cid, change_by=it.get("by"), **keep), request)
    it["request"] = {"version_id": out["id"], "by": actor, "at": now(), "run_id": run_id,
                     "next": f"A reviewer other than {actor}" + (f" and {it['by']} (who designed the experiment)" if it.get("by") and it["by"] != actor else "")
                             + " approves the new version in Registry (four eyes); promotion to "
                             "Production is a separate, human step."}
    await run_in_threadpool(C.save_all, _regdir(request), vid, items)
    request.app.state.registry.audit(actor=actor, action="change_requested", version=v, run_id=run_id,
                                     comment=f"{it['title']} → new version {out['id']} (approval pending)",
                                     details={"change_id": cid, "kind": it["kind"], "new_version_id": out["id"], "delta": r.get("delta"),
                                              "ci": r.get("ci"), "metric": r.get("metric"), "calibration": keep.get("calibration"),
                                              "decision_threshold": keep.get("decision_threshold"),
                                              "cost": r.get("cost") if it["kind"] == "recalibrate" else None})
    return {"change": it, "version": out}
