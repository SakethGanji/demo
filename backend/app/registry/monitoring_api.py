"""F18 · Monitoring over time, champion / challenger and the retrain proposal (the API; the numbers are in monitoring.py).

GET  /registry/versions/{vid}/monitoring               the version's time series, alerts, challenger comparison, retrain proposal
PUT  /registry/models/{name}/monitoring                thresholds, the challenger (a Staging version), id / label columns (audited)
POST /registry/batches/{bid}/labels                    a later label file for a scored batch, keyed by an id column
GET  /registry/versions/{vid}/monitoring/retrain       the proposal: a run on the recent labelled window with the champion's config
POST /registry/versions/{vid}/monitoring/retrain       launch it (a click; never automatic)
GET  /monitoring/stream                                the simulated 12-month stream (sample loan-stream)
POST /registry/versions/{vid}/monitoring/stream        feed one month of it: score the month, deliver the labels now due
"""

from __future__ import annotations

import json
import secrets
from datetime import date
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
from fastapi import APIRouter, File, Form, HTTPException, Request, UploadFile
from fastapi.concurrency import run_in_threadpool
from pydantic import BaseModel, Field

from .. import config
from ..http import batch_or_404, version_or_404
from . import monitoring as M
from .store import RegistryStore

router = APIRouter(prefix="/api", tags=["monitoring"])
STREAM_DATASET = "loan-stream"


def _reg(request: Request) -> RegistryStore:
    return request.app.state.registry


def _dir(request: Request) -> Path:
    return request.app.state.settings.data_dir / "registry"


def _version(request: Request, vid: str) -> dict[str, Any]:
    return version_or_404(request, vid)


def _label(v: dict[str, Any]) -> str:
    return f"{v['model_name']} v{v['version']}"


# --------------------------------------------------------------------------- configuration


def _cfg_path(request: Request, model: str) -> Path:
    return _dir(request) / "monitoring" / f"{model}.json"


def _defaults(request: Request, model: str) -> dict[str, Any]:
    m = _reg(request).get_model(model) or {}
    ds = request.app.state.db.get_dataset(m.get("dataset_id") or "") or {}
    ids = [c["name"] for c in (ds.get("profile") or {}).get("columns", []) if c.get("type") == "id"]
    return {"thresholds": dict(M.DEFAULT_THRESHOLDS), "challenger_version_id": None, "id_column": ids[0] if ids else None,
            "label_column": m.get("label"), "retrain_window_months": 6, "updated_by": None, "updated_at": None}


def get_config(request: Request, model: str) -> dict[str, Any]:
    cfg = _defaults(request, model)
    p = _cfg_path(request, model)
    if p.exists():
        saved = json.loads(p.read_text())
        cfg.update({k: v for k, v in saved.items() if k != "thresholds"})
        cfg["thresholds"].update(saved.get("thresholds") or {})
    return cfg


class MonitoringConfig(BaseModel):
    actor: str
    thresholds: dict[str, float] | None = Field(None, description="Any of " + ", ".join(M.DEFAULT_THRESHOLDS))
    challenger_version_id: str | None = Field(None, description="A Staging version that shadow-scores every batch of the "
                                                                "Production version; '' clears it.")
    id_column: str | None = Field(None, description="The column that keys label files to batch rows.")
    label_column: str | None = None
    retrain_window_months: int | None = Field(None, ge=1, le=36)


@router.put("/registry/models/{name}/monitoring")
def put_config(name: str, body: MonitoringConfig, request: Request) -> dict[str, Any]:
    reg = _reg(request)
    if not reg.get_model(name):
        raise HTTPException(404, f"No registered model '{name}'.")
    actor = (body.actor or "").strip()
    if not actor:
        raise HTTPException(400, "actor is required (the change is audited).")
    cfg = get_config(request, name)
    old = json.loads(json.dumps(cfg))
    if body.thresholds:
        bad = [k for k in body.thresholds if k not in M.DEFAULT_THRESHOLDS]
        if bad:
            raise HTTPException(400, f"Unknown thresholds: {', '.join(bad)}.")
        if any(v < 0 for v in body.thresholds.values()):
            raise HTTPException(400, "Thresholds must be ≥ 0.")
        cfg["thresholds"].update(body.thresholds)
    if body.challenger_version_id is not None:
        if body.challenger_version_id == "":
            cfg["challenger_version_id"] = None
        else:
            c = reg.get_version(body.challenger_version_id)
            if not c:
                raise HTTPException(404, f"No version with id '{body.challenger_version_id}'.")
            if c["status"] != "ready":
                raise HTTPException(409, f"Version {c['id']} is {c['status']}.")
            if c["stage"] != "Staging":
                raise HTTPException(409, f"{_label(c)} is in {c['stage']}: a challenger shadow-scores from Staging. "
                                         "Move it to Staging first (the transition is audited).")
            m = reg.get_model(name)
            cm = reg.get_model(c["model_name"])
            if (cm or {}).get("label") != m.get("label"):
                raise HTTPException(400, f"{_label(c)} predicts '{(cm or {}).get('label')}', not '{m.get('label')}'.")
            run = request.app.state.runs.get(c["run_id"]) or {}
            if ((run.get("result") or {}).get("problem_type")) != "binary":
                raise HTTPException(400, "Champion / challenger compares binary versions (p̂ of the same positive class).")
            cfg["challenger_version_id"] = c["id"]
    for k in ("id_column", "label_column", "retrain_window_months"):
        if getattr(body, k) is not None:
            cfg[k] = getattr(body, k)
    cfg["updated_by"] = actor
    cfg["updated_at"] = date.today().isoformat()
    p = _cfg_path(request, name)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(cfg, indent=1))
    changed = {k: {"from": old.get(k), "to": cfg.get(k)} for k in cfg if k not in ("updated_by", "updated_at") and old.get(k) != cfg.get(k)}
    prod = next((x for x in reg.list_versions(name) if x["stage"] == "Production"), None)
    reg.audit(actor=actor, action="monitoring_configured", version=prod or (reg.list_versions(name) or [None])[0],
              comment=", ".join(changed) or "no change", details={"model": name, "changed": changed})
    return {"model": name, "config": cfg, "changed": changed}


# --------------------------------------------------------------------------- batches: date, shadow, labels


def resolve_data_date(request: Request, run: dict[str, Any] | None, csv_path: Path, given: str | None) -> str:
    if given:
        try:
            return date.fromisoformat(given.strip()[:10]).isoformat()
        except ValueError as e:
            raise HTTPException(400, f"data_date must be YYYY-MM-DD, not '{given}'.") from e
    col = (((run or {}).get("config") or {}).get("split") or {}).get("time_column") or \
          (((run or {}).get("config") or {}).get("split_spec") or {}).get("time_column")
    if col:
        try:
            s = pd.to_datetime(pd.read_csv(csv_path, usecols=[col])[col], errors="coerce").dropna()
            if len(s):
                return str(s.max().date())
        except (ValueError, KeyError):
            pass
    return date.today().isoformat()


def shadow_for(request: Request, v: dict[str, Any]) -> dict[str, Any] | None:
    """The challenger that shadow-scores this version's batches: only for the Production version, only a ready
    Staging version named in the model's monitoring config."""
    if v.get("stage") != "Production":
        return None
    cid = get_config(request, v["model_name"]).get("challenger_version_id")
    c = _reg(request).get_version(cid) if cid else None
    if not c or c["status"] != "ready" or c["stage"] != "Staging" or not c.get("path"):
        return None
    return {"version_id": c["id"], "path": c["path"], "label": _label(c)}


@router.post("/registry/batches/{bid}/labels")
async def upload_labels(bid: str, request: Request, file: UploadFile = File(...), created_by: str = Form(""),
                        id_column: str | None = Form(None), label_column: str | None = Form(None),
                        label_date: str | None = Form(None)) -> dict[str, Any]:
    """Labels that arrive after a batch was scored: a CSV with the id column and the label (and optionally label_date).
    Joined to the batch's rows on the id; performance on the joined rows, the lag label date − data date, and the paired
    champion / challenger comparison when the batch was shadow-scored."""
    b = batch_or_404(request, bid)
    if b["status"] != "succeeded":
        raise HTTPException(409, f"Batch {bid} is {b['status']}; labels join a scored batch.")
    who = (created_by or "").strip()
    if not who:
        raise HTTPException(400, "created_by is required (labels are audited).")
    content = await file.read(config.MAX_UPLOAD_BYTES + 1)
    if len(content) > config.MAX_UPLOAD_BYTES:
        raise HTTPException(413, "The label file is too large.")
    if not content.strip():
        raise HTTPException(400, "The label file is empty.")
    if label_date:
        try:
            label_date = date.fromisoformat(label_date.strip()[:10]).isoformat()
        except ValueError as e:
            raise HTTPException(400, f"label_date must be YYYY-MM-DD, not '{label_date}'.") from e
    return await attach(request, b, content, who, id_column, label_column, label_date)


async def attach(request: Request, b: dict[str, Any], content: bytes, who: str, id_column: str | None,
                 label_column: str | None, label_date: str | None) -> dict[str, Any]:
    from . import scoring

    reg = _reg(request)
    v = _version(request, b["version_id"])
    run = request.app.state.runs.get(v["run_id"]) or {}
    cfg = get_config(request, v["model_name"])
    idc = id_column or cfg.get("id_column")
    lc = label_column or cfg.get("label_column") or run.get("label")
    if not idc:
        raise HTTPException(400, "Say which column keys the labels to the batch rows (id_column).")
    bdir = _dir(request) / "batches" / b["id"]
    lp = bdir / f"labels_upload_{secrets.token_hex(3)}.csv"
    lp.write_bytes(content)
    res = (b.get("result") or {})
    summ = res.get("summary") or {}
    info = {"problem_type": summ.get("problem_type"), "positive_class": summ.get("positive_class"),
            "classes": summ.get("classes"), "threshold": summ.get("threshold") or 0.5,
            "cost": (run.get("config") or {}).get("cost"), "data_date": b.get("data_date"), "actor": who,
            "challenger_threshold": (res.get("shadow") or {}).get("threshold_challenger")}
    try:
        out = await request.app.state.heavy.run(scoring.attach_labels, str(request.app.state.settings.db_path), b["id"],
                                                str(bdir), str(lp), idc, lc, label_date, info)
    except ValueError as e:
        raise HTTPException(400, str(e)) from e
    perf = out.get("performance") or {}
    reg.audit(actor=who, action="labels_attached", version=v,
              comment=f"{out['n_joined']:,} of {out['n_batch']:,} rows of {b['filename']} labelled, {out.get('lag_days')} days after the data",
              details={"batch_id": b["id"], "n_joined": out["n_joined"], "lag_days": out.get("lag_days"),
                       "auc": perf.get("auc"), "observed_rate": perf.get("observed_rate"), "mean_p": perf.get("mean_p")})
    return {"batch_id": b["id"], "labels": out}


# --------------------------------------------------------------------------- the time series


def _adversarial(request: Request, run: dict[str, Any], bid: str) -> dict[str, Any] | None:
    from ..engine import shift as sh
    from ..engine import shift_api

    try:
        st = shift_api._state(run, f"batch-{bid}", request.app.state.runs)
    except Exception:  # noqa: BLE001
        return None
    if not st:
        return None
    s = sh.summary(st) or {}
    return {k: s.get(k) for k in ("status", "auc", "ci", "verdict", "top_features", "n_reference", "n_new", "key")}


def _point(request: Request, run: dict[str, Any], b: dict[str, Any], th: dict[str, Any], baseline: dict[str, Any] | None) -> dict[str, Any]:
    res = b.get("result") or {}
    s = res.get("summary") or {}
    d = res.get("drift") or {}
    lab = res.get("labels") or None
    p: dict[str, Any] = {
        "batch_id": b["id"], "filename": b["filename"], "data_date": b.get("data_date"),
        "period": (b.get("data_date") or "")[:7] or None, "scored_at": (b.get("finished_at") or "")[:10] or None,
        "n_rows": s.get("n_rows"), "max_psi": d.get("max_psi"),
        "features": [{"feature": f["feature"], "psi": f.get("psi"), "band": f.get("band")} for f in d.get("features") or []],
        "prediction_psi": (d.get("prediction") or {}).get("psi"), "prediction_ks": ((d.get("prediction") or {}).get("ks") or {}).get("statistic"),
        "mean_p": s.get("mean_p"), "mean_p_reference": s.get("mean_p_reference"), "flagged_share": s.get("flagged_share"),
        "score_quantiles": s.get("score_quantiles"), "mean_prediction": s.get("mean_prediction"),
        "adversarial": _adversarial(request, run, b["id"]),
        "performance": (lab or {}).get("performance"), "labels_date": (lab or {}).get("label_date"),
        "label_lag_days": (lab or {}).get("lag_days"), "label_coverage": (lab or {}).get("coverage"),
        "shadow": {k: v for k, v in (res.get("shadow") or {}).items() if k not in ("quantiles_champion", "quantiles_challenger")} or None,
        "paired": (lab or {}).get("paired"),
    }
    p["alerts"] = M.alerts_for(p, baseline, th)
    return p


def _baseline(v: dict[str, Any], run: dict[str, Any]) -> dict[str, Any] | None:
    """The version's own holdout performance, the reference every batch is compared with."""
    res = run.get("result") or {}
    if res.get("problem_type") != "binary" or not v.get("path"):
        return None
    hp_path = Path(v["path"]).parent / "holdout_predictions.csv"
    if not hp_path.exists():
        hp_path = Path(run["config"]["run_dir"]) / "holdout_predictions.csv"
    if not hp_path.exists():
        return None
    hp = pd.read_csv(hp_path)
    vres = (v.get("result") or {}).get("version") or {}
    pos = str(vres.get("positive_class") or res.get("positive_class"))
    if f"p_{pos}" not in hp.columns:
        return None
    y = (hp["y_true"].astype(str) == pos).astype(int).to_numpy()
    t = vres.get("decision_threshold")
    out = M.perf_binary(y, hp[f"p_{pos}"].to_numpy(dtype=float), float(t if t is not None else 0.5))
    out["source"] = "the version's predictions on its run's holdout"
    out["data_until"] = None
    return out


def _pooled_paired(request: Request, points: list[dict[str, Any]], cid: str | None) -> dict[str, Any] | None:
    """Every labelled row that was shadow-scored by the current challenger, pooled across batches."""
    ys, a, b, used = [], [], [], []
    for p in points:
        if not p.get("paired") or not p.get("shadow") or p["shadow"].get("version_id") != cid:
            continue
        bdir = _dir(request) / "batches" / p["batch_id"]
        if not (bdir / "labels.csv").exists() or not (bdir / "shadow.csv").exists():
            continue
        lab = pd.read_csv(bdir / "labels.csv")
        sh = pd.read_csv(bdir / "shadow.csv").set_index("row")
        b_rec = _reg(request).get_batch(p["batch_id"]) or {}
        pos = ((b_rec.get("result") or {}).get("summary") or {}).get("positive_class")
        ys.append((lab["y"].astype(str) == str(pos)).astype(int).to_numpy())
        a.append(sh.loc[lab["row"], "p_champion"].to_numpy())
        b.append(sh.loc[lab["row"], "p_challenger"].to_numpy())
        used.append(p["batch_id"])
    if not used:
        return None
    y = np.concatenate(ys)
    out = M.paired_with_labels(y, np.concatenate(a), np.concatenate(b))
    out["batches"] = used
    return out


def monitoring_state(request: Request, vid: str) -> dict[str, Any]:
    reg = _reg(request)
    v = _version(request, vid)
    run = request.app.state.runs.get(v["run_id"]) or {}
    cfg = get_config(request, v["model_name"])
    th = cfg["thresholds"]
    baseline = _baseline(v, run) if v["status"] == "ready" else None
    bs = [b for b in reg.list_batches(vid) if b["status"] == "succeeded"]
    bs.sort(key=lambda b: (b.get("data_date") or b["created_at"][:10], b["created_at"]))
    points = [_point(request, run, b, th, baseline) for b in bs]
    running = [{"batch_id": b["id"], "status": b["status"], "stage": b.get("stage"), "data_date": b.get("data_date")}
               for b in reg.list_batches(vid) if b["status"] in ("queued", "running")]
    cid = cfg.get("challenger_version_id")
    ch = reg.get_version(cid) if cid else None
    shadowed = [p for p in points if p.get("shadow") and not p["shadow"].get("error") and p["shadow"].get("version_id") == cid]
    challenger = None
    if ch:
        challenger = {"version_id": ch["id"], "label": _label(ch), "stage": ch["stage"], "approval": ch["approval"],
                      "run_id": ch["run_id"], "batches": len(shadowed),
                      "agreement_mean": float(np.mean([p["shadow"]["agreement"] for p in shadowed])) if shadowed else None,
                      "psi_mean": float(np.mean([p["shadow"]["psi"] for p in shadowed])) if shadowed else None,
                      "spearman_mean": float(np.mean([p["shadow"]["spearman"] for p in shadowed])) if shadowed else None,
                      "pooled": _pooled_paired(request, points, cid),
                      "decision": "The stage change stays a human decision: an approved four-eyes review, then the "
                                  "transition to Production. Nothing here promotes the challenger.",
                      "promote": {"review": f"/api/registry/versions/{ch['id']}/review",
                                  "transition": f"/api/registry/versions/{ch['id']}/transition"}}
    summ = M.summarize_alerts(points)
    stream = None
    if run.get("dataset_id") == STREAM_DATASET:
        fed = sorted({int(b["filename"].split("_m")[-1].split(".")[0]) for b in reg.list_batches(vid)
                      if b["filename"].startswith("stream_") and "_m" in b["filename"]})
        stream = {"dataset": STREAM_DATASET, "fed_months": fed, "next_month": next((m for m in range(1, 13) if m not in fed), None)}
    return {"schema": "monitoring/1", "version_id": vid, "label": _label(v), "stage": v["stage"], "model": v["model_name"],
            "run_id": v["run_id"], "problem_type": (run.get("result") or {}).get("problem_type"), "config": cfg,
            "rules": M.THRESHOLD_RULES, "baseline": baseline, "points": points, "running": running, "alerts": summ,
            "challenger": challenger, "stream": stream,
            "retrain": {"endpoint": f"/api/registry/versions/{vid}/monitoring/retrain",
                        "suggested": bool(summ["active"] or summ["first_performance"] or summ["first_covariate"])},
            "formulas": {"psi": "PSI = Σᵢ (aᵢ − eᵢ) · ln(aᵢ / eᵢ)", "auc_ci": "AUC ± 1.96 · SE_DeLong",
                         "calibration": "z = (Σy − Σp̂) / √Σp̂(1 − p̂)", "lag": "lag = label date − data date",
                         "paired": "Δ = AUC_challenger − AUC_champion,  Var(Δ) = Var(A₁) + Var(A₂) − 2 Cov(A₁, A₂)"}}


@router.get("/registry/versions/{vid}/monitoring")
async def get_monitoring(vid: str, request: Request) -> dict[str, Any]:
    """F18: the version's batches in data order: PSI per feature, the score's PSI and distribution, F15's adversarial AUC
    where checked, performance once labels arrived (with the lag), alerts against the model's thresholds, the
    challenger's shadow comparison (pooled paired ΔAUC once labelled) and the retrain proposal's endpoint."""
    return await run_in_threadpool(monitoring_state, request, vid)


def doctor_context(request: Request, run_id: str) -> list[dict[str, Any]]:
    """For F17's Model Doctor: the monitoring state of every version of this run that has scored batches."""
    out = []
    for v in _reg(request).list_versions():
        if v["run_id"] != run_id or v["status"] != "ready" or not _reg(request).list_batches(v["id"]):
            continue
        try:
            st = monitoring_state(request, v["id"])
        except Exception:  # noqa: BLE001
            continue
        if not st["points"]:
            continue
        a = st["alerts"]
        out.append({"version_id": v["id"], "label": st["label"], "stage": v["stage"], "batches": len(st["points"]),
                    "active": a["active"], "first_covariate": a["first_covariate"], "first_performance": a["first_performance"],
                    "latest": st["points"][-1]["period"], "latest_labelled": a["latest_labelled"],
                    "retrain_endpoint": st["retrain"]["endpoint"]})
    return out


# --------------------------------------------------------------------------- the retrain proposal


def _window(request: Request, vid: str, months: int) -> tuple[list[dict[str, Any]], str | None, str | None]:
    """The labelled batches whose data date lies in the last `months` months before the latest labelled one."""
    reg = _reg(request)
    lab = [b for b in reg.list_batches(vid) if b["status"] == "succeeded" and (b.get("result") or {}).get("labels")]
    lab.sort(key=lambda b: b.get("data_date") or b["created_at"][:10])
    if not lab:
        return [], None, None
    end = pd.Timestamp(lab[-1].get("data_date") or lab[-1]["created_at"][:10])
    ym = lambda t: t.year * 12 + t.month - 1  # noqa: E731
    first = ym(end) - months + 1  # the window is whole months: the latest labelled month and the months - 1 before it
    start = pd.Timestamp(year=first // 12, month=first % 12 + 1, day=1)
    sel = [b for b in lab if ym(pd.Timestamp(b.get("data_date") or b["created_at"][:10])) >= first]
    return sel, str(start.date()), str(end.date())


def _proposal(request: Request, vid: str, months: int | None, include_reference: bool) -> dict[str, Any]:
    from ..gym.api import run_body

    v = _version(request, vid)
    run = request.app.state.runs.get(v["run_id"])
    if not run:
        raise HTTPException(404, f"The run behind {_label(v)} is gone.")
    if (run.get("config") or {}).get("kind") in ("forecast", "anomaly", "expected_loss", "segmented") or (run.get("config") or {}).get("import"):
        raise HTTPException(400, "The retrain proposal is for trained tabular versions.")
    cfg = get_config(request, v["model_name"])
    months = months or cfg.get("retrain_window_months") or 6
    sel, start, end = _window(request, vid, months)
    body = run_body(run)
    split_in = dict(body.get("split_spec") or (run["config"].get("split") or {}) or {})
    split_note = "random stratified holdout of the window (as the champion)"
    split = None
    if split_in.get("strategy") and split_in["strategy"] != "random-stratified":
        split = {k: split_in[k] for k in ("strategy", "time_column", "group_column", "tuning_share") if split_in.get(k)}
        if split_in.get("cutoff"):
            split_note = (f"{split['strategy']} split on {split.get('time_column') or split.get('group_column')} as the champion; the "
                          f"champion's cut-off {split_in['cutoff']} is before the window, so it is re-drawn: the latest 20 % of the window's rows")
        else:
            split_note = f"{split['strategy']} split on {split.get('time_column') or split.get('group_column')}, as the champion"
    body = {**body, "split_spec": split}
    n_rows = sum(int(((b.get("result") or {}).get("labels") or {}).get("n_joined") or 0) for b in sel)
    ref_rows = 0
    if include_reference:
        ds = request.app.state.db.get_dataset(run["dataset_id"]) or {}
        ref_rows = int(ds.get("rows") or 0)
    alerts = monitoring_state(request, vid)["alerts"]
    why = []
    if alerts["first_covariate"]:
        why.append(f"covariate drift since {alerts['first_covariate']['period']} ({alerts['first_covariate']['text']})")
    if alerts["first_performance"]:
        why.append(f"performance since {alerts['first_performance']['period']}, seen {alerts['first_performance']['visible_on']} "
                   f"({alerts['first_performance']['text']})")
    return {"version_id": vid, "label": _label(v), "champion_run_id": run["id"], "window_months": months,
            "window": {"start": start, "end": end}, "batches": [{"batch_id": b["id"], "filename": b["filename"],
                                                                "data_date": b.get("data_date"),
                                                                "rows": b["result"]["labels"]["n_joined"],
                                                                "label_date": b["result"]["labels"].get("label_date")} for b in sel],
            "rows": n_rows + ref_rows, "rows_window": n_rows, "rows_reference": ref_rows, "include_reference": include_reference,
            "body": body, "split_note": split_note, "why": why,
            "ready": n_rows >= 200,
            "blocked": None if n_rows >= 200 else "Fewer than 200 labelled rows in the window: wait for labels or widen the window.",
            "launch": {"method": "POST", "endpoint": f"/api/registry/versions/{vid}/monitoring/retrain",
                       "body": {"window_months": months, "include_reference": include_reference, "actor": "<you>"}},
            "note": "Nothing launches by itself: the run starts on POST (a click), and the new model goes through "
                    "registration, review and the stage change like any other."}


@router.get("/registry/versions/{vid}/monitoring/retrain")
async def get_retrain(vid: str, request: Request, window_months: int | None = None, include_reference: bool = False) -> dict[str, Any]:
    if window_months is not None and not 1 <= window_months <= 36:
        raise HTTPException(422, "window_months must be 1 … 36.")
    return await run_in_threadpool(_proposal, request, vid, window_months, include_reference)


class RetrainBody(BaseModel):
    actor: str
    window_months: int | None = Field(None, ge=1, le=36)
    include_reference: bool = False
    time_limit: float | None = Field(None, gt=0, description="Override the champion's time limit (seconds).")


@router.post("/registry/versions/{vid}/monitoring/retrain", status_code=201)
async def launch_retrain(vid: str, body: RetrainBody, request: Request) -> dict[str, Any]:
    """Launch the proposal: a dataset of the window's labelled rows (lineage: the batches and label files), then a run
    with the champion's config on it. Returns the dataset and the run."""
    from ..engine.api import RunCreate, create_run

    actor = (body.actor or "").strip()
    if not actor:
        raise HTTPException(400, "actor is required (the launch is audited).")
    prop = await run_in_threadpool(_proposal, request, vid, body.window_months, body.include_reference)
    if not prop["ready"]:
        raise HTTPException(409, prop["blocked"])
    v = _version(request, vid)
    run = request.app.state.runs.get(v["run_id"])
    ds = await run_in_threadpool(_window_dataset, request, v, run, prop, actor)
    rb = {**prop["body"], "dataset_id": ds["id"], "parent_run_id": run["id"], "source": "monitoring",
          "name": f"retrain {v['model_name']} on {prop['window']['start'][:7]} … {prop['window']['end'][:7]}"}
    if body.time_limit:
        rb["time_limit"] = body.time_limit
    rb = {k: val for k, val in rb.items() if val is not None or k in ("time_limit",)}
    new = await create_run(RunCreate(**rb), request)
    _reg(request).audit(actor=actor, action="retrain_launched", version=v, run_id=new["id"],
                        comment=f"{prop['rows']:,} labelled rows, {prop['window']['start']} … {prop['window']['end']} → {new['id']}",
                        details={"dataset_id": ds["id"], "run_id": new["id"], "batches": [b["batch_id"] for b in prop["batches"]],
                                 "why": prop["why"], "split": prop["split_note"]})
    return {"proposal": prop, "dataset": {"id": ds["id"], "rows": ds["rows"], "name": ds["name"]},
            "run": {"id": new["id"], "status": new.get("status")}}


def _window_dataset(request: Request, v: dict[str, Any], run: dict[str, Any], prop: dict[str, Any], actor: str) -> dict[str, Any]:
    reg = _reg(request)
    label = run["label"]
    frames = []
    for pb in prop["batches"]:
        b = reg.get_batch(pb["batch_id"])
        bdir = _dir(request) / "batches" / b["id"]
        x = pd.read_csv(bdir / "input.csv")
        lab = pd.read_csv(bdir / "labels.csv")
        x = x.iloc[lab["row"].to_numpy()].copy()
        x[label] = lab["y"].to_numpy()
        x["_source_batch"] = b["id"]
        frames.append(x)
    df = pd.concat(frames, ignore_index=True)
    if prop["include_reference"]:
        ref = request.app.state.datasets.load_frame(run["dataset_id"])
        ref["_source_batch"] = "reference"
        df = pd.concat([ref, df], ignore_index=True)
    src = df.pop("_source_batch")
    store = request.app.state.datasets
    stem = f"{v['model_name']}_window_{prop['window']['start'][:7]}_{prop['window']['end'][:7]}"
    rec = store.create_from_upload(f"{stem}.csv", df.to_csv(index=False).encode(), target=label)
    lineage = {"kind": "monitoring-window", "parent": run["dataset_id"], "root": run["dataset_id"],
               "champion_version_id": v["id"], "champion_run_id": run["id"], "window": prop["window"],
               "batches": prop["batches"], "include_reference": prop["include_reference"], "created_by": actor,
               "rows_by_source": {str(k): int(n) for k, n in src.value_counts().items()},
               "changes": [f"{len(df):,} rows: the labelled rows of {len(prop['batches'])} scored batches"
                           + (" and the champion's training data" if prop["include_reference"] else "")]}
    d = store.dir(rec["id"])
    (d / "lineage.json").write_text(json.dumps(lineage))
    prof = dict(rec["profile"])
    prof["lineage"] = {k: val for k, val in lineage.items() if k not in ("changes",)}
    with request.app.state.db.connect() as con:
        con.execute("UPDATE datasets SET profile = ?, note = ? WHERE id = ?",
                    (json.dumps(prof), f"Monitoring window of {_label(v)} · {prop['window']['start']} … {prop['window']['end']}", rec["id"]))
    return request.app.state.db.get_dataset(rec["id"])


# --------------------------------------------------------------------------- the simulated stream


@router.get("/monitoring/stream")
def stream_spec(request: Request) -> dict[str, Any]:
    """The simulated 12-month stream behind the loan-stream sample: months, label arrival dates, what drifts when."""
    sc = request.app.state.datasets.sidecar(STREAM_DATASET, "stream_truth.json")
    if not sc:
        raise HTTPException(404, "The loan-stream sample is missing (make reset regenerates the samples).")
    return {"dataset": STREAM_DATASET, **sc["stream"], "id_column": sc["id_column"], "date_column": sc["date_column"]}


class StreamFeed(BaseModel):
    month: int = Field(..., ge=1, le=12)
    actor: str
    deliver_labels: bool = Field(True, description="Also attach every label file due by the end of this month.")


@router.post("/registry/versions/{vid}/monitoring/stream", status_code=202)
async def feed_stream(vid: str, body: StreamFeed, request: Request) -> dict[str, Any]:
    """Feed month m of the simulated stream to this version: the month's applications are scored as a batch dated the
    month's last day; then each earlier month's labels whose arrival date (end of month + 3) is on or before that day
    are attached to its batch. Labels of a batch still scoring are delivered on the next feed."""
    from ..samples_f18 import label_delivery, stream_month
    from .api import submit_batch

    v = _version(request, vid)
    if v["status"] != "ready":
        raise HTTPException(409, f"Version {vid} is {v['status']}.")
    run = request.app.state.runs.get(v["run_id"]) or {}
    if run.get("dataset_id") != STREAM_DATASET:
        raise HTTPException(400, f"The stream feeds versions trained on the {STREAM_DATASET} sample.")
    actor = (body.actor or "").strip()
    if not actor:
        raise HTTPException(400, "actor is required.")
    reg = _reg(request)
    mine = {b["filename"]: b for b in reg.list_batches(vid)}
    fname = f"stream_{_month_label(body.month)}_m{body.month:02d}.csv"
    if fname in mine:
        raise HTTPException(409, f"Month {body.month} was already fed to this version ({mine[fname]['id']}).")
    x, _, info = await run_in_threadpool(stream_month, body.month, config.SEED)
    b = submit_batch(request, v, x.to_csv(index=False).encode(), fname, actor, info["end"])
    delivered, pending = [], []
    if body.deliver_labels:
        asof = date.fromisoformat(info["end"])
        for m in range(1, body.month):
            fn = f"stream_{_month_label(m)}_m{m:02d}.csv"
            pb = mine.get(fn)
            if not pb or label_delivery(m) > asof:
                continue
            if (pb.get("result") or {}).get("labels"):
                continue
            if pb["status"] != "succeeded":
                pending.append({"month": m, "batch_id": pb["id"], "status": pb["status"]})
                continue
            _, lab, linfo = await run_in_threadpool(stream_month, m, config.SEED)
            r = await attach(request, pb, lab.to_csv(index=False).encode(), actor, "application_id", "defaulted",
                             linfo["labels_arrive"])
            delivered.append({"month": m, "batch_id": pb["id"], "label_date": linfo["labels_arrive"],
                              "lag_days": r["labels"].get("lag_days"), "n": r["labels"]["n_joined"]})
    return {"month": body.month, "period": info["label"], "batch": b, "data_date": info["end"],
            "labels_delivered": delivered, "labels_pending": pending,
            "labels_due": {"month": body.month, "arrive": info["labels_arrive"]}}


def _month_label(m: int) -> str:
    from ..samples_f18 import _month

    return _month(m)[0].strftime("%Y-%m")
