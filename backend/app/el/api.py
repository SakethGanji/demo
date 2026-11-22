"""G3 routes: start an expected-loss run (zero-config from the G0 roles), read it, and run stress scenarios.

    POST /api/expected-loss/runs                  {dataset_id, ...overrides} → a run (kind expected_loss)
    GET  /api/expected-loss/runs/{run_id}         the run's EL result (portfolio, components, segments, scenarios)
    POST /api/expected-loss/runs/{run_id}/stress  {name?, shocks: [{column, op: pct|pp|add|set, value}], expect?}
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Literal

import pandas as pd
from fastapi import APIRouter, HTTPException, Request
from fastapi.concurrency import run_in_threadpool
from ..authz import body, path, query, require# P19
from pydantic import BaseModel, Field

from ..engine.api import alloc_run, queue_run, run_out, split_record
from ..datasets import DatasetError
from ..http import dataset_or_404
from .. import frames
from . import core

router = APIRouter(prefix="/api/expected-loss")
MAX_CUSTOM = 12


class ElCreate(BaseModel):
    dataset_id: str
    name: str | None = None
    pd_column: str | None = Field(None, description="The default flag; default: the detected target.")
    lgd_column: str | None = Field(None, description="Loss given default (0–1, defaulters only); default: the LGD role.")
    ead_column: str | None = Field(None, description="Exposure at default; default: the EAD role.")
    positive_class: Any = None
    segments: list[str] | None = Field(None, description="Columns for the EL breakdown; default: segment + geography roles.")
    drop_columns: list[str] | None = Field(None, description="Left out of all three models; default: IDs, constants, leaks.")
    split: Literal["auto", "random", "time", "group", "time-group"] = "auto"
    time_column: str | None = None
    group_column: str | None = None
    presets: str | None = "medium_quality"
    time_limit: float | None = Field(60, description="Seconds per model (three models).")
    fit_kwargs: dict[str, Any] = Field(default_factory=dict)


def detect(df: pd.DataFrame) -> dict[str, Any]:
    """The EL inputs from the G0 roles: the default flag, LGD, EAD, segments, what to leave out and the split, each
    with its evidence."""
    from ..intake import detect as roles_of

    found = roles_of(df)
    cols = found["columns"]

    def having(role: str) -> list[dict[str, Any]]:
        return [c for c in cols if c["role"] == role] + [c for c in cols if c["role"] != role and any(a["role"] == role for a in c["also"])]

    def ev(c: dict[str, Any], role: str) -> list[str]:
        return c["evidence"] if c["role"] == role else next(a["evidence"] for a in c["also"] if a["role"] == role)

    tgt = next((c for c in cols if c["role"] == "target"), None)
    out: dict[str, Any] = {"pd_column": None, "lgd_column": None, "ead_column": None, "segments": [], "evidence": {}}
    if tgt and int(df[tgt["name"]].nunique()) == 2:
        out["pd_column"] = tgt["name"]
        out["evidence"][tgt["name"]] = ["the target: a yes/no outcome", *tgt["evidence"]]
    for role in ("lgd", "ead"):
        hits = having(role)
        if hits:
            out[f"{role}_column"] = hits[0]["name"]
            out["evidence"][hits[0]["name"]] = ev(hits[0], role)
    for role in ("segment", "geography"):
        for c in having(role):
            if c["name"] not in out["segments"] and 2 <= c["unique"] <= core.SEGMENT_MAX:
                out["segments"].append(c["name"])
                out["evidence"].setdefault(c["name"], ev(c, role))
    used = {out["pd_column"], out["lgd_column"], out["ead_column"]}
    out["drop_columns"] = [c["name"] for c in cols if c["name"] not in used and
                           (c["role"] in ("id", "constant", "leak", "entity") or any(a["role"] == "leak" and a["confidence"] >= 0.85 for a in c["also"]))]
    date = next((c["name"] for c in cols if c["role"] == "date"), None)
    ent = next((c["name"] for c in cols if c["role"] == "entity"), None)
    out["split"] = "time-group" if date and ent else "time" if date else "group" if ent else "random"
    out["time_column"], out["group_column"] = date, ent
    return out


@router.get("/detect", dependencies=[require("data.preview", query("dataset_id", "dataset"))])
def detect_route(dataset_id: str, request: Request) -> dict[str, Any]:
    """What an EL run on this table would use, from the G0 roles, with the evidence (the Build page shows it)."""
    dataset_or_404(request, dataset_id)
    return {"dataset_id": dataset_id, **detect(frames.read_version(dataset_id))}


@router.post("/runs", status_code=201, dependencies=[require("run.train", body("dataset_id", "dataset"))])
async def create(body: ElCreate, request: Request) -> dict[str, Any]:
    return await run_in_threadpool(_create, body, request)


def _create(body: ElCreate, request: Request) -> dict[str, Any]:
    from ..engine.api import display_name

    app = request.app
    ds = dataset_or_404(request, body.dataset_id)
    df = frames.read_version(body.dataset_id)
    det = detect(df)
    pd_col = body.pd_column or det["pd_column"]
    lgd_col = body.lgd_column or det["lgd_column"]
    ead_col = body.ead_column or det["ead_column"]
    for what, c in (("pd_column", pd_col), ("lgd_column", lgd_col), ("ead_column", ead_col)):
        if not c:
            raise HTTPException(400, f"{what}: no column was detected for it; name one.")
        if c not in df.columns:
            raise HTTPException(400, f"{what}: no column named '{c}'.")
    if len({pd_col, lgd_col, ead_col}) < 3:
        raise HTTPException(400, "The default flag, LGD and EAD must be three different columns.")
    if df[pd_col].nunique(dropna=True) != 2:
        raise HTTPException(400, f"'{pd_col}' must be a yes/no flag (two values) for the PD model.")
    for c in (lgd_col, ead_col):
        if not pd.api.types.is_numeric_dtype(df[c]):
            raise HTTPException(400, f"'{c}' must be numeric.")
    lg = df[lgd_col].dropna()
    if len(lg) and (lg.min() < 0 or lg.max() > 1):
        raise HTTPException(400, f"'{lgd_col}' must be a share in [0, 1] (loss ÷ exposure).")
    segs = body.segments if body.segments is not None else det["segments"]
    drop = body.drop_columns if body.drop_columns is not None else det["drop_columns"]
    bad = [c for c in [*segs, *drop] if c not in df.columns]
    if bad:
        raise HTTPException(400, f"No column named '{bad[0]}'.")
    drop = [c for c in drop if c not in (pd_col, lgd_col, ead_col)]
    for k in ("train_data", "tuning_data", "label", "path"):
        if k in body.fit_kwargs:
            raise HTTPException(400, f"fit_kwargs.{k} is set by the run.")
    split = det["split"] if body.split == "auto" else body.split
    time_col = body.time_column or det["time_column"]
    group_col = body.group_column or det["group_column"]
    spec: dict[str, Any] = {"strategy": "random-stratified"}
    if split in ("time", "time-group") and time_col:
        spec = {"strategy": split, "time_column": time_col}
        if split == "time-group" and group_col:
            spec["group_column"] = group_col
    elif split == "group" and group_col:
        spec = {"strategy": "group", "group_column": group_col}
    try:
        sp = split_record(app.state.datasets, body.dataset_id, pd_col, spec)
    except DatasetError as e:
        raise HTTPException(400, str(e)) from e
    if group_col and spec.get("group_column") and group_col not in drop:
        drop.append(group_col)
    settings = app.state.settings
    run_id, run_dir = alloc_run(app)
    cfg = {
        "kind": "expected_loss", "dataset_id": body.dataset_id, "label": pd_col, "lgd_column": lgd_col, "ead_column": ead_col,
        "positive_class": body.positive_class, "segments": segs, "drop_columns": drop, "presets": body.presets,
        "time_limit": body.time_limit, "fit_kwargs": body.fit_kwargs, "split": sp, "splits_path": sp["path"],
        "run_id": run_id, "db_path": str(settings.db_path), "csv_path": str(frames.version_file(body.dataset_id)), "input_ref": frames.input_ref_for(body.dataset_id),
        "run_dir": str(run_dir), "predictor_path": str(run_dir / "el"),
        "detected": {"pd_column": det["pd_column"], "lgd_column": det["lgd_column"], "ead_column": det["ead_column"],
                     "segments": det["segments"], "drop_columns": det["drop_columns"], "split": det["split"],
                     "evidence": det["evidence"]},
    }
    name = body.name or f"{display_name(ds)} · expected loss"
    store = queue_run(app, run_id, run_dir, name, body.dataset_id, pd_col, cfg, kind="expected_loss",
                      dataset_id=body.dataset_id, label=pd_col, presets=body.presets, time_limit=body.time_limit)
    return run_out(store.get(run_id), store)


def _run(request: Request, run_id: str) -> dict[str, Any]:
    rec = request.app.state.runs.get(run_id)
    if not rec or rec["config"].get("kind") != "expected_loss":
        raise HTTPException(404, f"No expected-loss run with id '{run_id}'.")
    return rec


@router.get("/runs/{run_id}", dependencies=[require("run.read", path("run_id", "run"))])
def get(run_id: str, request: Request) -> dict[str, Any]:
    rec = _run(request, run_id)
    res = rec.get("result") or {}
    cfg = rec["config"]
    return {"run_id": run_id, "name": rec["name"], "status": rec["status"], "error": rec.get("error"),
            "dataset_id": rec["dataset_id"], "config": {k: cfg.get(k) for k in ("label", "lgd_column", "ead_column", "segments",
                                                                                 "drop_columns", "presets", "time_limit", "detected")},
            "split": {k: v for k, v in (cfg.get("split") or {}).items() if k != "path"},
            "expected_loss": res.get("expected_loss"), "custom_scenarios": res.get("custom_scenarios") or []}


class Shock(BaseModel):
    column: str
    op: Literal["pct", "pp", "add", "set"]
    value: Any


class StressBody(BaseModel):
    name: str | None = None
    shocks: list[Shock] = Field(..., min_length=1, max_length=12)
    expect: Literal["up", "down"] | None = None
    save: bool = True


@router.post("/runs/{run_id}/stress", dependencies=[require("run.train", path("run_id", "run"))])
async def stress(run_id: str, body: StressBody, request: Request) -> dict[str, Any]:
    """Shock the holdout loans, re-score all three models, and compare EL overall and by segment."""
    rec = _run(request, run_id)
    if rec["status"] != "succeeded":
        raise HTTPException(409, "The run has not finished.")
    return await run_in_threadpool(_stress, request, rec, body)


def _stress(request: Request, rec: dict[str, Any], body: StressBody) -> dict[str, Any]:
    from ..engine.worker import load_frames

    cfg = rec["config"]
    try:
        models = core.load(cfg["predictor_path"])
    except FileNotFoundError as e:
        raise HTTPException(404, "The run's models are gone.") from e
    meta = models["meta"]
    base = {**cfg, "drop_columns": [c for c in cfg["drop_columns"] if c not in (cfg["lgd_column"], cfg["ead_column"])]}
    _, holdout, _, _ = load_frames(base)
    scen = {"name": body.name, "shocks": [s.model_dump() for s in body.shocks], "expect": body.expect}
    base_scores = None
    f = Path(cfg["run_dir"]) / "el_holdout.csv"
    if f.exists():  # the run's own holdout scores, in the same row order: only the shocked rows need scoring
        b = frames.read_artifact(f)
        if len(b) == len(holdout) and (b["row"].to_numpy() == holdout.index.to_numpy()).all():
            base_scores = b[["pd", "lgd", "ead", "el"]].reset_index(drop=True)
    try:
        out = core.stress(models, holdout, scen, meta.get("segments") or [], base=base_scores)
    except ValueError as e:
        raise HTTPException(400, str(e)) from e
    if body.save:
        res = rec.get("result") or {}
        custom = [s for s in (res.get("custom_scenarios") or []) if s.get("name") != out["name"]]
        request.app.state.runs.merge_result(rec["id"], custom_scenarios=(custom + [out])[-MAX_CUSTOM:])
    return out


def card_section(run: dict[str, Any]) -> dict[str, Any] | None:
    """The model card's expected-loss section (JSON); None for other runs."""
    if (run.get("config") or {}).get("kind") != "expected_loss":
        return None
    el = (run.get("result") or {}).get("expected_loss") or {}
    p, c = el.get("portfolio") or {}, el.get("components") or {}
    return {"columns": el.get("columns"), "portfolio": p, "segments": el.get("segment_columns"),
            "models": {k: {x: (c.get(k) or {}).get(x) for x in ("label", "problem_type", "eval_metric", "best_model", "train_rows",
                                                                 "auc", "mae", "r2", "mean_pd", "default_rate")} for k in ("pd", "lgd", "ead")},
            "scenarios": [{x: s.get(x) for x in ("name", "delta", "delta_pct", "expect", "as_expected")} for s in el.get("scenarios") or []],
            "detected": (run.get("config") or {}).get("detected")}

