"""Routes for anomaly-detection runs (IsolationForest, optional LOF). Same runner, events and SSE as other runs."""

from __future__ import annotations

import json
import re
import secrets
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
from fastapi import APIRouter, File, HTTPException, Query, Request, UploadFile
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from .. import config
from ..engine.store import now
from . import core, group

router = APIRouter(prefix="/api/anomaly")


class AnomalyCreate(BaseModel):
    dataset_id: str
    name: str | None = None
    features: list[str] | None = Field(None, description="Columns to use; default: every non-ID, non-text column.")
    drop_columns: list[str] | None = None
    contamination: float = Field(0.015, gt=0, lt=0.5, description="Expected share of anomalies: sets the threshold.")
    n_estimators: int = Field(300, ge=10, le=5000)
    max_samples: int | float | str = "auto"
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
    source: str | None = None


def _ds(request: Request, dataset_id: str) -> dict[str, Any]:
    ds = request.app.state.db.get_dataset(dataset_id)
    if not ds:
        raise HTTPException(404, f"No dataset with id '{dataset_id}'.")
    return ds


@router.post("/runs", status_code=201)
async def create(body: AnomalyCreate, request: Request) -> dict[str, Any]:
    return await run_in_threadpool(_create, body, request)


def _create(body: AnomalyCreate, request: Request) -> dict[str, Any]:
    from ..engine.api import run_out

    app = request.app
    ds = _ds(request, body.dataset_id)
    cols = {c["name"]: c for c in ds["profile"]["columns"]}
    for c in (body.features or []) + (body.drop_columns or []):
        if c not in cols:
            raise HTTPException(400, f"No column named '{c}'.")
    for c in [body.entity_column, body.time_column, *(body.relative_columns or [])]:
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
    run_id = "run_" + secrets.token_hex(4)
    settings = app.state.settings
    run_dir = settings.runs_dir / run_id
    run_dir.mkdir(parents=True)
    truth = (app.state.datasets.sidecar(body.dataset_id, "anomaly_truth.json") or {}).get("anomalies")
    params = body.model_dump(exclude={"dataset_id", "name", "features", "drop_columns", "source"})
    cfg = {"kind": "anomaly", "dataset_id": body.dataset_id, "features": body.features, "drop_columns": body.drop_columns or [],
           "params": params, "label": "anomaly", "source": body.source,
           "id_columns": [c for c, v in cols.items() if v["type"] == "id"],
           "profile": {"columns": [{"name": c["name"], "type": c["type"]} for c in ds["profile"]["columns"]]},
           "truth": truth, "run_id": run_id, "db_path": str(settings.db_path),
           "csv_path": str(app.state.datasets.csv_path(body.dataset_id)), "run_dir": str(run_dir),
           "predictor_path": str(run_dir / "model")}
    (run_dir / "config.json").write_text(json.dumps(cfg, indent=1))
    name = body.name or f"{ds['name'].replace(' (sample)', '')} · anomalies · c={body.contamination:g}"
    store = app.state.runs
    store.create({"id": run_id, "name": name, "dataset_id": body.dataset_id, "label": "anomaly", "created_at": now(), "config": cfg})
    store.emit(run_id, "run_queued", kind="anomaly", dataset_id=body.dataset_id, contamination=body.contamination)
    app.state.runner.submit(run_id)
    out = run_out(store.get(run_id), store)
    out["config"] = {k: v for k, v in cfg.items() if k != "truth"}
    return out


@router.get("/mapping/{dataset_id}")
def mapping(dataset_id: str, request: Request) -> dict[str, Any]:
    """Suggested entity, time and relative columns for group-relative features: the sample's spec, else names and
    types (a repeated ID-like column, a datetime column, amount-like numbers)."""
    ds = _ds(request, dataset_id)
    spec = request.app.state.datasets.sidecar(dataset_id, "anomaly_truth.json")
    df = pd.read_csv(request.app.state.datasets.csv_path(dataset_id), nrows=20000)
    types = {c["name"]: c["type"] for c in ds["profile"]["columns"]}
    return group.suggest(df, types, spec)


def _run(request: Request, run_id: str, done: bool = True) -> dict[str, Any]:
    rec = request.app.state.runs.get(run_id)
    if not rec or rec["config"].get("kind") != "anomaly":
        raise HTTPException(404, f"No anomaly run with id '{run_id}'.")
    if done and rec["status"] != "succeeded":
        raise HTTPException(409, f"Run {run_id} is {rec['status']}.")
    return rec


@router.get("/runs/{run_id}/top")
def top(run_id: str, request: Request, contamination: float | None = Query(None, gt=0, lt=0.5),
        limit: int = Query(100, ge=1, le=1000)) -> dict[str, Any]:
    """The most anomalous rows with their reasons; with `contamination`, the threshold and the flags at that share
    (and, for the sample, precision@k against the hidden ground truth)."""
    rec = _run(request, run_id)
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
    return {"run_id": run_id, "contamination": c, "threshold": thr, "n_flagged": n_flag, "rows": rows[:limit],
            "truth": check, "n_rows": int(len(s))}


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
    content = await file.read(config.MAX_UPLOAD_BYTES + 1)
    if not content.strip():
        raise HTTPException(400, "The file is empty.")
    bid = "ab_" + secrets.token_hex(4)
    d = Path(rec["config"]["run_dir"]) / "batches" / bid
    d.mkdir(parents=True)
    (d / "input.csv").write_bytes(content)
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


class OneRow(BaseModel):
    row: dict[str, Any]


@router.post("/runs/{run_id}/score-one")
async def score_one(run_id: str, body: OneRow, request: Request) -> dict[str, Any]:
    rec = _run(request, run_id)
    try:
        return await request.app.state.heavy.run(core.score_one, rec["config"]["predictor_path"], body.row)
    except core.AnomalyError as e:
        raise HTTPException(400, str(e)) from e
