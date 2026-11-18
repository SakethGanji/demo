"""Routes for forecasting runs (TimeSeriesPredictor). The run itself goes through the same job runner, events and SSE
as a tabular run (`GET /api/runs/{id}` and `/api/runs/{id}/events` work on it)."""

from __future__ import annotations

import json
import re
import secrets
from pathlib import Path
from typing import Any

import pandas as pd
from fastapi import APIRouter, File, HTTPException, Request, UploadFile
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from .. import config
from ..engine.store import now
from . import core

router = APIRouter(prefix="/api/forecast")


class Mapping(BaseModel):
    item_id: str | None = Field(None, description="Column naming the series; none = one series.")
    timestamp: str
    target: str
    known_covariates: list[str] = Field(default_factory=list)
    static_features: list[str] = Field(default_factory=list)
    past_covariates: list[str] = Field(default_factory=list)


class ForecastCreate(BaseModel):
    dataset_id: str
    mapping: Mapping
    name: str | None = None
    prediction_length: int = Field(28, ge=1, le=10_000)
    freq: str | None = Field(None, description="Pandas offset alias; None infers it.")
    eval_metric: str = "MASE"
    eval_metric_seasonal_period: int | None = Field(None, ge=1)
    presets: str | None = "medium_quality"
    time_limit: float | None = Field(300, gt=0)
    quantile_levels: list[float] = Field(default_factory=lambda: list(core.DEFAULT_QUANTILES))
    num_val_windows: int | None = Field(2, ge=1, le=20)
    hyperparameters: dict[str, Any] | str | None = None
    fit_kwargs: dict[str, Any] = Field(default_factory=dict, description="Passed to TimeSeriesPredictor.fit unchanged.")
    predictor_kwargs: dict[str, Any] = Field(default_factory=dict, description="Passed to TimeSeriesPredictor(...).")
    random_seed: int = 123
    source: str | None = None


class PreviewRequest(BaseModel):
    dataset_id: str
    mapping: Mapping


def _ds(request: Request, dataset_id: str) -> dict[str, Any]:
    ds = request.app.state.db.get_dataset(dataset_id)
    if not ds:
        raise HTTPException(404, f"No dataset with id '{dataset_id}'.")
    return ds


@router.get("/options")
def options() -> dict[str, Any]:
    return {"presets": [{"key": k, "info": core.PRESET_INFO[k]} for k in core.PRESETS],
            "metrics": [{"key": k, "formula": f, "quantile": k in core.QUANTILE_METRICS} for k, f in core.METRICS.items()],
            "defaults": {"prediction_length": 28, "eval_metric": "MASE", "presets": "medium_quality", "time_limit": 300,
                         "quantile_levels": core.DEFAULT_QUANTILES, "num_val_windows": 2},
            "families": ["baseline", "statistical", "tabular", "deep", "foundation", "ensemble"]}


def suggest_mapping(request: Request, dataset_id: str) -> dict[str, Any]:
    ds = _ds(request, dataset_id)
    spec = request.app.state.datasets.sidecar(dataset_id, "forecast_spec.json")
    if spec and spec.get("forecast"):
        f = spec["forecast"]
        return {"mapping": {k: f.get(k) for k in ("item_id", "timestamp", "target", "known_covariates", "static_features")}
                | {"past_covariates": []}, "source": "sample spec",
                "prediction_length": f.get("suggested_prediction_length", 28)}
    cols = ds["profile"]["columns"]
    df = request.app.state.datasets.load_frame(dataset_id).head(500)
    ts = next((c["name"] for c in cols if re.search(r"date|time|day|month|week|period", c["name"], re.I)
               and pd.to_datetime(df[c["name"]], errors="coerce").notna().mean() > 0.9), None)
    if ts is None:
        ts = next((c["name"] for c in cols if c["type"] != "number"
                   and pd.to_datetime(df[c["name"]], errors="coerce").notna().mean() > 0.9), None)
    nums = [c for c in cols if c["type"] == "number" and c["name"] != ts]
    target = next((c["name"] for c in nums if re.search(r"volume|amount|sales|count|value|target|demand|y$", c["name"], re.I)),
                  nums[0]["name"] if nums else None)
    items = [c for c in cols if c["type"] in ("category", "id") and c["name"] != ts and 1 < c.get("unique", 0) <= max(2, ds["rows"] // 8)]
    item = items[0]["name"] if items else None
    statics, known = [], []
    if item:
        g = df.groupby(item)
        statics = [c["name"] for c in cols if c["name"] not in (item, ts, target) and c["type"] == "category"
                   and (g[c["name"]].nunique() <= 1).all()]
    known = [c["name"] for c in nums if c["name"] != target and re.search(r"holiday|promo|payday|event|open|price", c["name"], re.I)]
    return {"mapping": {"item_id": item, "timestamp": ts, "target": target, "known_covariates": known,
                        "static_features": statics, "past_covariates": []}, "source": "inferred from column names and types",
            "prediction_length": None}


@router.get("/mapping/{dataset_id}")
def mapping(dataset_id: str, request: Request) -> dict[str, Any]:
    return suggest_mapping(request, dataset_id)


@router.post("/preview")
async def preview(body: PreviewRequest, request: Request) -> dict[str, Any]:
    _ds(request, body.dataset_id)
    try:
        return await run_in_threadpool(core.preview, str(request.app.state.datasets.csv_path(body.dataset_id)),
                                       body.mapping.model_dump())
    except core.ForecastError as e:
        raise HTTPException(400, str(e)) from e
    except Exception as e:  # noqa: BLE001
        raise HTTPException(400, f"{type(e).__name__}: {e}") from e


@router.post("/runs", status_code=201)
async def create(body: ForecastCreate, request: Request) -> dict[str, Any]:
    return await run_in_threadpool(_create, body, request)


def _create(body: ForecastCreate, request: Request) -> dict[str, Any]:
    from ..engine.api import run_out

    app = request.app
    ds = _ds(request, body.dataset_id)
    cols = {c["name"] for c in ds["profile"]["columns"]}
    m = body.mapping
    for f in [m.timestamp, m.target, *([m.item_id] if m.item_id else []), *m.known_covariates, *m.static_features, *m.past_covariates]:
        if f not in cols:
            raise HTTPException(400, f"mapping: no column named '{f}'.")
    if body.eval_metric not in core.METRICS:
        raise HTTPException(400, f"eval_metric: one of {', '.join(core.METRICS)}.")
    if body.presets and body.presets not in core.PRESETS and body.presets not in ("medium", "high", "best", "fast"):
        raise HTTPException(400, f"presets: one of {', '.join(core.PRESETS)}.")
    q = sorted(set(float(x) for x in body.quantile_levels))
    if not q or any(not 0 < x < 1 for x in q):
        raise HTTPException(400, "quantile_levels: values strictly between 0 and 1.")
    for k in ("train_data", "path"):
        if k in body.fit_kwargs or k in body.predictor_kwargs:
            raise HTTPException(400, f"{k} is set by the run.")
    run_id = "run_" + secrets.token_hex(4)
    settings = app.state.settings
    run_dir = settings.runs_dir / run_id
    run_dir.mkdir(parents=True)
    cfg = {**body.model_dump(exclude={"name"}), "quantile_levels": q, "kind": "forecast", "label": m.target,
           "run_id": run_id, "db_path": str(settings.db_path), "csv_path": str(app.state.datasets.csv_path(body.dataset_id)),
           "run_dir": str(run_dir), "predictor_path": str(run_dir / "predictor"), "drop_columns": []}
    (run_dir / "config.json").write_text(json.dumps(cfg, indent=1))
    name = body.name or f"{ds['name'].replace(' (sample)', '')} · forecast {m.target} · H={body.prediction_length}"
    store = app.state.runs
    store.create({"id": run_id, "name": name, "dataset_id": body.dataset_id, "label": m.target, "created_at": now(), "config": cfg})
    store.emit(run_id, "run_queued", kind="forecast", dataset_id=body.dataset_id, label=m.target,
               prediction_length=body.prediction_length, eval_metric=body.eval_metric, presets=body.presets,
               time_limit=body.time_limit)
    app.state.runner.submit(run_id)
    return run_out(store.get(run_id), store)


def _run(request: Request, run_id: str) -> dict[str, Any]:
    rec = request.app.state.runs.get(run_id)
    if not rec or rec["config"].get("kind") != "forecast":
        raise HTTPException(404, f"No forecast run with id '{run_id}'.")
    return rec


@router.get("/runs/{run_id}/chart")
def chart(run_id: str, request: Request) -> dict[str, Any]:
    rec = _run(request, run_id)
    p = Path(rec["config"]["run_dir"]) / "chart.json"
    if rec["status"] != "succeeded" or not p.exists():
        raise HTTPException(409, f"Run {run_id} is {rec['status']}; the forecast is ready when it succeeds.")
    return {"run_id": run_id, **json.loads(p.read_text())}


@router.get("/runs/{run_id}/forecast.csv")
def download(run_id: str, request: Request, which: str = "future"):
    rec = _run(request, run_id)
    name = "holdout_forecast.csv" if which == "holdout" else "forecast.csv"
    p = Path(rec["config"]["run_dir"]) / name
    if not p.exists():
        raise HTTPException(409, "No forecast yet.")
    return FileResponse(p, media_type="text/csv", filename=f"{run_id}_{name}")


@router.post("/runs/{run_id}/predict")
async def predict_new(run_id: str, request: Request, file: UploadFile = File(...)) -> dict[str, Any]:
    """Forecast from a new history file (same columns as the training data): the saved predictor, no refit."""
    rec = _run(request, run_id)
    if rec["status"] != "succeeded":
        raise HTTPException(409, f"Run {run_id} is {rec['status']}.")
    content = await file.read(config.MAX_UPLOAD_BYTES + 1)
    if not content.strip():
        raise HTTPException(400, "The file is empty.")
    pid = "fc_" + secrets.token_hex(4)
    d = Path(rec["config"]["run_dir"]) / "predictions" / pid
    d.mkdir(parents=True)
    (d / "input.csv").write_bytes(content)
    try:
        out = await request.app.state.heavy.run(core.predict_new, rec["config"]["predictor_path"], rec["config"],
                                                str(d / "input.csv"), str(d / "forecast.csv"))
    except core.ForecastError as e:
        raise HTTPException(400, str(e)) from e
    except Exception as e:  # noqa: BLE001
        raise HTTPException(400, f"{type(e).__name__}: {e}") from e
    return {"run_id": run_id, "id": pid, **out, "download": f"/api/forecast/runs/{run_id}/predictions/{pid}.csv"}


@router.get("/runs/{run_id}/predictions/{pid}.csv")
def download_prediction(run_id: str, pid: str, request: Request):
    rec = _run(request, run_id)
    if not re.fullmatch(r"fc_[0-9a-f]{8}", pid):
        raise HTTPException(404, "No such forecast.")
    p = Path(rec["config"]["run_dir"]) / "predictions" / pid / "forecast.csv"
    if not p.exists():
        raise HTTPException(404, "No such forecast.")
    return FileResponse(p, media_type="text/csv", filename=f"{run_id}_{pid}.csv")
