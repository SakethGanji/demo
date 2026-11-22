"""G6 routes: time-to-event models (zero-config from the G0 roles) and their results.

    GET  /api/survival/detect?dataset_id=     the duration and event columns (with candidates and evidence), the horizons,
                                              the columns left out and the split, each with why
    POST /api/survival/runs                   {dataset_id, ...overrides} → a run (kind survival)
    GET  /api/survival/runs/{run_id}          the C-index, AUC(τ), calibration by risk group, hazard ratios, curves
"""

from __future__ import annotations

from typing import Any, Literal

import pandas as pd
from fastapi import APIRouter, HTTPException, Request
from fastapi.concurrency import run_in_threadpool
from ..authz import body, path, query, require# P19
from pydantic import BaseModel, Field

from ..datasets import DatasetError
from ..engine.api import alloc_run, queue_run, run_out, split_record
from ..http import dataset_or_404
from .. import frames
from . import core

router = APIRouter(prefix="/api/survival")


class SurvCreate(BaseModel):
    dataset_id: str
    name: str | None = None
    duration_column: str | None = Field(None, description="Time from the start of follow-up to the event or censoring; default: the duration role.")
    event_column: str | None = Field(None, description="1 = the event happened, 0 = censored; default: the event role (or a 0/1 event-named target).")
    horizons: list[float] | None = Field(None, description="Times τ at which to report Ŝ(τ); default: 12/24/36 for months, else the quartiles.")
    drop_columns: list[str] | None = Field(None, description="Left out of the models; default: IDs, dates, constants, leaks, protected.")
    split: Literal["auto", "random", "group"] = "auto"
    group_column: str | None = None
    n_estimators: int = Field(200, ge=20, le=1000, description="Trees in the random survival forest.")
    min_samples_leaf: int = Field(15, ge=3, le=500)


def detect(df: pd.DataFrame) -> dict[str, Any]:
    """What a survival run would use, from the G0 roles, each with its evidence."""
    from ..intake import EXCLUDED, NOT_INPUTS, detect as roles_of

    found = roles_of(df)
    cols = found["columns"]
    tgt = next((c["name"] for c in cols if c["role"] == "target"), None)
    p = core.pairs(df, cols, tgt)
    d, e = (p["durations"] or [None])[0], (p["events"] or [None])[0]
    out: dict[str, Any] = {"duration_column": d and d["column"], "event_column": e and e["column"], "strong": bool(d and d["strong"]),
                           "duration_candidates": [x["column"] for x in p["durations"]], "event_candidates": [x["column"] for x in p["events"]],
                           "evidence": {**({d["column"]: d["evidence"]} if d else {}), **({e["column"]: e["evidence"]} if e else {})}}
    if d:
        t = pd.to_numeric(df[d["column"]], errors="coerce")
        out["horizons"] = core.default_horizons(d["column"], t)
        out["unit"] = core.unit_of(d["column"])
        out["duration_range"] = [float(t.min()), float(t.max())]
    else:
        out["horizons"], out["unit"], out["duration_range"] = [], None, None
    out["event_rate"] = e and e["rate"]
    used = {out["duration_column"], out["event_column"]}
    out["drop_columns"] = [c["name"] for c in cols if c["name"] not in used and
                           (c["role"] in EXCLUDED or c["role"] in NOT_INPUTS or c["role"] in ("date", "target", "text")
                            or any(a["role"] == "leak" and a["confidence"] >= 0.85 for a in c["also"]))]
    ent = next((c["name"] for c in cols if c["role"] == "entity"), None)
    date = next((c["name"] for c in cols if c["role"] == "date"), None)
    out["split"], out["group_column"] = ("group", ent) if ent else ("random", None)
    out["split_why"] = (f"group holdout by {ent}: no {ent} is in both training and holdout" if ent else
                        "random holdout, stratified by the event" +
                        (f". Not out-of-time by {date}: the newest subjects have the shortest follow-up, so an out-of-time "
                         f"holdout would be mostly censored and could not check the later horizons" if date else ""))
    return out


@router.get("/detect", dependencies=[require("data.preview", query("dataset_id", "dataset"))])
def detect_route(dataset_id: str, request: Request) -> dict[str, Any]:
    dataset_or_404(request, dataset_id)
    return {"dataset_id": dataset_id, **detect(frames.read_version(dataset_id))}


@router.post("/runs", status_code=201, dependencies=[require("run.train", body("dataset_id", "dataset"))])
async def create(body: SurvCreate, request: Request) -> dict[str, Any]:
    return await run_in_threadpool(_create, body, request)


def _create(body: SurvCreate, request: Request) -> dict[str, Any]:
    from ..engine.api import display_name

    app = request.app
    ds = dataset_or_404(request, body.dataset_id)
    df = frames.read_version(body.dataset_id)
    det = detect(df)
    dur = body.duration_column or det["duration_column"]
    evt = body.event_column or det["event_column"]
    for what, c in (("duration_column", dur), ("event_column", evt)):
        if not c:
            raise HTTPException(400, f"{what}: no column was detected for it; name one.")
        if c not in df.columns:
            raise HTTPException(400, f"{what}: no column named '{c}'.")
    if dur == evt:
        raise HTTPException(400, "The duration and the event must be different columns.")
    t = pd.to_numeric(df[dur], errors="coerce")
    if t.notna().sum() < 100 or (t.dropna() < 0).any():
        raise HTTPException(400, f"'{dur}' must be a non-negative number (time to the event or to censoring).")
    ev = pd.to_numeric(df[evt], errors="coerce")
    if not set(ev.dropna().unique()) <= {0, 1}:
        raise HTTPException(400, f"'{evt}' must be 0/1: 1 = the event happened, 0 = censored.")
    if ev.sum() < 20 or (ev == 0).sum() < 1:
        raise HTTPException(400, f"'{evt}' needs at least 20 events.")
    horizons = sorted({float(h) for h in (body.horizons or det["horizons"] or core.default_horizons(dur, t)) if h > 0})
    drop = body.drop_columns if body.drop_columns is not None else det["drop_columns"]
    bad = [c for c in drop if c not in df.columns]
    if bad:
        raise HTTPException(400, f"No column named '{bad[0]}'.")
    drop = [c for c in drop if c not in (dur, evt)]
    split = det["split"] if body.split == "auto" else body.split
    group_col = body.group_column or det["group_column"]
    spec: dict[str, Any] = {"strategy": "group", "group_column": group_col} if split == "group" and group_col else {"strategy": "random-stratified"}
    try:
        sp = split_record(app.state.datasets, body.dataset_id, evt, spec)
    except DatasetError as e:
        raise HTTPException(400, str(e)) from e
    if spec.get("group_column") and group_col not in drop:
        drop.append(group_col)
    settings = app.state.settings
    run_id, run_dir = alloc_run(app)
    cfg = {
        "kind": "survival", "dataset_id": body.dataset_id, "label": evt, "duration_column": dur, "event_column": evt,
        "horizons": horizons, "drop_columns": drop, "n_estimators": body.n_estimators, "min_samples_leaf": body.min_samples_leaf,
        "split": sp, "splits_path": sp["path"], "run_id": run_id, "db_path": str(settings.db_path),
        "csv_path": str(frames.version_file(body.dataset_id)), "input_ref": frames.input_ref_for(body.dataset_id), "run_dir": str(run_dir),
        "predictor_path": str(run_dir / "survival"),
        "detected": {k: det[k] for k in ("duration_column", "event_column", "strong", "duration_candidates", "event_candidates",
                                         "horizons", "drop_columns", "split", "split_why", "evidence", "event_rate")},
    }
    name = body.name or f"{display_name(ds)} · time to {evt}"
    store = queue_run(app, run_id, run_dir, name, body.dataset_id, evt, cfg, kind="survival", dataset_id=body.dataset_id, label=evt)
    return run_out(store.get(run_id), store)


@router.get("/runs/{run_id}", dependencies=[require("run.read", path("run_id", "run"))])
def get(run_id: str, request: Request) -> dict[str, Any]:
    rec = request.app.state.runs.get(run_id)
    if not rec or rec["config"].get("kind") != "survival":
        raise HTTPException(404, f"No survival run with id '{run_id}'.")
    cfg = rec["config"]
    return {"run_id": run_id, "name": rec["name"], "status": rec["status"], "error": rec.get("error"),
            "dataset_id": rec["dataset_id"], "created_at": rec.get("created_at"),
            "config": {k: cfg.get(k) for k in ("duration_column", "event_column", "horizons", "drop_columns", "n_estimators",
                                                "min_samples_leaf", "detected")},
            "split": {k: v for k, v in (cfg.get("split") or {}).items() if k != "path"},
            "survival": (rec.get("result") or {}).get("survival")}


def card_section(run: dict[str, Any]) -> dict[str, Any] | None:
    """The model card's time-to-event section (JSON); None for other runs."""
    if (run.get("config") or {}).get("kind") != "survival":
        return None
    s = (run.get("result") or {}).get("survival") or {}
    m = (s.get("models") or {}).get(s.get("served")) or {}
    other = "rsf" if s.get("served") == "cox" else "cox"
    return {"duration_column": s.get("duration_column"), "event_column": s.get("event_column"), "unit": s.get("unit"),
            "served": s.get("served"), "chosen_on": s.get("val_from"), "c_index": m.get("c_index"), "c_index_ci": m.get("c_index_ci"),
            "other": {"model": other, "c_index": ((s.get("models") or {}).get(other) or {}).get("c_index")},
            "at": m.get("at"), "calibration": s.get("calibration"), "holdout_rows": s.get("holdout_rows"),
            "events": s.get("events"), "hazard_ratios": (s.get("hazard_ratios") or [])[:8]}
