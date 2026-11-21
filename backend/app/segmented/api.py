"""G5 routes: train one model per segment against a global one (zero-config from the G0 roles) and read the comparison.

    GET  /api/segmented/detect?dataset_id=     the target, the segment column (with its candidates), the split, the
                                               columns left out, and whether the segments look different, with evidence
    POST /api/segmented/runs                   {dataset_id, ...overrides} → a run (kind segmented)
    GET  /api/segmented/runs/{run_id}          the per-segment and overall comparison, and the routing
"""

from __future__ import annotations

from typing import Any, Literal

import pandas as pd
from fastapi import APIRouter, HTTPException, Request
from fastapi.concurrency import run_in_threadpool
from pydantic import BaseModel, Field

from ..datasets import DatasetError
from ..engine.api import alloc_run, queue_run, run_out, split_record
from ..http import dataset_or_404
from .. import frames
from . import core

router = APIRouter(prefix="/api/segmented")


class SegCreate(BaseModel):
    dataset_id: str
    name: str | None = None
    label: str | None = Field(None, description="The target; default: the detected one.")
    segment_column: str | None = Field(None, description="One model per value of this column; default: the segment role.")
    drop_columns: list[str] | None = Field(None, description="Left out of every model; default: IDs, constants, leaks, protected.")
    split: Literal["auto", "random", "time", "group", "time-group"] = "auto"
    time_column: str | None = None
    group_column: str | None = None
    cost: dict[str, float] | None = Field(None, description="Yes/no targets: {c_fp, c_fn}; t = c_FP / (c_FP + c_FN). Default F7's 1 : 10.")
    presets: str | None = "medium_quality"
    time_limit: float | None = Field(20, description="Seconds per model (the global one and each segment's).")
    fit_kwargs: dict[str, Any] = Field(default_factory=dict)


def _task(s: pd.Series) -> str | None:
    k = int(s.nunique(dropna=True))
    if k == 2:
        return "binary"
    if pd.api.types.is_numeric_dtype(s) and k > 20:
        return "regression"
    return None


def detect(df: pd.DataFrame) -> dict[str, Any]:
    """What a segmented run would use, from the G0 roles, each with its evidence."""
    from ..intake import EXCLUDED, detect as roles_of

    found = roles_of(df)
    cols = found["columns"]
    out: dict[str, Any] = {"label": None, "problem_type": None, "segment_column": None, "candidates": [], "evidence": {}}
    tgt = next((c for c in cols if c["role"] == "target"), None)
    if tgt:
        out["label"], out["problem_type"] = tgt["name"], _task(df[tgt["name"]])
        out["evidence"][tgt["name"]] = ["the target", *tgt["evidence"]]
    cands = []
    for c in cols:
        if c["name"] == out["label"] or not 2 <= c["unique"] <= core.SEGMENT_MAX:
            continue
        roles = [c["role"]] + [a["role"] for a in c["also"]]
        if "segment" in roles:
            cands.append((0, c, c["evidence"] if c["role"] == "segment" else next(a["evidence"] for a in c["also"] if a["role"] == "segment")))
        elif c["role"] in ("category", "geography") and c["type"] == "category":
            cands.append((1, c, [f"a category with {c['unique']} values", *(c["evidence"] or [])][:2]))
    cands.sort(key=lambda x: (x[0], x[1]["unique"]))
    out["candidates"] = [c["name"] for _, c, _ in cands]
    if cands:
        _, c, ev = cands[0]
        out["segment_column"] = c["name"]
        vc = df[c["name"]].astype(str).value_counts()
        out["evidence"][c["name"]] = [*ev, f"{len(vc)} groups: " + ", ".join(f"{k} {v:,}" for k, v in vc.items())]
    out["heterogeneity"] = core.heterogeneity(df, out["label"], out["segment_column"]) if out["label"] and out["segment_column"] else None
    used = {out["label"], out["segment_column"]}
    out["drop_columns"] = [c["name"] for c in cols if c["name"] not in used and
                           (c["role"] in EXCLUDED or c["role"] == "entity" or any(a["role"] == "leak" and a["confidence"] >= 0.85 for a in c["also"]))]
    date = next((c["name"] for c in cols if c["role"] == "date"), None)
    ent = next((c["name"] for c in cols if c["role"] == "entity"), None)
    out["split"] = "time-group" if date and ent else "time" if date else "group" if ent else "random"
    out["time_column"], out["group_column"] = date, ent
    return out


@router.get("/detect")
def detect_route(dataset_id: str, request: Request) -> dict[str, Any]:
    dataset_or_404(request, dataset_id)
    return {"dataset_id": dataset_id, **detect(frames.read_version(dataset_id))}


@router.post("/runs", status_code=201)
async def create(body: SegCreate, request: Request) -> dict[str, Any]:
    return await run_in_threadpool(_create, body, request)


def _create(body: SegCreate, request: Request) -> dict[str, Any]:
    from ..engine.api import display_name

    app = request.app
    ds = dataset_or_404(request, body.dataset_id)
    df = frames.read_version(body.dataset_id)
    det = detect(df)
    label = body.label or det["label"]
    seg = body.segment_column or det["segment_column"]
    for what, c in (("label", label), ("segment_column", seg)):
        if not c:
            raise HTTPException(400, f"{what}: no column was detected for it; name one.")
        if c not in df.columns:
            raise HTTPException(400, f"{what}: no column named '{c}'.")
    if label == seg:
        raise HTTPException(400, "The target and the segment column must differ.")
    pt = _task(df[label])
    if pt is None:
        raise HTTPException(400, f"'{label}' must be a yes/no outcome or a number: segmented models compare binary "
                                 "classifiers or regressions.")
    k = int(df[seg].nunique(dropna=True))
    if not 2 <= k <= core.SEGMENT_MAX:
        raise HTTPException(400, f"'{seg}' has {k} values; a segment column needs 2–{core.SEGMENT_MAX}.")
    drop = body.drop_columns if body.drop_columns is not None else det["drop_columns"]
    bad = [c for c in drop if c not in df.columns]
    if bad:
        raise HTTPException(400, f"No column named '{bad[0]}'.")
    drop = [c for c in drop if c not in (label, seg)]
    for key in ("train_data", "tuning_data", "label", "path"):
        if key in body.fit_kwargs:
            raise HTTPException(400, f"fit_kwargs.{key} is set by the run.")
    if body.cost and (body.cost.get("c_fp", 0) <= 0 or body.cost.get("c_fn", 0) <= 0):
        raise HTTPException(400, "cost: c_fp and c_fn must be > 0.")
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
        sp = split_record(app.state.datasets, body.dataset_id, label, spec)
    except DatasetError as e:
        raise HTTPException(400, str(e)) from e
    if group_col and spec.get("group_column") and group_col not in drop:
        drop.append(group_col)
    if time_col and spec.get("time_column") and time_col not in drop:
        drop.append(time_col)
    settings = app.state.settings
    run_id, run_dir = alloc_run(app)
    cfg = {
        "kind": "segmented", "dataset_id": body.dataset_id, "label": label, "problem_type": pt, "segment_column": seg,
        "drop_columns": drop, "presets": body.presets, "time_limit": body.time_limit, "fit_kwargs": body.fit_kwargs,
        "cost": body.cost, "split": sp, "splits_path": sp["path"], "run_id": run_id, "db_path": str(settings.db_path),
        "csv_path": str(frames.version_file(body.dataset_id)), "input_ref": frames.input_ref_for(body.dataset_id), "run_dir": str(run_dir),
        "predictor_path": str(run_dir / "segmented"),
        "detected": {k: det[k] for k in ("label", "segment_column", "candidates", "drop_columns", "split", "evidence", "heterogeneity")},
    }
    name = body.name or f"{display_name(ds)} · segmented by {seg}"
    store = queue_run(app, run_id, run_dir, name, body.dataset_id, label, cfg, kind="segmented",
                      dataset_id=body.dataset_id, label=label, presets=body.presets, time_limit=body.time_limit)
    return run_out(store.get(run_id), store)


@router.get("/runs/{run_id}")
def get(run_id: str, request: Request) -> dict[str, Any]:
    rec = request.app.state.runs.get(run_id)
    if not rec or rec["config"].get("kind") != "segmented":
        raise HTTPException(404, f"No segmented run with id '{run_id}'.")
    cfg = rec["config"]
    return {"run_id": run_id, "name": rec["name"], "status": rec["status"], "error": rec.get("error"),
            "dataset_id": rec["dataset_id"], "created_at": rec.get("created_at"),
            "config": {k: cfg.get(k) for k in ("label", "problem_type", "segment_column", "drop_columns", "presets",
                                                "time_limit", "cost", "detected")},
            "split": {k: v for k, v in (cfg.get("split") or {}).items() if k != "path"},
            "segmented": (rec.get("result") or {}).get("segmented")}


def card_section(run: dict[str, Any]) -> dict[str, Any] | None:
    """The model card's segmented-models section (JSON); None for other runs."""
    if (run.get("config") or {}).get("kind") != "segmented":
        return None
    s = (run.get("result") or {}).get("segmented") or {}
    head = "delta_auc" if s.get("problem_type") == "binary" else "delta_mse"

    def brief(c: dict[str, Any] | None) -> dict[str, Any] | None:
        if not c:
            return None
        d = c.get(head) or {}
        return {"n": c.get("n"), "global": c.get("global"), "routed": c.get("routed"), "delta": d.get("delta"),
                "ci": d.get("ci"), "verdict": c.get("verdict")}

    return {"segment_column": s.get("segment_column"), "problem_type": s.get("problem_type"), "headline": s.get("headline"),
            "threshold": s.get("threshold"), "routing_rule": s.get("routing_rule"), "routes": s.get("routes"),
            "overall": brief(s.get("overall")),
            "segments": [{"value": r["value"], "train_rows": r["train_rows"], "holdout_rows": r.get("holdout_rows"),
                          "routed": r.get("routed"), "why": r.get("route_why"), "own": brief(r.get("own"))}
                         for r in s.get("segments") or []]}
