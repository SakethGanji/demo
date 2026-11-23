"""Routes for forecasting runs (TimeSeriesPredictor). The run itself goes through the same job runner, events and SSE
as a tabular run (`GET /api/runs/{id}` and `/api/runs/{id}/events` work on it)."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Literal

import pandas as pd
from fastapi import APIRouter, File, HTTPException, Request, UploadFile
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import FileResponse
from ..authz import body, path, public, require# P19
from pydantic import BaseModel, Field

from . import core
from ..http import dataset_or_404, stash_upload
from .. import frames

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
    num_val_windows: int | None = Field(None, ge=1, le=20, description="Backtest windows W; None = auto (AutoGluon's "
                                        "recommendation with a floor of 3, capped by the shortest series).")
    ensemble: Literal["greedy", "quantile", "both"] = Field(
        "both", description="greedy = AutoGluon's WeightedEnsemble on eval_metric; quantile = LinearStackerEnsemble with "
                            "a weight per model and quantile fitted by pinball loss; both = fit both, AutoGluon keeps "
                            "the better on validation. fit_kwargs.ensemble_hyperparameters, if given, wins.")
    conformal: bool = Field(True, description="Recalibrate the best model's quantiles from its backtest forecasts "
                                              "(split-conformal scaling per quantile level). Raw and calibrated are both kept.")
    conformal_scope: Literal["pooled", "item"] = Field("pooled", description="One c_τ for all series, or per series "
                                                                            "(with ≥ 60 backtest points; pooled elsewhere).")
    hyperparameters: dict[str, Any] | str | None = None
    fit_kwargs: dict[str, Any] = Field(default_factory=dict, description="Passed to TimeSeriesPredictor.fit unchanged.")
    predictor_kwargs: dict[str, Any] = Field(default_factory=dict, description="Passed to TimeSeriesPredictor(...).")
    random_seed: int = 123
    source: str | None = None


class PreviewRequest(BaseModel):
    dataset_id: str
    mapping: Mapping


@router.get("/options", dependencies=[public("forecast presets and metrics; no data")])
def options() -> dict[str, Any]:
    return {"presets": [{"key": k, "info": core.PRESET_INFO[k]} for k in core.PRESETS],
            "metrics": [{"key": k, "formula": f, "quantile": k in core.QUANTILE_METRICS} for k, f in core.METRICS.items()],
            "defaults": {"prediction_length": 28, "eval_metric": "MASE", "presets": "medium_quality", "time_limit": 300,
                         "quantile_levels": core.DEFAULT_QUANTILES, "num_val_windows": None, "ensemble": "both",
                         "conformal": True, "conformal_scope": "pooled"},
            "ensembles": [{"key": k, "label": v["label"], "info": v["info"], "ensemble_hyperparameters": v["hp"]}
                          for k, v in core.ENSEMBLES.items()],
            "num_val_windows_auto": "W = min(5 if fewer than 20 series else 3, ⌊(n_min − 1)/H⌋ − 2), at least 1",
            "families": ["baseline", "statistical", "tabular", "deep", "foundation", "ensemble"]}


def suggest_mapping(request: Request, dataset_id: str) -> dict[str, Any]:
    ds = dataset_or_404(request, dataset_id)
    cols = ds["profile"]["columns"]
    df = frames.read_version(dataset_id, rows=range(0, 500))
    ts = next((c["name"] for c in cols if re.search(r"date|time|day|month|week|period", c["name"], re.I)
               and pd.to_datetime(df[c["name"]], errors="coerce").notna().mean() > 0.9), None)
    if ts is None:
        ts = next((c["name"] for c in cols if c["type"] != "number"
                   and pd.to_datetime(df[c["name"]], errors="coerce").notna().mean() > 0.9), None)
    nums = [c for c in cols if c["type"] == "number" and c["name"] != ts]
    cov_rx = r"holiday|promo|payday|event|open|price|flag|^is_|^has_"
    cands = [c for c in nums if not re.search(cov_rx, c["name"], re.I)]
    target = next((c["name"] for c in cands if re.search(r"volume|amount|sales|count|value|target|demand|txn|transaction|qty|quantity|revenue|balance",
                                                          c["name"], re.I)),
                  max(cands, key=lambda c: c.get("unique", 0))["name"] if cands else None)
    items = [c for c in cols if c["type"] in ("category", "id") and c["name"] != ts and 1 < c.get("unique", 0) <= max(2, ds["rows"] // 8)]
    item = items[0]["name"] if items else None
    statics, known = [], []
    if item:
        g = df.groupby(item)
        statics = [c["name"] for c in cols if c["name"] not in (item, ts, target) and c["type"] == "category"
                   and (g[c["name"]].nunique() <= 1).all()]
    known = [c["name"] for c in nums if c["name"] != target and re.search(cov_rx, c["name"], re.I)]
    return {"mapping": {"item_id": item, "timestamp": ts, "target": target, "known_covariates": known,
                        "static_features": statics, "past_covariates": []}, "source": "inferred from column names and types",
            "prediction_length": None}


@router.get("/mapping/{dataset_id}", dependencies=[require("data.preview", path("dataset_id", "dataset"))])
def mapping(dataset_id: str, request: Request) -> dict[str, Any]:
    return suggest_mapping(request, dataset_id)


@router.post("/preview", dependencies=[require("data.read_meta", body("dataset_id", "dataset"))])
async def preview(body: PreviewRequest, request: Request) -> dict[str, Any]:
    dataset_or_404(request, body.dataset_id)
    try:
        return await run_in_threadpool(core.preview, frames.input_ref_for(body.dataset_id),
                                       body.mapping.model_dump())
    except core.ForecastError as e:
        raise HTTPException(400, str(e)) from e
    except Exception as e:  # noqa: BLE001
        raise HTTPException(400, f"{type(e).__name__}: {e}") from e


@router.post("/runs", status_code=201, dependencies=[require("run.train", body("dataset_id", "dataset"))])
async def create(body: ForecastCreate, request: Request) -> dict[str, Any]:
    return await run_in_threadpool(_create, body, request)


def _create(body: ForecastCreate, request: Request) -> dict[str, Any]:
    from ..engine.api import alloc_run, display_name, queue_run, run_out

    app = request.app
    ds = dataset_or_404(request, body.dataset_id)
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
    settings = app.state.settings
    run_id, run_dir = alloc_run(app)
    cfg = {**body.model_dump(exclude={"name"}), "quantile_levels": q, "kind": "forecast", "label": m.target,
           "run_id": run_id, "db_path": str(settings.db_path), "csv_path": str(frames.version_file(body.dataset_id)),
           "input_ref": frames.input_ref_for(body.dataset_id), "run_dir": str(run_dir), "predictor_path": str(run_dir / "predictor"), "drop_columns": []}
    name = body.name or f"{display_name(ds)} · forecast {m.target} · H={body.prediction_length}"
    store = queue_run(app, run_id, run_dir, name, body.dataset_id, m.target, cfg, kind="forecast", dataset_id=body.dataset_id,
                      label=m.target, prediction_length=body.prediction_length, eval_metric=body.eval_metric,
                      presets=body.presets, time_limit=body.time_limit)
    return run_out(store.get(run_id), store)


def _run(request: Request, run_id: str) -> dict[str, Any]:
    rec = request.app.state.runs.get(run_id)
    if not rec or rec["config"].get("kind") != "forecast":
        raise HTTPException(404, f"No forecast run with id '{run_id}'.")
    return rec


@router.get("/runs/{run_id}/chart", dependencies=[require("run.read", path("run_id", "run"))])
def chart(run_id: str, request: Request) -> dict[str, Any]:
    rec = _run(request, run_id)
    p = Path(rec["config"]["run_dir"]) / "chart.json"
    if rec["status"] != "succeeded" or not p.exists():
        raise HTTPException(409, f"Run {run_id} is {rec['status']}; the forecast is ready when it succeeds.")
    return {"run_id": run_id, **json.loads(p.read_text())}


@router.get("/runs/{run_id}/forecast.csv", dependencies=[require("run.read", path("run_id", "run"))])
def download(run_id: str, request: Request, which: str = "future"):
    rec = _run(request, run_id)
    # which = future | holdout (recalibrated when the run recalibrated) | future_raw | holdout_raw (AutoGluon's quantiles)
    name = {"holdout": "holdout_forecast.csv", "future_raw": "forecast_raw.csv",
            "holdout_raw": "holdout_forecast_raw.csv"}.get(which, "forecast.csv")
    p = Path(rec["config"]["run_dir"]) / name
    if not p.exists():
        raise HTTPException(409, "No forecast yet.")
    return _masked_csv(request, rec, p, f"{run_id}_{name}")


@router.post("/runs/{run_id}/predict", dependencies=[require("run.use", path("run_id", "run"))])
async def predict_new(run_id: str, request: Request, file: UploadFile = File(...)) -> dict[str, Any]:
    """Forecast from a new history file (same columns as the training data): the saved predictor, no refit."""
    rec = _run(request, run_id)
    if rec["status"] != "succeeded":
        raise HTTPException(409, f"Run {run_id} is {rec['status']}.")
    pid, d = await stash_upload(rec, file, "predictions", "fc_")
    try:
        out = await request.app.state.heavy.run(core.predict_new, rec["config"]["predictor_path"], rec["config"],
                                                str(d / "input.csv"), str(d / "forecast.csv"))
    except core.ForecastError as e:
        raise HTTPException(400, str(e)) from e
    except Exception as e:  # noqa: BLE001
        raise HTTPException(400, f"{type(e).__name__}: {e}") from e
    return {"run_id": run_id, "id": pid, **out, "download": f"/api/forecast/runs/{run_id}/predictions/{pid}.csv"}


@router.get("/runs/{run_id}/predictions/{pid}.csv", dependencies=[require("run.read", path("run_id", "run"))])
def download_prediction(run_id: str, pid: str, request: Request):
    rec = _run(request, run_id)
    if not re.fullmatch(r"fc_[0-9a-f]{8}", pid):
        raise HTTPException(404, "No such forecast.")
    p = Path(rec["config"]["run_dir"]) / "predictions" / pid / "forecast.csv"
    if not p.exists():
        raise HTTPException(404, "No such forecast.")
    return _masked_csv(request, rec, p, f"{run_id}_{pid}.csv")


def _masked_csv(request: Request, rec: dict[str, Any], p: Path, filename: str):
    """U3 (F-X4a hand-off): a forecast CSV names the series `item_id` and the time `timestamp`; when the run's mapped
    item / time column is PII-tagged, that column is masked in the file (`lake.mask.batch_mask` + `csv_stream`, as batch
    downloads). `?unmasked=1&reason=…` is data.export_unmasked on the run's input, audited."""
    import dataclasses

    from fastapi.responses import StreamingResponse

    from ..lake import mask as M

    q = request.query_params
    m = M.batch_mask(request, rec, unmasked=q.get("unmasked", ""), reason=q.get("reason", ""), by=q.get("by", ""),
                     export=True, read="forecast_download")
    mp = (rec.get("config") or {}).get("mapping") or {}
    cols = {out: m.cols[src] for out, src in (("item_id", mp.get("item_id")), ("timestamp", mp.get("timestamp")))
            if m is not None and src in m.cols} if m is not None else {}
    if not cols:
        return FileResponse(p, media_type="text/csv", filename=filename)
    fm = dataclasses.replace(m, cols=cols)
    return StreamingResponse(M.csv_stream(p, fm), media_type="text/csv",
                             headers={"Content-Disposition": f'attachment; filename="{filename}"',
                                      "X-Ensemble-Masked-Columns": ",".join(sorted(cols))})
