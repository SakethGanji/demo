"""Routes for runs, their events (SSE with replay), predictions and the model catalogue."""

from __future__ import annotations

import asyncio
import json
import secrets
import shutil
from pathlib import Path
from importlib import metadata
from typing import Any, Literal

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.concurrency import run_in_threadpool
from pydantic import BaseModel, Field
from sse_starlette.sse import EventSourceResponse

from ..splits import load_splits
from . import catalogue, serving
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
                "feature_importance", "leaderboard", "resolved"):
        out[key] = res.get(key)
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
def create_run(body: RunCreate, request: Request) -> dict[str, Any]:
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
    text_cols = [c["name"] for c in profile["columns"] if c["type"] == "text" and c["name"] not in drop and c["name"] != label]
    if label == profile.get("target"):
        task = profile.get("task")
    else:
        task = "regression" if cols[label]["type"] == "number" and cols[label]["unique"] > 50 else None

    run_id = "run_" + secrets.token_hex(4)
    settings = app.state.settings
    run_dir = settings.runs_dir / run_id
    run_dir.mkdir(parents=True, exist_ok=False)
    cfg = {
        **body.model_dump(exclude={"label", "name", "drop_columns"}),
        "problem_type": None if body.problem_type == "auto" else body.problem_type,
        "label": label, "drop_columns": drop, "text_columns": text_cols, "task": task,
        "run_id": run_id, "db_path": str(settings.db_path), "csv_path": str(app.state.datasets.csv_path(body.dataset_id)),
        "splits_path": str(app.state.datasets.splits_path(body.dataset_id)), "run_dir": str(run_dir),
        "predictor_path": str(run_dir / "predictor"),
    }
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
        p = await run_in_threadpool(request.app.state.predictors.get, rec["config"]["predictor_path"])
        out = await run_in_threadpool(serving.predict, p, body.rows, body.model)
    except serving.ServingError as e:
        raise HTTPException(400, str(e)) from e
    return {"run_id": run_id, **out}


@router.get("/runs/{run_id}/oof")
async def out_of_fold(run_id: str, request: Request, model: str | None = None) -> dict[str, Any]:
    rec = _get_run(request, run_id)
    _require_succeeded(rec)
    try:
        p = await run_in_threadpool(request.app.state.predictors.get, rec["config"]["predictor_path"])
        out = await run_in_threadpool(serving.oof, p, model)
    except serving.ServingError as e:
        raise HTTPException(400, str(e)) from e
    return {"run_id": run_id, **out}


@router.get("/runs/{run_id}/feature-importance")
async def feature_importance(run_id: str, request: Request) -> dict[str, Any]:
    """Permutation importance on the holdout: stored if the run computed it, else computed now and stored."""
    rec = _get_run(request, run_id)
    _require_succeeded(rec)
    fi = (rec.get("result") or {}).get("feature_importance")
    if fi is None:
        cfg = rec["config"]

        def compute():
            p = request.app.state.predictors.get(cfg["predictor_path"])
            h = serving.holdout_frame(cfg["csv_path"], load_splits(Path(cfg["splits_path"])),
                                      cfg["label"], cfg["drop_columns"])
            return serving.feature_importance(p, h)

        fi = await run_in_threadpool(compute)
        _store(request).merge_result(run_id, feature_importance=fi)
    return {"run_id": run_id, "features": fi}
