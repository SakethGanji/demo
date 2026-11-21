"""Routes for anomaly-detection runs (IsolationForest, optional LOF). Same runner, events and SSE as other runs."""

from __future__ import annotations

import json
import re
import threading
from pathlib import Path
from typing import Any, Literal

import numpy as np
import pandas as pd
from fastapi import APIRouter, File, HTTPException, Query, Request, UploadFile
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from ..engine.store import now
from . import core, group, labels as review, velocity
from ..http import dataset_or_404, stash_upload

router = APIRouter(prefix="/api/anomaly")


class AnomalyCreate(BaseModel):
    dataset_id: str
    name: str | None = None
    features: list[str] | None = Field(None, description="Columns to use; default: every non-ID, non-text column.")
    drop_columns: list[str] | None = None
    contamination: float = Field(0.015, gt=0, lt=0.5, description="Expected share of anomalies: sets the threshold.")
    n_estimators: int = Field(300, ge=10, le=5000)
    max_samples: int | float | str | None = Field(None, description="ψ, rows per tree. None: min(1024, n) (F2: on the "
                                                  "card sample ψ = 1024 beat sklearn's 'auto' = 256 at every seed tried).")
    max_features: float = Field(1.0, gt=0, le=1)
    bootstrap: bool = False
    random_state: int = 0
    lof: bool = Field(True, description="Also fit LocalOutlierFactor (novelty=True) on robust-scaled features.")
    lof_neighbors: int = Field(20, ge=2, le=500)
    iforest_kwargs: dict[str, Any] = Field(default_factory=dict, description="Passed to IsolationForest(...).")
    group_features: bool | None = Field(None, description="Group-relative features (x ÷ the entity's mean, z within the "
                                        "entity, time since its previous row). None: on when an entity column is mapped.")
    entity_column: str | None = Field(None, description="The entity (card, account, customer); default: the sample's spec.")
    time_column: str | None = None
    relative_columns: list[str] | None = Field(None, description="Numeric columns compared within the entity.")
    velocity_features: bool | None = Field(None, description="Per-entity rolling features from earlier rows only: counts "
                                           "and sums in the last 1 h / 24 h, distinct categories in 24 h, amount ÷ the "
                                           "running mean, the gap to the previous row. None: on when entity and time "
                                           "columns are mapped.")
    amount_column: str | None = Field(None, description="The amount for velocity sums; default: the first relative column.")
    category_column: str | None = Field(None, description="Merchant / category column for distinct counts; default: suggested.")
    velocity_in_forest: list[str] | None = Field(None, description="Velocity columns the forest sees; default: "
                                                 "txns_prev_1h, txns_prev_24h, log_hours_since_prev (all are computed).")
    source: str | None = None


@router.post("/runs", status_code=201)
async def create(body: AnomalyCreate, request: Request) -> dict[str, Any]:
    return await run_in_threadpool(_create, body, request)


def _create(body: AnomalyCreate, request: Request) -> dict[str, Any]:
    from ..engine.api import alloc_run, display_name, queue_run, run_out

    app = request.app
    ds = dataset_or_404(request, body.dataset_id)
    cols = {c["name"]: c for c in ds["profile"]["columns"]}
    for c in (body.features or []) + (body.drop_columns or []):
        if c not in cols:
            raise HTTPException(400, f"No column named '{c}'.")
    for c in [body.entity_column, body.time_column, body.amount_column, body.category_column, *(body.relative_columns or [])]:
        if c and c not in cols:
            raise HTTPException(400, f"No column named '{c}'.")
    if body.entity_column is None and body.group_features is not False:
        spec = app.state.datasets.sidecar(body.dataset_id, "anomaly_truth.json") or {}
        if spec.get("entity_column"):
            body.entity_column, body.time_column = spec["entity_column"], spec.get("time_column")
            body.relative_columns = body.relative_columns or spec.get("relative_columns")
    if body.group_features is None:
        body.group_features = bool(body.entity_column)
    if body.group_features:
        if not body.entity_column:
            raise HTTPException(400, "group_features needs an entity_column.")
        if not body.relative_columns:
            body.relative_columns = [c for c, v in cols.items() if v["type"] == "number"
                                     and any(h in c.lower() for h in group.AMOUNT_HINT)][:3]
    if body.velocity_features is None:
        body.velocity_features = bool(body.group_features and body.entity_column and body.time_column)
    if body.velocity_features:
        if not (body.group_features and body.entity_column and body.time_column):
            raise HTTPException(400, "velocity_features needs group features with an entity_column and a time_column.")
        body.amount_column = body.amount_column or (body.relative_columns or [None])[0]
        if not body.amount_column:
            raise HTTPException(400, "velocity_features needs an amount_column (a numeric column).")
        if body.category_column is None:
            df_head = pd.read_csv(app.state.datasets.csv_path(body.dataset_id), nrows=20000)
            body.category_column = velocity.suggest_category(
                df_head, {c: v["type"] for c, v in cols.items()}, {body.entity_column, body.time_column})
        bad = [c for c in body.velocity_in_forest or [] if c not in velocity.names(body.model_dump())]
        if bad:
            raise HTTPException(400, f"velocity_in_forest: unknown column(s) {', '.join(bad)}; "
                                     f"choose from {', '.join(velocity.names(body.model_dump()))}.")
    settings = app.state.settings
    run_id, run_dir = alloc_run(app)
    truth = (app.state.datasets.sidecar(body.dataset_id, "anomaly_truth.json") or {}).get("anomalies")
    params = body.model_dump(exclude={"dataset_id", "name", "features", "drop_columns", "source"})
    cfg = {"kind": "anomaly", "dataset_id": body.dataset_id, "features": body.features, "drop_columns": body.drop_columns or [],
           "params": params, "label": "anomaly", "source": body.source,
           "id_columns": [c for c, v in cols.items() if v["type"] == "id"],
           "profile": {"columns": [{"name": c["name"], "type": c["type"]} for c in ds["profile"]["columns"]]},
           "truth": truth, "run_id": run_id, "db_path": str(settings.db_path),
           "csv_path": str(app.state.datasets.csv_path(body.dataset_id)), "run_dir": str(run_dir),
           "predictor_path": str(run_dir / "model")}
    name = body.name or f"{display_name(ds)} · anomalies · c={body.contamination:g}"
    store = queue_run(app, run_id, run_dir, name, body.dataset_id, "anomaly", cfg, kind="anomaly",
                      dataset_id=body.dataset_id, contamination=body.contamination)
    out = run_out(store.get(run_id), store)
    out["config"] = {k: v for k, v in cfg.items() if k != "truth"}
    return out


@router.get("/mapping/{dataset_id}")
def mapping(dataset_id: str, request: Request) -> dict[str, Any]:
    """Suggested entity, time and relative columns for group-relative features: the sample's spec, else names and
    types (a repeated ID-like column, a datetime column, amount-like numbers)."""
    ds = dataset_or_404(request, dataset_id)
    spec = request.app.state.datasets.sidecar(dataset_id, "anomaly_truth.json")
    df = pd.read_csv(request.app.state.datasets.csv_path(dataset_id), nrows=20000)
    types = {c["name"]: c["type"] for c in ds["profile"]["columns"]}
    out = group.suggest(df, types, spec)
    out["amount_column"] = (out.get("relative_columns") or [None])[0]
    out["category_column"] = velocity.suggest_category(df, types, {out.get("entity_column"), out.get("time_column")})
    out["velocity_columns"] = velocity.names(out) if out.get("amount_column") else []
    out["velocity_in_forest"] = velocity.default_in_forest(out)
    out["windows_hours"] = dict(velocity.WINDOWS)
    return out


def _run(request: Request, run_id: str, done: bool = True) -> dict[str, Any]:
    rec = request.app.state.runs.get(run_id)
    if not rec or rec["config"].get("kind") != "anomaly":
        raise HTTPException(404, f"No anomaly run with id '{run_id}'.")
    if done and rec["status"] != "succeeded":
        raise HTTPException(409, f"Run {run_id} is {rec['status']}.")
    return rec


@router.get("/runs/{run_id}/top")
def top(run_id: str, request: Request, contamination: float | None = Query(None, gt=0, lt=0.5),
        limit: int = Query(100, ge=1, le=1000), by: str = "") -> dict[str, Any]:
    """The most anomalous rows with their reasons; with `contamination`, the threshold and the flags at that share
    (and, for the sample, precision@k against the hidden ground truth). DL3 (m2): a read of rows, audited (needs a name)."""
    from ..catalog import governance as G

    G.reader(by)
    rec = _run(request, run_id)
    G.run_read_audit(request.app, "anomaly_top", run_id, {"limit": limit}, by=by)
    d = Path(rec["config"]["run_dir"])
    a = (rec.get("result") or {}).get("anomaly") or {}
    rows = json.loads((d / "top.json").read_text())
    scores = pd.read_csv(d / "scores.csv")
    s = scores["s"].to_numpy()
    c = contamination or a.get("contamination")
    thr = float(np.quantile(s, 1 - c))
    n_flag = int((s >= thr).sum())
    for r in rows:
        r["flagged"] = r["s"] >= thr
    truth = rec["config"].get("truth")
    check = None
    if truth:
        check = core.truth_check(scores["row"].to_numpy(), s, truth, n_flag)
        t = set(truth["rows"])
        for r in rows:
            r["planted"] = r["row"] in t
    lab = review.load(request.app.state.datasets.dir(rec["dataset_id"]))
    for r in rows:
        v = lab.get(r["row"])
        r["review"] = None if v is None else {"label": v["label"], "by": v["by"], "at": v["at"]}
    return {"run_id": run_id, "contamination": c, "threshold": thr, "n_flagged": n_flag, "rows": rows[:limit],
            "truth": check, "n_rows": int(len(s)), "review": review.summary(lab)}


# --------------------------------------------------------------------------- review loop (F2)

class ReviewItem(BaseModel):
    row: int = Field(..., ge=0)
    label: Literal["fraud", "not_fraud"] | None = Field(..., description="fraud = confirm, not_fraud = dismiss, null = clear.")
    note: str | None = None


class ReviewBody(BaseModel):
    items: list[ReviewItem] = Field(..., min_length=1)
    by: str = Field("Analyst", min_length=1, description="Who reviewed (goes to the audit log).")


def _labels_out(request: Request, rec: dict[str, Any], cur: dict[int, dict[str, Any]]) -> dict[str, Any]:
    truth = rec["config"].get("truth")
    out = {"dataset_id": rec["dataset_id"], **review.summary(cur),
           "labels": [{"row": r, **v} for r, v in sorted(cur.items())]}
    if truth:  # the sample only: how often the analyst's (smoke's) calls agree with the hidden truth
        t = set(truth["rows"])
        out["truth_agreement"] = {"confirmed_planted": sum(1 for r, v in cur.items() if v["label"] == 1 and r in t),
                                  "dismissed_planted": sum(1 for r, v in cur.items() if v["label"] == 0 and r in t)}
    return out


@router.get("/runs/{run_id}/labels")
def get_labels(run_id: str, request: Request) -> dict[str, Any]:
    """The dataset's reviewed rows (confirmed fraud = 1, dismissed = 0) and whether "Train supervised" is open."""
    rec = _run(request, run_id)
    return _labels_out(request, rec, review.load(request.app.state.datasets.dir(rec["dataset_id"])))


@router.post("/runs/{run_id}/labels")
def set_labels(run_id: str, body: ReviewBody, request: Request) -> dict[str, Any]:
    """Confirm (fraud) or dismiss (not fraud) flagged rows; the labels are stored with the dataset and audited."""
    rec = _run(request, run_id)
    sc = pd.read_csv(Path(rec["config"]["run_dir"]) / "scores.csv")
    n = len(sc)
    bad = [it.row for it in body.items if it.row >= n]
    if bad:
        raise HTTPException(400, f"No row {bad[0]} (the dataset has {n} rows).")
    s = dict(zip(sc["row"].astype(int), sc["s"].astype(float)))
    items = [{"row": it.row, "label": None if it.label is None else int(it.label == "fraud"), "note": it.note}
             for it in body.items]
    cur = review.update(request.app.state.datasets.dir(rec["dataset_id"]), items, by=body.by, run_id=run_id, at=now(),
                        scores={it["row"]: s.get(it["row"]) for it in items})
    try:
        request.app.state.registry.audit(
            actor=body.by, action="anomaly_review", run_id=run_id,
            details={"dataset_id": rec["dataset_id"], "fraud": [i["row"] for i in items if i["label"] == 1],
                     "not_fraud": [i["row"] for i in items if i["label"] == 0],
                     "cleared": [i["row"] for i in items if i["label"] is None]})
    except Exception:  # noqa: BLE001 — the label is stored; the audit entry is best effort
        pass
    return _labels_out(request, rec, cur)


class SupervisedCreate(BaseModel):
    presets: str | list[str] | None = "medium_quality"
    time_limit: float | None = Field(120, gt=0)
    eval_metric: str | None = Field("average_precision", description="Any AutoGluon binary metric.")
    num_bag_folds: int = Field(5, ge=2, le=20, description="Bagging gives out-of-fold scores for every training row.")
    include_score: bool = Field(True, description="Give the isolation score (and LOF) as features.")
    negatives: Literal["unlabeled", "dismissed_only"] = Field(
        "unlabeled", description="unlabeled: every row not confirmed is 0 (positive-unlabeled); dismissed_only: train "
                                 "on reviewed rows only.")
    min_positives: int = Field(review.MIN_POSITIVES, ge=1)
    fit_kwargs: dict[str, Any] = Field(default_factory=dict, description="Passed to TabularPredictor.fit unchanged.")
    by: str = "Analyst"


@router.post("/runs/{run_id}/supervised", status_code=201)
async def create_supervised(run_id: str, body: SupervisedCreate, request: Request) -> dict[str, Any]:
    return await run_in_threadpool(_create_supervised, run_id, body, request)


def _create_supervised(run_id: str, body: SupervisedCreate, request: Request) -> dict[str, Any]:
    """An AutoGluon binary run on the confirmed labels: data.csv + the derived (group, velocity) columns + the
    anomaly score, label `confirmed_fraud`. A new dataset version holds it (stratified holdout and folds on the label)."""
    from ..engine.api import RunCreate, _create_run
    from ..splits import make_splits

    app = request.app
    rec = _run(request, run_id)
    cfg = rec["config"]
    cur = review.load(app.state.datasets.dir(rec["dataset_id"]))
    summ = review.summary(cur, body.min_positives)
    if not summ["can_train"]:
        raise HTTPException(409, f"{summ['confirmed']} confirmed fraud(s); confirm at least {body.min_positives} to train.")
    d = Path(cfg["run_dir"])
    df = pd.read_csv(cfg["csv_path"])
    n = len(df)
    p = cfg["params"]
    derived = d / "derived.csv"
    if derived.exists():
        der = pd.read_csv(derived).drop(columns=["row"])
        for c in der.columns:
            if c not in df.columns:
                df[c] = der[c].to_numpy()
    sc = pd.read_csv(d / "scores.csv")
    if body.include_score:
        df["anomaly_score"] = sc["s"].to_numpy()
        if "lof" in sc:
            df["lof_score"] = sc["lof"].to_numpy()
    y = np.zeros(n, dtype=int)
    reviewed = np.zeros(n, dtype=bool)
    for r, v in cur.items():
        if r < n:
            y[r] = v["label"]
            reviewed[r] = True
    label = "confirmed_fraud"
    df[label] = y
    if body.negatives == "dismissed_only":
        df.loc[~reviewed, label] = np.nan  # rows without a label are left out of training (the engine drops them)
    splits = make_splits(pd.Series(y, name=label), n, 42, 0.2, 5)
    ds = dataset_or_404(request, rec["dataset_id"])
    note = (f"{summ['confirmed']} confirmed, {summ['dismissed']} dismissed, {n - summ['reviewed']} unreviewed "
            f"({'positive-unlabeled: unreviewed = 0' if body.negatives == 'unlabeled' else 'reviewed rows only'})")
    new = app.state.datasets.derive(  # P1: every row kept; a new label column and its own split
        rec["dataset_id"], df, None, "anomaly_review", [], splits=splits, extra={"anomaly_run": run_id, "summary": note},
        name=f"{ds['name'].replace(' (sample)', '')} · confirmed labels", note=note, by=(body.by or "").strip()[:80] or None)
    drop = [c for c in cfg.get("id_columns") or [] if c in df.columns]
    for c in (p.get("entity_column"), p.get("time_column")):
        if c and c in df.columns and c not in drop:
            drop.append(c)
    fit_kwargs = {"num_bag_folds": body.num_bag_folds, **body.fit_kwargs}
    rc = RunCreate(dataset_id=new["id"], label=label, problem_type="binary", eval_metric=body.eval_metric,
                   presets=body.presets, time_limit=body.time_limit, fit_kwargs=fit_kwargs, drop_columns=drop,
                   name=f"{ds['name'].replace(' (sample)', '')} · supervised fraud (PU) from {run_id}",
                   parent_run_id=run_id, source="anomaly-supervised")
    out = _create_run(rc, request)
    entry = {"run_id": out["id"], "dataset_id": new["id"], "created_at": now(), "by": body.by,
             "negatives": body.negatives, "include_score": body.include_score, **summ,
             "unreviewed": int(n - summ["reviewed"]), "reviewed_rows": sorted(int(r) for r in cur if r < n),
             "features_added": [c for c in df.columns if c not in pd.read_csv(cfg["csv_path"], nrows=1).columns and c != label],
             "drop_columns": drop, "fit_kwargs": fit_kwargs, "eval_metric": body.eval_metric}
    with _sup_lock:
        lst = _sup_list(d)
        lst.append(entry)
        (d / "supervised.json").write_text(json.dumps(lst, indent=1))
    try:
        app.state.registry.audit(actor=body.by, action="anomaly_train_supervised", run_id=run_id,
                                 details={"supervised_run": out["id"], "dataset_id": new["id"], **summ})
    except Exception:  # noqa: BLE001
        pass
    return {"anomaly_run_id": run_id, **entry, "run": out}


_sup_lock = threading.Lock()


def _sup_list(d: Path) -> list[dict[str, Any]]:
    p = d / "supervised.json"
    return json.loads(p.read_text()) if p.exists() else []


@router.get("/runs/{run_id}/supervised")
async def list_supervised(run_id: str, request: Request) -> dict[str, Any]:
    """The supervised runs trained from this run's reviews, each (once finished) compared with the isolation forest on
    the rows the analyst did not review: out-of-sample scores only (out-of-fold for training rows, the holdout's own)."""
    rec = _run(request, run_id)
    d = Path(rec["config"]["run_dir"])
    out = []
    for e in _sup_list(d):
        r = request.app.state.runs.get(e["run_id"])
        item = {**{k: v for k, v in e.items() if k != "reviewed_rows"}, "status": r["status"] if r else "deleted",
                "error": r and r.get("error")}
        if r and r["status"] == "succeeded":
            cache = d / f"supervised_{e['run_id']}.json"
            if cache.exists():
                item["comparison"] = json.loads(cache.read_text())
            else:
                try:
                    oof = await request.app.state.heavy.run(_heavy_oof, r["config"]["predictor_path"], r["config"]["csv_path"],
                                                           r["config"]["label"])
                    cmp_ = compare(rec, e, r, oof)
                    cache.write_text(json.dumps(cmp_))
                    item["comparison"] = cmp_
                except Exception as ex:  # noqa: BLE001
                    item["comparison_error"] = f"{type(ex).__name__}: {ex}"
            res = r.get("result") or {}
            item["best_model"] = res.get("best_model")
            item["holdout"] = (res.get("holdout") or {}).get("scores")
        out.append(item)
    cur = review.load(request.app.state.datasets.dir(rec["dataset_id"]))
    return {"run_id": run_id, "review": review.summary(cur), "runs": out}


def _heavy_oof(predictor_path: str, csv_path: str, label: str) -> dict[str, Any]:
    """(In the heavy pool.) Out-of-fold P(1) for the training rows, and predict_proba for every other row (the holdout,
    and with negatives = dismissed_only the unreviewed rows): none of them was training data for its score."""
    from ..engine.heavy import load_predictor
    from ..engine.serving import oof as serving_oof

    pred = load_predictor(predictor_path)
    o = serving_oof(pred, None)
    df = pd.read_csv(csv_path)
    seen = set(o["rows"])
    rest = [i for i in range(len(df)) if i not in seen]
    if rest:
        pr = pred.predict_proba(df.iloc[rest].drop(columns=[label]))
        o["rows"] = list(o["rows"]) + rest
        o["probabilities"] = list(o["probabilities"]) + pr[list(pred.class_labels)].to_numpy().round(6).tolist()
    o["n_oof"] = len(seen)
    return o


def compare(rec: dict[str, Any], entry: dict[str, Any], sup: dict[str, Any], oof: dict[str, Any]) -> dict[str, Any]:
    """Per row an out-of-sample P(confirmed | x): out-of-fold for training rows, the holdout prediction for holdout
    rows. Compared with the isolation score s on the rows nobody reviewed (U), against the hidden truth (sample)."""
    d = Path(rec["config"]["run_dir"])
    sc = pd.read_csv(d / "scores.csv")
    n = len(sc)
    p = np.full(n, np.nan)
    cls = [str(c) for c in oof["classes"]]
    j = cls.index("1") if "1" in cls else len(cls) - 1
    pr = np.asarray(oof["probabilities"], dtype=float)
    p[np.asarray(oof["rows"], dtype=int)] = pr[:, j]
    reviewed = set(entry.get("reviewed_rows") or [])
    u = np.array([i for i in range(n) if i not in reviewed and not np.isnan(p[i])], dtype=int)
    s = sc["s"].to_numpy(dtype=float)
    out: dict[str, Any] = {"model": oof["model"], "rows_scored": int((~np.isnan(p)).sum()), "unreviewed": int(len(u)),
                           "n_oof": int(oof.get("n_oof", 0)),
                           "source": "out-of-fold (bagged) for training rows; predict_proba for rows that were not trained on"}
    k = int(rec["result"]["anomaly"]["n_flagged"]) if rec.get("result") else 90
    top_s, top_p = set(u[np.argsort(-s[u], kind="stable")[:k]]), set(u[np.argsort(-p[u], kind="stable")[:k]])
    out["k"] = k
    out["overlap_top_k"] = len(top_s & top_p)
    truth = rec["config"].get("truth")
    if truth:
        keep = [(r, kd) for r, kd in zip(truth["rows"], truth.get("kinds") or []) if r not in reviewed]
        tu = {"rows": [r for r, _ in keep], "kinds": [kd for _, kd in keep]}
        kk = max(1, len(keep))
        out["hidden_in_unreviewed"] = len(keep)
        out["iforest"] = core.truth_check(u, s[u], tu, kk)
        out["supervised"] = core.truth_check(u, p[u], tu, kk)
        out["iforest_at_k"] = core.truth_check(u, s[u], tu, min(k, len(u)))
        out["supervised_at_k"] = core.truth_check(u, p[u], tu, min(k, len(u)))
        out["note"] = ("sample only: the hidden truth is never given to either model; rows the analyst reviewed are "
                       "left out of the comparison (their labels were training data).")
    return out


@router.get("/runs/{run_id}/scores")
def scores(run_id: str, request: Request, bins: int = Query(60, ge=5, le=400)) -> dict[str, Any]:
    rec = _run(request, run_id)
    s = pd.read_csv(Path(rec["config"]["run_dir"]) / "scores.csv")
    h, e = np.histogram(s["s"], bins=bins)
    out = {"run_id": run_id, "edges": e.tolist(), "counts": h.tolist(), "n": int(len(s)),
           "sorted_sample": np.quantile(s["s"], np.linspace(0, 1, 201)).tolist()}
    if "lof" in s:
        out["scatter"] = s.sample(min(len(s), 1500), random_state=0)[["row", "s", "lof"]].to_dict(orient="records")
    return out


@router.get("/runs/{run_id}/scores.csv")
def scores_csv(run_id: str, request: Request):
    rec = _run(request, run_id)
    return FileResponse(Path(rec["config"]["run_dir"]) / "scores.csv", media_type="text/csv", filename=f"{run_id}_scores.csv")


@router.post("/runs/{run_id}/score")
async def score_batch(run_id: str, request: Request, file: UploadFile = File(...)) -> dict[str, Any]:
    """Score a new batch with the run's model: s(x,n), E[h(x)], the percentile among the fitted rows, the flag at
    the saved threshold, reasons for the top rows; the scored CSV to download."""
    rec = _run(request, run_id)
    bid, d = await stash_upload(rec, file, "batches", "ab_")
    try:
        out = await request.app.state.heavy.run(core.score_batch, rec["config"]["predictor_path"], str(d / "input.csv"),
                                                str(d / "scored.csv"))
    except core.AnomalyError as e:
        raise HTTPException(400, str(e)) from e
    return {"run_id": run_id, "id": bid, "filename": file.filename, **out,
            "download": f"/api/anomaly/runs/{run_id}/batches/{bid}.csv"}


@router.get("/runs/{run_id}/batches/{bid}.csv")
def batch_csv(run_id: str, bid: str, request: Request):
    rec = _run(request, run_id)
    if not re.fullmatch(r"ab_[0-9a-f]{8}", bid):
        raise HTTPException(404, "No such batch.")
    p = Path(rec["config"]["run_dir"]) / "batches" / bid / "scored.csv"
    if not p.exists():
        raise HTTPException(404, "No such batch.")
    return FileResponse(p, media_type="text/csv", filename=f"{run_id}_{bid}_scored.csv")
