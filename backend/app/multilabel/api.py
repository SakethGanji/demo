"""G8 routes: multi-label classification (zero-config from the G0 roles) and its results.

    GET  /api/multilabel/detect?dataset_id=   the label set (a delimited column or one 0/1 column per label) with its
                                              evidence, the labels and their counts, the text columns, the columns left
                                              out and the split
    POST /api/multilabel/runs                 {dataset_id, ...overrides} → a run (kind multilabel)
    GET  /api/multilabel/runs/{run_id}        per-label F1 / precision / recall / AP / threshold, micro and macro F1,
                                              Hamming loss, with CIs
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

router = APIRouter(prefix="/api/multilabel")


class MLCreate(BaseModel):
    dataset_id: str
    name: str | None = None
    label_column: str | None = Field(None, description="A column of delimited label lists; default: the multilabel role.")
    delimiter: str | None = Field(None, description="Default: detected (';', '|' or ',').")
    label_columns: list[str] | None = Field(None, description="Or one 0/1 column per label; default: a detected group.")
    drop_columns: list[str] | None = Field(None, description="Left out of every model; default: IDs, dates, constants, leaks.")
    split: Literal["auto", "random", "time"] = "auto"
    time_column: str | None = None
    presets: str | None = "medium_quality"
    time_limit: float | None = Field(15, description="Seconds per label model.")
    fit_kwargs: dict[str, Any] = Field(default_factory=dict)


def detect(df: pd.DataFrame) -> dict[str, Any]:
    """What a multi-label run would use, from the G0 roles, each with its evidence."""
    from ..intake import EXCLUDED, NOT_INPUTS, detect as roles_of

    cols = roles_of(df)["columns"]
    f = core.find(df, cols)
    out: dict[str, Any] = {**f}
    if f["source"]:
        M, labels = core.label_matrix(df, f)
        out["counts"] = [{"label": l, "n": int(M[:, k].sum()), "share": float(M[:, k].mean())} for k, l in enumerate(labels)]
        out["multi_share"] = float((M.sum(axis=1) >= 2).mean())
        out["mean_labels"] = float(M.sum(axis=1).mean())
    used = set(core.label_source_columns(f)) if f["source"] else set()
    out["text_columns"] = [c["name"] for c in cols if c["role"] == "text" and c["name"] not in used]
    out["drop_columns"] = [c["name"] for c in cols if c["name"] not in used and
                           (c["role"] in EXCLUDED or c["role"] in NOT_INPUTS or c["role"] in ("entity", "date", "target")
                            or any(a["role"] == "leak" and a["confidence"] >= 0.85 for a in c["also"]))]
    date = next((c["name"] for c in cols if c["role"] == "date"), None)
    out["split"], out["time_column"] = ("time", date) if date else ("random", None)
    out["split_why"] = (f"out-of-time by {date}: the holdout is the latest period; the period before it tunes each label's threshold"
                        if date else "random holdout; each label's threshold is tuned on AutoGluon's own validation rows")
    return out


@router.get("/detect", dependencies=[require("data.preview", query("dataset_id", "dataset"))])
def detect_route(dataset_id: str, request: Request) -> dict[str, Any]:
    dataset_or_404(request, dataset_id)
    return {"dataset_id": dataset_id, **detect(frames.read_version(dataset_id))}


@router.post("/runs", status_code=201, dependencies=[require("run.train", body("dataset_id", "dataset"))])
async def create(body: MLCreate, request: Request) -> dict[str, Any]:
    return await run_in_threadpool(_create, body, request)


def _create(body: MLCreate, request: Request) -> dict[str, Any]:
    from ..engine.api import display_name

    app = request.app
    ds = dataset_or_404(request, body.dataset_id)
    df = frames.read_version(body.dataset_id)
    det = detect(df)
    spec = {k: det[k] for k in ("source", "label_column", "label_columns", "delimiter", "labels")}
    if body.label_columns:
        bad = [c for c in body.label_columns if c not in df.columns]
        if bad:
            raise HTTPException(400, f"label_columns: no column named '{bad[0]}'.")
        if len(body.label_columns) < 2:
            raise HTTPException(400, "label_columns: name at least two 0/1 columns, one per label.")
        spec = {"source": "columns", "label_column": "+".join(body.label_columns)[:60], "label_columns": body.label_columns,
                "delimiter": None, "labels": list(body.label_columns)}
    elif body.label_column:
        if body.label_column not in df.columns:
            raise HTTPException(400, f"label_column: no column named '{body.label_column}'.")
        d = body.delimiter or core.delimiter_of(df[body.label_column])
        if not d:
            raise HTTPException(400, f"'{body.label_column}' does not read as lists of labels; say the delimiter.")
        sets = core.split_labels(df[body.label_column], d)
        spec = {"source": "delimited", "label_column": body.label_column, "label_columns": [], "delimiter": d,
                "labels": sorted({x for xs in sets for x in xs})}
    if not spec["source"]:
        raise HTTPException(400, "No multi-label column was detected: name a column of delimited labels (label_column) "
                                 "or the 0/1 columns, one per label (label_columns).")
    if len(spec["labels"]) < 2 or len(spec["labels"]) > 60:
        raise HTTPException(400, f"{len(spec['labels'])} labels: multi-label needs 2 to 60.")
    used = core.label_source_columns(spec)
    drop = body.drop_columns if body.drop_columns is not None else det["drop_columns"]
    bad = [c for c in drop if c not in df.columns]
    if bad:
        raise HTTPException(400, f"No column named '{bad[0]}'.")
    drop = [c for c in drop if c not in used]
    for key in ("train_data", "tuning_data", "label", "path"):
        if key in body.fit_kwargs:
            raise HTTPException(400, f"fit_kwargs.{key} is set by the run.")
    split = det["split"] if body.split == "auto" else body.split
    time_col = body.time_column or det["time_column"]
    sp_spec: dict[str, Any] = {"strategy": "time", "time_column": time_col} if split == "time" and time_col else {"strategy": "random-stratified"}
    try:
        sp = split_record(app.state.datasets, body.dataset_id, None, sp_spec)
    except DatasetError as e:
        raise HTTPException(400, str(e)) from e
    if sp_spec.get("time_column") and time_col not in drop:
        drop.append(time_col)
    label = spec["label_column"]
    settings = app.state.settings
    run_id, run_dir = alloc_run(app)
    cfg = {
        "kind": "multilabel", "dataset_id": body.dataset_id, "label": label, "problem_type": "multilabel",
        "labels_spec": spec, "text_columns": [c for c in det["text_columns"] if c not in drop and c not in used],
        "drop_columns": drop, "presets": body.presets, "time_limit": body.time_limit, "fit_kwargs": body.fit_kwargs,
        "split": sp, "splits_path": sp["path"], "run_id": run_id, "db_path": str(settings.db_path),
        "csv_path": str(frames.version_file(body.dataset_id)), "input_ref": frames.input_ref_for(body.dataset_id), "run_dir": str(run_dir),
        "predictor_path": str(run_dir / "multilabel"),
        "detected": {k: det.get(k) for k in ("source", "label_column", "label_columns", "delimiter", "labels", "evidence",
                                              "candidates", "text_columns", "drop_columns", "split", "split_why",
                                              "multi_share", "mean_labels", "counts")},
    }
    name = body.name or f"{display_name(ds)} · {label} (multi-label)"
    store = queue_run(app, run_id, run_dir, name, body.dataset_id, label, cfg, kind="multilabel",
                      dataset_id=body.dataset_id, label=label, presets=body.presets, time_limit=body.time_limit)
    return run_out(store.get(run_id), store)


@router.get("/runs/{run_id}", dependencies=[require("run.read", path("run_id", "run"))])
def get(run_id: str, request: Request) -> dict[str, Any]:
    rec = request.app.state.runs.get(run_id)
    if not rec or rec["config"].get("kind") != "multilabel":
        raise HTTPException(404, f"No multi-label run with id '{run_id}'.")
    cfg = rec["config"]
    return {"run_id": run_id, "name": rec["name"], "status": rec["status"], "error": rec.get("error"),
            "dataset_id": rec["dataset_id"], "created_at": rec.get("created_at"),
            "config": {k: cfg.get(k) for k in ("label", "labels_spec", "text_columns", "drop_columns", "presets", "time_limit", "detected")},
            "split": {k: v for k, v in (cfg.get("split") or {}).items() if k not in ("path",)},
            "multilabel": (rec.get("result") or {}).get("multilabel")}


def card_section(run: dict[str, Any]) -> dict[str, Any] | None:
    """The model card's multi-label section (JSON); None for other runs."""
    if (run.get("config") or {}).get("kind") != "multilabel":
        return None
    m = (run.get("result") or {}).get("multilabel") or {}
    return {"label_column": m.get("label_column"), "source": m.get("source"), "delimiter": m.get("delimiter"),
            "labels": m.get("labels"), "macro_f1": m.get("macro_f1"), "micro_f1": m.get("micro_f1"),
            "hamming_loss": m.get("hamming_loss"), "baseline_hamming": m.get("baseline_hamming"), "ci": m.get("ci"),
            "subset_accuracy": m.get("subset_accuracy"), "holdout_rows": m.get("holdout_rows"),
            "threshold_rule": m.get("threshold_rule"), "skipped": [s["label"] for s in m.get("skipped") or []],
            "per_label": [{k: p.get(k) for k in ("label", "support", "threshold", "precision", "recall", "f1", "f1_ci")}
                          | {"ap": (p.get("pr") or {}).get("ap")} for p in m.get("per_label") or []]}
