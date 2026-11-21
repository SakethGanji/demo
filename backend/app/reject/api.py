"""G7 routes: reject inference (zero-config from the G0 roles) and its through-the-door comparison.

    GET  /api/reject-inference/detect?dataset_id=   the outcome, the decision column and its approved value, the columns
                                                    left out, the split, and whether a through-the-door check is possible
    POST /api/reject-inference/runs                 {dataset_id, ...overrides} → a run (kind reject_inference)
    GET  /api/reject-inference/runs/{run_id}        each method against approved-only, on the TTD holdout
"""

from __future__ import annotations

from typing import Any, Literal

import pandas as pd
from fastapi import APIRouter, HTTPException, Request
from fastapi.concurrency import run_in_threadpool
from ..authz import body, require  # P19
from pydantic import BaseModel, Field

from ..datasets import DatasetError
from ..engine.api import alloc_run, queue_run, run_out, split_record
from ..http import dataset_or_404
from .. import frames
from . import core

router = APIRouter(prefix="/api/reject-inference")
TRUTH_FILE = "reject_truth.json"


class RICreate(BaseModel):
    dataset_id: str
    name: str | None = None
    label: str | None = Field(None, description="The yes/no outcome, blank for declined applicants; default: the detected target.")
    decision_column: str | None = Field(None, description="Approved/declined; default: the decision role, else 'declined' = no outcome.")
    approved_value: str | None = None
    k: float = Field(core.DEFAULT_K, ge=1.0, le=5.0, description="A declined applicant is k× as likely to default as an approved one scored the same.")
    drop_columns: list[str] | None = Field(None, description="Left out of every model; default: IDs, dates, entities, constants, leaks, protected.")
    split: Literal["auto", "random", "time"] = "auto"
    time_column: str | None = None
    presets: str | None = "medium_quality"
    time_limit: float | None = Field(20, description="Seconds per model (five fits: approved-only, acceptance, three methods).")
    fit_kwargs: dict[str, Any] = Field(default_factory=dict)


def detect(df: pd.DataFrame, has_truth: bool) -> dict[str, Any]:
    from ..intake import EXCLUDED, NOT_INPUTS, detect as roles_of

    cols = roles_of(df)["columns"]
    f = core.find(df, cols)
    used = {f["label"], f["decision_column"]}
    out: dict[str, Any] = {**f}
    out["drop_columns"] = [c["name"] for c in cols if c["name"] not in used and
                           (c["role"] in EXCLUDED or c["role"] in NOT_INPUTS or c["role"] in ("entity", "date")
                            or any(a["role"] == "leak" and a["confidence"] >= 0.85 for a in c["also"]))]
    date = next((c["name"] for c in cols if c["role"] == "date"), None)
    out["split"], out["time_column"] = ("time", date) if date else ("random", None)
    out["split_why"] = (f"out-of-time by {date}: the holdout is every applicant of the latest period, approved or declined"
                        if date else "random: every applicant has the same chance to be in the holdout, approved or declined")
    if f["label"]:
        A = core.decided(df, f)
        out["approval_rate"] = float(A.mean())
        out["declined"] = int((~A).sum())
    out["through_the_door"] = has_truth
    out["k"] = core.DEFAULT_K
    return out


def _truth_path(app: Any, dataset_id: str) -> str | None:
    p = app.state.datasets.dir(dataset_id) / TRUTH_FILE
    return str(p) if p.exists() else None


@router.get("/detect")
def detect_route(dataset_id: str, request: Request) -> dict[str, Any]:
    dataset_or_404(request, dataset_id)
    df = frames.read_version(dataset_id)
    return {"dataset_id": dataset_id, **detect(df, _truth_path(request.app, dataset_id) is not None)}


@router.post("/runs", status_code=201, dependencies=[require("run.train", body("dataset_id", "dataset"))])
async def create(body: RICreate, request: Request) -> dict[str, Any]:
    return await run_in_threadpool(_create, body, request)


def _create(body: RICreate, request: Request) -> dict[str, Any]:
    from ..engine.api import display_name

    app = request.app
    ds = dataset_or_404(request, body.dataset_id)
    df = frames.read_version(body.dataset_id)
    truth = _truth_path(app, body.dataset_id)
    det = detect(df, truth is not None)
    label = body.label or det["label"]
    if not label:
        raise HTTPException(400, "label: no yes/no outcome was detected; name one.")
    if label not in df.columns:
        raise HTTPException(400, f"label: no column named '{label}'.")
    if df[label].nunique(dropna=True) != 2:
        raise HTTPException(400, f"'{label}' must be a yes/no outcome (two values).")
    found = {**det, "label": label}
    if label != det["label"]:
        vals = sorted(df[label].dropna().unique().tolist(), key=str)
        found["positive_class"] = vals[-1].item() if hasattr(vals[-1], "item") else vals[-1]
    if body.decision_column:
        if body.decision_column not in df.columns:
            raise HTTPException(400, f"decision_column: no column named '{body.decision_column}'.")
        av = (str(body.approved_value), None) if body.approved_value else core.decision_values(df[body.decision_column])
        if not av:
            raise HTTPException(400, f"'{body.decision_column}': say which value means approved (approved_value).")
        found.update(decision_column=body.decision_column, approved_value=av[0])
    A = core.decided(df, found)
    if A.all() or not A.any():
        raise HTTPException(400, "Reject inference needs both approved and declined applicants; "
                                 + ("every row is approved." if A.all() else "no row is approved."))
    if df.loc[A, label].notna().mean() < 0.9:
        raise HTTPException(400, f"Most approved applicants have no '{label}': is that the right outcome and decision?")
    if (~A).sum() < 50:
        raise HTTPException(400, f"Only {int((~A).sum())} declined applicants: too few to infer from.")
    drop = body.drop_columns if body.drop_columns is not None else det["drop_columns"]
    bad = [c for c in drop if c not in df.columns]
    if bad:
        raise HTTPException(400, f"No column named '{bad[0]}'.")
    drop = [c for c in drop if c not in (label, found.get("decision_column"))]
    for key in ("train_data", "tuning_data", "label", "path"):
        if key in body.fit_kwargs:
            raise HTTPException(400, f"fit_kwargs.{key} is set by the run.")
    split = det["split"] if body.split == "auto" else body.split
    time_col = body.time_column or det["time_column"]
    # The split is over every applicant (approved or declined), drawn for the decision so both are in the holdout.
    spec: dict[str, Any] = {"strategy": "random-stratified"}
    split_on = found.get("decision_column") or label
    if split == "time" and time_col:
        spec = {"strategy": "time", "time_column": time_col}
    try:
        sp = split_record(app.state.datasets, body.dataset_id, split_on, spec)
    except DatasetError as e:
        raise HTTPException(400, str(e)) from e
    if time_col and spec.get("time_column") and time_col not in drop:
        drop.append(time_col)
    settings = app.state.settings
    run_id, run_dir = alloc_run(app)
    cfg = {
        "kind": "reject_inference", "dataset_id": body.dataset_id, "label": label, "problem_type": "binary",
        "positive_class": found["positive_class"], "found": {k: found.get(k) for k in ("label", "decision_column", "approved_value", "declined_value")},
        "k": body.k, "drop_columns": drop, "presets": body.presets, "time_limit": body.time_limit, "fit_kwargs": body.fit_kwargs,
        "split": sp, "splits_path": sp["path"], "run_id": run_id, "db_path": str(settings.db_path),
        "csv_path": str(frames.version_file(body.dataset_id)), "input_ref": frames.input_ref_for(body.dataset_id), "run_dir": str(run_dir), "truth_path": truth,
        "predictor_path": str(run_dir / "reject_inference"),
        "detected": {k: det.get(k) for k in ("label", "decision_column", "approved_value", "drop_columns", "split", "split_why",
                                              "evidence", "approval_rate", "declined", "through_the_door")},
    }
    name = body.name or f"{display_name(ds)} · reject inference"
    store = queue_run(app, run_id, run_dir, name, body.dataset_id, label, cfg, kind="reject_inference",
                      dataset_id=body.dataset_id, label=label, presets=body.presets, time_limit=body.time_limit)
    return run_out(store.get(run_id), store)


@router.get("/runs/{run_id}")
def get(run_id: str, request: Request) -> dict[str, Any]:
    rec = request.app.state.runs.get(run_id)
    if not rec or rec["config"].get("kind") != "reject_inference":
        raise HTTPException(404, f"No reject-inference run with id '{run_id}'.")
    cfg = rec["config"]
    return {"run_id": run_id, "name": rec["name"], "status": rec["status"], "error": rec.get("error"),
            "dataset_id": rec["dataset_id"], "created_at": rec.get("created_at"),
            "config": {k: cfg.get(k) for k in ("label", "positive_class", "found", "k", "drop_columns", "presets", "time_limit", "detected")},
            "split": {k: v for k, v in (cfg.get("split") or {}).items() if k != "path"},
            "reject_inference": (rec.get("result") or {}).get("reject_inference")}


def card_section(runs: list[dict[str, Any]]) -> dict[str, Any] | None:
    """The model card's reject-inference paragraph: the latest succeeded reject-inference run on the version's table
    (the card's model is trained on approved applicants only, so this says how far that can be trusted on everyone)."""
    r = next((x for x in runs if (x.get("config") or {}).get("kind") == "reject_inference" and x.get("status") == "succeeded"), None)
    if not r:
        return None
    ri = (r.get("result") or {}).get("reject_inference") or {}
    t = ri.get("ttd") or {}
    ms = t.get("methods") or {}

    def brief(m: str) -> dict[str, Any] | None:
        c = ms.get(m)
        if not c:
            return None
        return {"auc": c.get("auc"), "log_loss": c.get("log_loss"), "mean_p": c.get("mean_p"), "bad_rate": c.get("bad_rate"),
                "delta_auc": c.get("delta_auc"), "delta_log_loss": c.get("delta_log_loss"), "verdict": c.get("verdict")}

    return {"run_id": r["id"], "k": ri.get("k"), "through_the_door": bool(t), "n": t.get("n"), "best": t.get("best"),
            "better": t.get("better"), "methods": {m: brief(m) for m in core.METHODS} if t else None,
            "declined_shift": ri.get("shift"), "assumption": f"a declined applicant is k = {ri.get('k')}× as likely to default "
                                                               "as an approved one the approved-only model scores the same"}
