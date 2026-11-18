"""Routes for runs, their events (SSE with replay), predictions and the model catalogue."""

from __future__ import annotations

import asyncio
import json
import os
import secrets
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path
from importlib import metadata
from typing import Any, Literal

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.concurrency import run_in_threadpool
from pydantic import BaseModel, Field
from sse_starlette.sse import EventSourceResponse

from .. import config
from ..splits import load_splits
from . import catalogue, explain, heavy, serving, threshold
from .runner import JobRunner
from .store import TERMINAL_EVENTS, RunStore, now

router = APIRouter(prefix="/api")

ProblemType = Literal["auto", "binary", "multiclass", "regression", "quantile"]

# Leaderboard columns kept in GET /api/runs (the list); GET /api/runs/{id} returns every column.
LIST_LB_COLS = ("model", "family", "rank", "score_val", "score_test", "score_val_natural", "score_test_natural",
                "fit_time", "pred_time_val", "pred_time_test", "stack_level", "can_infer", "val_score_std",
                "fold_scores", "num_models", "weights")


class RunCreate(BaseModel):
    dataset_id: str
    label: str | None = Field(None, description="Label column; defaults to the dataset's target.")
    name: str | None = None
    problem_type: ProblemType = "auto"
    eval_metric: str | None = Field(None, description="Any AutoGluon metric name; None lets AutoGluon choose.")
    presets: str | list[str] | None = "medium_quality"
    time_limit: float | None = Field(120, gt=0, description="Seconds; None means no limit.")
    fit_kwargs: dict[str, Any] = Field(default_factory=dict, description="Passed to TabularPredictor.fit unchanged.")
    predictor_kwargs: dict[str, Any] = Field(default_factory=dict, description="Passed to TabularPredictor(...).")
    custom_text_models: bool = Field(True, description="Add TFIDF_LR and TFIDF_SVM when the data has a text column.")
    drop_columns: list[str] | None = Field(None, description="Columns left out of training; default: ID columns.")
    feature_importance: bool = Field(True, description="Compute permutation importance on the holdout after the fit.")
    column_types: dict[str, Literal["number", "category", "text"]] | None = Field(
        None, description="Override inferred column types; passed to AutoGluon as feature_metadata.")
    calibrate_threshold: str | None = Field(
        None, description="Binary only: after the fit, predictor.calibrate_decision_threshold(metric=...) and keep it. "
                          "A metric name (f1, balanced_accuracy, mcc, …) or 'auto' (f1 when the minority class < 20%). "
                          "Ignored when fit_kwargs.calibrate_decision_threshold is given.")
    pseudolabel: dict[str, Any] | None = Field(
        None, description="Pseudo-labelling with unlabeled rows: {dataset_id, method: fit_pseudolabel | unlabeled_data, "
                          "max_iter=3}. fit_pseudolabel runs predictor.fit_pseudolabel(pseudo_data=...) after the fit; "
                          "unlabeled_data passes the rows to fit(unlabeled_data=...).")
    distill: dict[str, Any] | None = Field(
        None, description="After the fit, predictor.distill(**distill) (time_limit defaults to half the budget).")
    parent_run_id: str | None = Field(None, description="The run this one was derived from (Gym, Vela patch).")
    patch: dict[str, Any] | None = Field(None, description="The Vela patch applied to the parent's config, if any.")
    source: str | None = Field(None, description="Where the run was started: wizard, gym, client, vela.")


class EnsembleRequest(BaseModel):
    models: list[str] = Field(..., min_length=1, description="Base models the weighted ensemble may use.")


class PredictRequest(BaseModel):
    rows: list[dict[str, Any]]
    model: str | None = None


def _runner(request: Request) -> JobRunner:
    return request.app.state.runner


def _store(request: Request) -> RunStore:
    return request.app.state.runs


def run_out(rec: dict[str, Any], store: RunStore, full: bool = True) -> dict[str, Any]:
    res = rec.get("result") or {}
    out = {
        "id": rec["id"], "name": rec["name"], "dataset_id": rec["dataset_id"], "label": rec["label"],
        "status": rec["status"], "created_at": rec["created_at"], "started_at": rec["started_at"],
        "finished_at": rec["finished_at"], "error": rec["error"], "config": rec["config"],
        "predictor_path": rec["config"].get("predictor_path"), "n_events": store.count_events(rec["id"]),
    }
    for key in ("problem_type", "eval_metric", "classes", "positive_class", "best_model", "fit_seconds",
                "elapsed_seconds", "data", "features", "ensemble", "ensembles", "holdout", "oof", "text_models",
                "feature_importance", "leaderboard", "resolved", "custom_ensembles", "threshold", "mlflow"):
        out[key] = res.get(key)
    out["feature_importance"] = explain.importance_with_ci(out["feature_importance"])
    if not full:
        out["leaderboard"] = [{k: r.get(k) for k in LIST_LB_COLS} for r in (out["leaderboard"] or [])] or out["leaderboard"]
        out["feature_importance"] = None
        out["resolved"] = None
        out["text_models"] = None
    return out


def _get_run(request: Request, run_id: str) -> dict[str, Any]:
    rec = _store(request).get(run_id)
    if not rec:
        raise HTTPException(404, f"No run with id '{run_id}'.")
    return rec


@router.get("/models")
def models() -> dict[str, Any]:
    return {"families": catalogue.catalogue(), "engine": engine_info()}


def engine_info() -> dict[str, Any]:
    def v(p: str) -> str | None:
        try:
            return metadata.version(p)
        except metadata.PackageNotFoundError:
            return None

    return {"name": "AutoGluon", "version": v("autogluon.tabular"), "timeseries": v("autogluon.timeseries")}


@router.post("/runs", status_code=201)
async def create_run(body: RunCreate, request: Request) -> dict[str, Any]:
    from ..gym.api import check_run_body

    app = request.app
    await check_run_body(request, body.model_dump())
    return await run_in_threadpool(_create_run, body, request)


def _create_run(body: RunCreate, request: Request) -> dict[str, Any]:
    app = request.app
    ds = app.state.db.get_dataset(body.dataset_id)
    if not ds:
        raise HTTPException(404, f"No dataset with id '{body.dataset_id}'.")
    profile = ds["profile"]
    cols = {c["name"]: c for c in profile["columns"]}
    label = body.label or ds["target"]
    if not label:
        raise HTTPException(400, "This dataset has no label column; say which column to predict (label).")
    if label not in cols:
        raise HTTPException(400, f"Column '{label}' is not in the dataset.")
    if cols[label]["unique"] < 2:
        raise HTTPException(400, f"Column '{label}' has fewer than two distinct values; there is nothing to learn.")
    for k in ("label", "path"):
        if k in body.predictor_kwargs:
            raise HTTPException(400, f"predictor_kwargs.{k} is set by the run; use the run's '{k}' field instead."
                                if k == "label" else "predictor_kwargs.path is set by the run (its own folder).")
    for k in ("train_data",):
        if k in body.fit_kwargs:
            raise HTTPException(400, "fit_kwargs.train_data is set by the run (the dataset minus its holdout).")
    drop = body.drop_columns if body.drop_columns is not None else \
        [c["name"] for c in profile["columns"] if c["type"] == "id" and c["name"] != label]
    unknown = [c for c in drop if c not in cols]
    if unknown:
        raise HTTPException(400, f"drop_columns: no column named {', '.join(map(repr, unknown))}.")
    if label in drop:
        raise HTTPException(400, "The label column can't be dropped.")
    types = body.column_types or {}
    bad = [c for c in types if c not in cols]
    if bad:
        raise HTTPException(400, f"column_types: no column named {', '.join(map(repr, bad))}.")
    if label in types:
        raise HTTPException(400, "column_types: the label's type is set by problem_type, not column_types.")
    if types and "feature_metadata" in body.fit_kwargs:
        raise HTTPException(400, "Give column_types or fit_kwargs.feature_metadata, not both.")
    if body.calibrate_threshold not in (None, "auto", *threshold.CALIBRATION_METRICS):
        raise HTTPException(400, f"calibrate_threshold: use 'auto' or one of {', '.join(threshold.CALIBRATION_METRICS)}.")
    text_cols = [c["name"] for c in profile["columns"] if types.get(c["name"], c["type"]) == "text"
                 and c["name"] not in drop and c["name"] != label]
    if label == profile.get("target"):
        task = profile.get("task")
    else:
        task = "regression" if cols[label]["type"] == "number" and cols[label]["unique"] > 50 else None

    run_id = "run_" + secrets.token_hex(4)
    settings = app.state.settings
    run_dir = settings.runs_dir / run_id
    run_dir.mkdir(parents=True, exist_ok=False)
    cfg = {
        **body.model_dump(exclude={"label", "name", "drop_columns", "column_types"}),
        "column_types": {c: t for c, t in types.items() if c not in drop},
        "problem_type": None if body.problem_type == "auto" else body.problem_type,
        "label": label, "drop_columns": drop, "text_columns": text_cols, "task": task,
        "run_id": run_id, "db_path": str(settings.db_path), "csv_path": str(app.state.datasets.csv_path(body.dataset_id)),
        "splits_path": str(app.state.datasets.splits_path(body.dataset_id)), "run_dir": str(run_dir),
        "predictor_path": str(run_dir / "predictor"),
    }
    if body.pseudolabel:
        cfg["unlabeled_csv_path"] = str(app.state.datasets.csv_path(body.pseudolabel["dataset_id"]))
    (run_dir / "config.json").write_text(json.dumps(cfg, indent=1))
    name = body.name or f"{ds['name'].replace(' (sample)', '')} · {label}"
    store = _store(request)
    store.create({"id": run_id, "name": name, "dataset_id": body.dataset_id, "label": label,
                  "created_at": now(), "config": cfg})
    store.emit(run_id, "run_queued", dataset_id=body.dataset_id, label=label, problem_type=cfg["problem_type"],
               eval_metric=body.eval_metric, presets=body.presets, time_limit=body.time_limit)
    _runner(request).submit(run_id)
    return run_out(store.get(run_id), store)


@router.get("/runs")
def list_runs(request: Request, dataset_id: str | None = None, full: bool = False) -> dict[str, Any]:
    store = _store(request)
    recs = [r for r in store.list() if dataset_id is None or r["dataset_id"] == dataset_id]
    return {"runs": [run_out(r, store, full=full) for r in recs]}


@router.get("/runs/{run_id}")
def get_run(run_id: str, request: Request) -> dict[str, Any]:
    return run_out(_get_run(request, run_id), _store(request))


@router.post("/runs/{run_id}/cancel")
def cancel_run(run_id: str, request: Request) -> dict[str, Any]:
    rec = _get_run(request, run_id)
    if not _runner(request).cancel(run_id):
        raise HTTPException(409, f"Run {run_id} has already finished ({rec['status']}).")
    return {"id": run_id, "cancelling": True}


@router.delete("/runs/{run_id}", status_code=204)
def delete_run(run_id: str, request: Request) -> None:
    rec = _get_run(request, run_id)
    if rec["status"] in ("queued", "running"):
        raise HTTPException(409, "Cancel the run before deleting it.")
    with request.app.state.db.connect() as con:
        con.execute("DELETE FROM run_events WHERE run_id = ?", (run_id,))
        con.execute("DELETE FROM runs WHERE id = ?", (run_id,))
    shutil.rmtree(request.app.state.settings.runs_dir / run_id, ignore_errors=True)


@router.get("/runs/{run_id}/events")
async def run_events(run_id: str, request: Request, start: int = Query(0, alias="from", ge=0),
                     format: Literal["sse", "json"] = "sse"):
    """Every event of the run from `from` (default 0). SSE by default: `id` is the event's seq, `event` its type,
    `data` the JSON payload; a reconnect with Last-Event-ID resumes after it. The stream ends after the terminal
    event (run_succeeded, run_failed or run_cancelled). `format=json` returns what is there now, once."""
    _get_run(request, run_id)
    store = _store(request)
    if format == "json":
        evs = await run_in_threadpool(store.events, run_id, start, 100_000)
        return {"events": evs, "next": (evs[-1]["seq"] + 1) if evs else start}
    last_id = request.headers.get("last-event-id")
    if last_id is not None and last_id.strip().lstrip("-").isdigit():
        start = max(start, int(last_id) + 1)

    async def gen():
        cursor = start
        idle = 0
        while True:
            if await request.is_disconnected():
                return
            evs = await run_in_threadpool(store.events, run_id, cursor, 500)
            for e in evs:
                yield {"id": str(e["seq"]), "event": e["type"], "data": json.dumps(e)}
                cursor = e["seq"] + 1
                if e["type"] in TERMINAL_EVENTS:
                    return
            if not evs:
                idle += 1
                # Asked to start past the terminal event: nothing more will come.
                if idle % 8 == 1 and await run_in_threadpool(store.has_terminal_event, run_id):
                    rec = await run_in_threadpool(store.get, run_id)
                    if rec and rec["status"] not in ("queued", "running"):
                        return
                await asyncio.sleep(0.25)
            else:
                idle = 0

    return EventSourceResponse(gen(), ping=15, headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


def _require_succeeded(rec: dict[str, Any]) -> None:
    if rec["status"] != "succeeded":
        raise HTTPException(409, f"Run {rec['id']} is {rec['status']}; it needs to finish first.")


@router.post("/runs/{run_id}/predict")
async def predict(run_id: str, body: PredictRequest, request: Request) -> dict[str, Any]:
    rec = _get_run(request, run_id)
    _require_succeeded(rec)
    if len(body.rows) > 10_000:
        raise HTTPException(413, "At most 10,000 rows per request.")
    try:
        out = await request.app.state.heavy.run(heavy.predict_rows, rec["config"]["predictor_path"], body.rows, body.model)
    except serving.ServingError as e:
        raise HTTPException(400, str(e)) from e
    return {"run_id": run_id, **out}


@router.get("/runs/{run_id}/oof")
async def out_of_fold(run_id: str, request: Request, model: str | None = None) -> dict[str, Any]:
    rec = _get_run(request, run_id)
    _require_succeeded(rec)
    try:
        out = await request.app.state.heavy.run(heavy.out_of_fold, rec["config"]["predictor_path"], model)
    except serving.ServingError as e:
        raise HTTPException(400, str(e)) from e
    return {"run_id": run_id, **out}


@router.get("/runs/{run_id}/feature-importance")
async def feature_importance(run_id: str, request: Request,
                             shuffles: int | None = Query(None, ge=2, le=50)) -> dict[str, Any]:
    """Permutation importance on the holdout: stored if the run computed it, else computed now and stored.
    `shuffles` recomputes it with that many shuffle sets (tighter intervals). Each feature has a 95% t-interval
    importance ± t₀.₉₇₅,ₙ₋₁·s/√n over the n shuffles."""
    rec = _get_run(request, run_id)
    _require_succeeded(rec)
    res = rec.get("result") or {}
    fi = res.get("feature_importance")
    meta = res.get("feature_importance_meta") or {"num_shuffle_sets": 3, "subsample_size": None}
    if fi is None or (shuffles is not None and shuffles != meta.get("num_shuffle_sets")):
        cfg = rec["config"]
        n_sets = shuffles or 5

        # In a worker process: permutation importance re-predicts the holdout many times (CPU-bound).
        fi, sub = await request.app.state.heavy.run(heavy.permutation_importance, cfg, n_sets)
        meta = {"num_shuffle_sets": n_sets, "subsample_size": sub}
        _store(request).merge_result(run_id, feature_importance=fi, feature_importance_meta=meta)
    return {"run_id": run_id, "features": explain.importance_with_ci(fi), "meta": meta,
            "method": "permutation importance on the holdout: score drop when one feature's values are shuffled "
                      "(AutoGluon's predictor.feature_importance)", "eval_metric": (res.get("eval_metric") or {}).get("name")}


@router.get("/runs/{run_id}/holdout-predictions")
async def holdout_predictions(run_id: str, request: Request) -> dict[str, Any]:
    """The best model's predictions on the holdout rows: data.csv row, true label, prediction and (classification)
    one probability per class. Residuals and curves are computed from these."""
    rec = _get_run(request, run_id)
    _require_succeeded(rec)
    path = Path(rec["config"]["run_dir"]) / "holdout_predictions.csv"
    if not path.exists():
        raise HTTPException(404, "This run has no saved holdout predictions.")

    def read():
        import pandas as pd

        from .jsonsafe import jsonable

        df = pd.read_csv(path)
        prob_cols = [c for c in df.columns if c.startswith("p_")]
        return {"run_id": run_id, "model": (rec.get("result") or {}).get("best_model"), "n_rows": len(df),
                "rows": jsonable(df["row"].tolist()), "y_true": jsonable(df["y_true"].tolist()),
                "y_pred": jsonable(df["y_pred"].tolist()),
                "classes": [c[2:] for c in prob_cols] or None,
                "probabilities": jsonable(df[prob_cols].to_numpy()) if prob_cols else None}

    return await run_in_threadpool(read)


_ensemble_locks: dict[str, threading.Lock] = {}
_ensemble_locks_guard = threading.Lock()


def _fit_custom_ensemble(app, rec: dict[str, Any], models: list[str]) -> dict[str, Any]:
    """Run app.engine.custom_ensemble in its own process (it writes into the predictor folder), one per run at a time."""
    run_id = rec["id"]
    with _ensemble_locks_guard:
        lock = _ensemble_locks.setdefault(run_id, threading.Lock())
    with lock:
        store: RunStore = app.state.runs
        rec = store.get(run_id)
        existing = (rec.get("result") or {}).get("custom_ensembles") or []
        n = len(existing) + 1
        run_dir = Path(rec["config"]["run_dir"])
        req_dir = run_dir / "custom_ensembles"
        req_dir.mkdir(exist_ok=True)
        req_path = req_dir / f"request_{n}.json"
        req_path.write_text(json.dumps({"models": models, "suffix": f"_Custom{n}"}))
        env = {**os.environ, "PYTHONUNBUFFERED": "1", "MLFLOW_DISABLE_AGENT_HINT": "1",
               "PYTHONPATH": os.pathsep.join(p for p in (str(config.BACKEND_DIR), os.environ.get("PYTHONPATH", "")) if p)}
        try:
            p = subprocess.run([sys.executable, "-m", "app.engine.custom_ensemble", str(run_dir), str(req_path)],
                               cwd=config.BACKEND_DIR, env=env, capture_output=True, text=True, timeout=600)
        except subprocess.TimeoutExpired as e:
            raise HTTPException(504, "Fitting the ensemble took longer than 10 minutes; it was stopped.") from e
        (req_dir / f"log_{n}.txt").write_text((p.stdout or "") + "\n--- stderr ---\n" + (p.stderr or ""))
        lines = [ln for ln in (p.stdout or "").splitlines() if ln.startswith("{")]
        if not lines:
            raise HTTPException(500, f"The ensemble process exited with code {p.returncode}: {(p.stderr or '')[-800:]}")
        out = json.loads(lines[-1])
        if not out["ok"]:
            raise HTTPException(400, out["error"])
        res = out["result"]
        app.state.predictors.drop(rec["config"]["predictor_path"])

        s = res["scores"]
        lb = list((rec.get("result") or {}).get("leaderboard") or [])
        lb.append({"model": res["model"], "family": "ENS_WEIGHTED", "score_val": s["score_val"], "score_test": s["score_test"],
                   "score_val_natural": s["score_val_natural"], "score_test_natural": s["score_test_natural"],
                   "fit_time": s["fit_time"], "pred_time_test": s["pred_time_test"], "pred_time_val": None,
                   "stack_level": s["stack_level"], "can_infer": True, "fold_scores": None, "val_score_std": None,
                   "weights": res["weights"], "custom": True, "model_type": "WeightedEnsembleModel",
                   "predict_model": res.get("predict_model")})
        lb.sort(key=lambda r: -(r.get("score_val") if r.get("score_val") is not None else float("-inf")))
        for i, r in enumerate(lb, start=1):
            r["rank"] = i
        store.merge_result(run_id, custom_ensembles=existing + [res], leaderboard=lb)
        return res


@router.post("/runs/{run_id}/ensemble", status_code=201)
async def custom_ensemble(run_id: str, body: EnsembleRequest, request: Request) -> dict[str, Any]:
    """Fit a weighted ensemble on the models you choose: `TabularPredictor.fit_weighted_ensemble(base_models=...)`.
    The result has its weights, its validation and holdout scores, and whether it beats the best single model."""
    rec = _get_run(request, run_id)
    _require_succeeded(rec)
    models = list(dict.fromkeys(body.models))
    names = {r["model"] for r in (rec.get("result") or {}).get("leaderboard") or []}
    unknown = [m for m in models if m not in names]
    if unknown:
        raise HTTPException(400, f"No model named {', '.join(map(repr, unknown))} in this run.")
    ens = [m for m in models if m.startswith("WeightedEnsemble")]
    if ens:
        raise HTTPException(400, f"Choose base models, not weighted ensembles ({', '.join(ens)}).")
    full = [m for m in models if m.endswith("_FULL")]
    if full:
        raise HTTPException(400, f"Refit models ({', '.join(full)}) have no validation predictions to weight; "
                                 "choose the models they were refit from.")
    res = await run_in_threadpool(_fit_custom_ensemble, request.app, rec, models)
    return {"run_id": run_id, **res}


# --------------------------------------------------------------------------- understand (S4)


def _holdout_df(rec: dict[str, Any]):
    try:
        return explain.load_holdout(Path(rec["config"]["run_dir"]))
    except FileNotFoundError as e:
        raise HTTPException(404, str(e)) from e


def _require_binary(rec: dict[str, Any]) -> dict[str, Any]:
    res = rec.get("result") or {}
    if res.get("problem_type") != "binary":
        raise HTTPException(400, f"A decision threshold applies to binary classification; this run is {res.get('problem_type')}.")
    return res


def _current_threshold(res: dict[str, Any]) -> float:
    return float((res.get("threshold") or {}).get("value", 0.5))


@router.get("/runs/{run_id}/threshold")
async def get_threshold(run_id: str, request: Request, cost_fp: float = Query(1.0, ge=0), cost_fn: float = Query(1.0, ge=0),
                        t: float | None = Query(None, ge=0, le=1)) -> dict[str, Any]:
    """Binary runs: precision, recall, F1, balanced accuracy, MCC, the confusion counts and the cost
    C(t) = c_FP·FP + c_FN·FN on the holdout at every t on a 0.005 grid (and at `t` exactly), the thresholds that
    maximise F1 / balanced accuracy / MCC and minimise cost, and the threshold the predictor uses now."""
    rec = _get_run(request, run_id)
    _require_succeeded(rec)
    res = _require_binary(rec)
    df = _holdout_df(rec)
    out = await run_in_threadpool(explain.threshold_analysis, df, res["positive_class"], current=_current_threshold(res),
                                  cost_fp=cost_fp, cost_fn=cost_fn, at=t)
    return {"run_id": run_id, "model": res.get("best_model"), "threshold": res.get("threshold"), **out}


class ThresholdRequest(BaseModel):
    metric: str | None = Field(None, description="Calibrate: predictor.calibrate_decision_threshold(metric=metric).")
    value: float | None = Field(None, ge=0, le=1, description="Or set this threshold directly.")


@router.post("/runs/{run_id}/threshold")
async def set_threshold(run_id: str, body: ThresholdRequest, request: Request) -> dict[str, Any]:
    """Calibrate (`metric`) or set (`value`) the decision threshold and persist it in the predictor
    (`set_decision_threshold` + `save`), so every later prediction uses it. The holdout is re-scored at it."""
    rec = _get_run(request, run_id)
    _require_succeeded(rec)
    _require_binary(rec)
    if (body.metric is None) == (body.value is None):
        raise HTTPException(400, "Give either metric (to calibrate) or value (to set the threshold).")
    if body.metric is not None and body.metric not in threshold.CALIBRATION_METRICS:
        raise HTTPException(400, f"Calibrate for one of {', '.join(threshold.CALIBRATION_METRICS)}.")
    cfg = rec["config"]
    app = request.app

    def work():
        with _run_lock(run_id):
            p = app.state.predictors.get(cfg["predictor_path"])
            h = serving.holdout_frame(cfg["csv_path"], load_splits(Path(cfg["splits_path"])), cfg["label"], cfg["drop_columns"])
            t0 = time.time()
            if body.metric is not None:
                value, source = threshold.calibrate(p, body.metric), "calibrated"
            else:
                value, source = float(body.value), "manual"
            out = threshold.apply(p, Path(cfg["run_dir"]), h, cfg["label"], value, source=source, metric=body.metric,
                                  seconds=time.time() - t0)
            res_now = (_store(request).get(run_id) or {}).get("result") or {}
            extra = {}
            if "fit_threshold" not in res_now:  # the threshold the run's leaderboard was scored at (export compares it)
                extra["fit_threshold"] = res_now.get("threshold") or {"source": "default", "value": 0.5, "metric": None}
            _store(request).merge_result(run_id, threshold=out["threshold"], holdout=out["holdout"], **extra)
            return out

    try:
        out = await run_in_threadpool(work)
    except (ValueError, AssertionError) as e:
        raise HTTPException(400, str(e)) from e
    return {"run_id": run_id, **out}


@router.get("/runs/{run_id}/explain")
async def explain_run(run_id: str, request: Request) -> dict[str, Any]:
    """Classification: confusion matrix, per-class precision/recall/F1, ROC and PR curves (one-vs-rest for
    multiclass) with AUC and average precision, the reliability curve with the Brier score and ECE, and the worst
    mistakes with their features. Regression: residuals against predictions, their histogram, RMSE/MAE/R² and the
    largest errors. Plus the columns that can define groups for fairness."""
    rec = _get_run(request, run_id)
    _require_succeeded(rec)
    res = rec.get("result") or {}
    df = _holdout_df(rec)
    cfg = rec["config"]
    ds = request.app.state.db.get_dataset(rec["dataset_id"])

    def build():
        if res.get("problem_type") in ("binary", "multiclass"):
            body = explain.classification_report(df, [str(c) for c in explain._classes(df)], res.get("positive_class"), cfg)
        else:
            body = explain.regression_report(df, cfg)
        return body

    body = await run_in_threadpool(build)
    return {"run_id": run_id, "model": res.get("best_model"), "problem_type": res.get("problem_type"),
            "eval_metric": res.get("eval_metric"), "label": rec["label"], "threshold": res.get("threshold"),
            "group_candidates": explain.group_candidates(cfg, res.get("features") or [], ds["profile"] if ds else None),
            "text_columns": cfg.get("text_columns") or [], **body}


@router.get("/runs/{run_id}/fairness")
async def fairness(run_id: str, request: Request, group: str = Query(..., min_length=1),
                   t: float | None = Query(None, ge=0, le=1)) -> dict[str, Any]:
    """fairlearn's MetricFrame on the holdout, one row per value of `group` (numbers with more than 8 values are cut
    into quartiles). Binary: selection rate, base rate, accuracy, TPR, FPR, precision per group at the run's threshold
    (or `t`), with the demographic parity difference and the equalised odds difference."""
    rec = _get_run(request, run_id)
    _require_succeeded(rec)
    res = rec.get("result") or {}
    df = _holdout_df(rec)
    pt = res.get("problem_type")
    thr_value = t if t is not None else (_current_threshold(res) if pt == "binary" else None)
    try:
        out = await run_in_threadpool(explain.fairness, df, rec["config"], group, problem_type=pt,
                                      positive=res.get("positive_class"), threshold=thr_value)
    except explain.ExplainError as e:
        raise HTTPException(400, str(e)) from e
    except ValueError as e:
        raise HTTPException(400, f"No column named {group!r} in the dataset." if "not in list" in str(e) or "Usecols" in str(e) else str(e)) from e
    reports = dict(res.get("fairness_reports") or {})
    reports[group] = {"gaps": out.get("gaps"), "threshold": thr_value, "computed_at": now(),
                      "binning": out.get("binning"), "by_group": (out.get("by_group") or [])[:20]}
    _store(request).merge_result(run_id, fairness_reports=reports)
    return {"run_id": run_id, "model": res.get("best_model"), **out}


@router.get("/runs/{run_id}/shap")
async def shap_values(run_id: str, request: Request, row: int = Query(..., ge=0), target: str | None = None) -> dict[str, Any]:
    """Kernel SHAP for one holdout row (data.csv row number) on the best model: f(x) = φ₀ + Σᵢ φᵢ, where f is
    predict_proba of `target` (default: the positive class, or the predicted class for multiclass) or the predicted
    value (regression), φ₀ = E[f] over a fixed sample of 50 training rows. Cached per row."""
    rec = _get_run(request, run_id)
    _require_succeeded(rec)
    cfg = rec["config"]
    app = request.app

    try:
        # In a worker process (Kernel SHAP evaluates thousands of coalitions); cached per row in the run folder.
        out = await app.state.heavy.run(heavy.shap_holdout_row, cfg, row, target)
    except explain.ExplainError as e:
        raise HTTPException(400, str(e)) from e
    return {"run_id": run_id, **out}


def _run_lock(run_id: str) -> threading.Lock:
    with _ensemble_locks_guard:
        return _ensemble_locks.setdefault(run_id, threading.Lock())


@router.get("/status")
def status(request: Request) -> dict[str, Any]:
    """What the status bar shows in live mode: the machine (CPU count, memory), versions, the active run and its
    progress (models started and finished, best validation score so far, elapsed against the budget) and the queue."""
    from .. import __version__

    store = _store(request)
    active = store.list_active()
    running = [r for r in active if r["status"] == "running"]
    cur = None
    if running:
        r = running[0]
        with request.app.state.db.connect() as con:
            rows = con.execute("SELECT type, data FROM run_events WHERE run_id = ? AND type IN "
                               "('fit_started', 'model_started', 'model_finished', 'model_failed', 'ensemble', "
                               "'leaderboard', 'evaluation', 'calibrating', 'explaining') ORDER BY seq", (r["id"],)).fetchall()
        started = finished = failed = 0
        best = None
        stage = "starting"
        greater = True
        for typ, data in rows:
            d = json.loads(data)
            if d.get("phase") == "dystack":
                continue
            stage = typ
            if typ == "fit_started":
                greater = bool(d.get("greater_is_better", True))
            elif typ == "model_started":
                started += 1
            elif typ == "model_finished":
                finished += 1
                v = d.get("val_score_natural")
                if v is not None and (best is None or (v > best if greater else v < best)):
                    best = v
            elif typ == "model_failed":
                failed += 1
        cfg = r["config"]
        t0 = r.get("started_at")
        cur = {"id": r["id"], "name": r["name"], "status": r["status"], "started_at": t0,
               "time_limit": cfg.get("time_limit"), "presets": cfg.get("presets"), "eval_metric": cfg.get("eval_metric"),
               "models_started": started, "models_finished": finished, "models_failed": failed, "best_val": best,
               "stage": stage}
    mem = None
    try:
        import psutil

        mem = round(psutil.virtual_memory().total / 2**30, 1)
    except Exception:  # noqa: BLE001
        pass
    import platform

    return {"version": __version__, "engine": engine_info(), "cpu_count": os.cpu_count(), "memory_gb": mem,
            "python": platform.python_version(), "host": platform.node(), "active": cur,
            "queued": sum(1 for r in active if r["status"] == "queued"), "runs": store.count(),
            "registry": request.app.state.registry.counts(), "pool": request.app.state.heavy.info(),
            "max_parallel_runs": getattr(request.app.state.runner, "max_parallel", None)}
