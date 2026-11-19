"""F16 routes: weak-slice discovery on a run's holdout (see slices.py for the statistics).

  POST /api/runs/{id}/slices                 search (synchronous: counts and sums, milliseconds to seconds)
  GET  /api/runs/{id}/slices                 the latest search (run with the defaults if there is none, or if the
                                             holdout predictions changed since, e.g. a new threshold)
  GET  /api/runs/{id}/slices/{sid}/rows      the slice's holdout rows: features, truth, prediction and loss
  POST /api/runs/{id}/slices/{sid}/shap      Kernel SHAP (the existing worker) for a sample of the slice's rows and as
                                             many rows from the rest: mean φ per feature inside vs outside
  GET  /api/runs/{id}/slices/{sid}/pool      how many of the run's training rows fall in the slice (the F13 queue's
                                             pool when it is pre-filtered to the slice)

The run payload carries `slices` (`slices/1`), the summary F17 reads.
"""

from __future__ import annotations

import asyncio
import json
import time
from pathlib import Path
from typing import Any, Literal

import numpy as np
import pandas as pd
from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.concurrency import run_in_threadpool
from pydantic import BaseModel, Field

from ..splits import holdout_mask
from . import explain, heavy
from . import slices as sl
from .jsonsafe import jsonable
from .store import now
from ..http import run_or_404

router = APIRouter(prefix="/api")


class SliceRequest(BaseModel):
    min_support: int | None = Field(None, ge=5, description="Smallest slice (rows of the holdout). Default: max(30, 2% of the holdout).")
    bins: int = Field(sl.DEFAULTS["bins"], ge=2, le=50, description="Quantile bins per number.")
    max_levels: int = Field(sl.DEFAULTS["max_levels"], ge=2, le=500, description="Category values kept (the rest form '(other)').")
    alpha: float = Field(sl.DEFAULTS["alpha"], gt=0, lt=1, description="False-discovery rate (Benjamini–Hochberg).")
    pairs: bool = Field(True, description="Also test every pair of feature values.")
    null_permutations: int = Field(sl.DEFAULTS["null_permutations"], ge=0, le=200, description="Shuffles of the losses for the null check.")
    loss: Literal["auto", "error", "log_loss", "absolute_error", "squared_error", "cost", "pinball"] = "auto"
    columns: list[str] | None = Field(None, description="Features to search (default: every feature of the run).")


class ShapRequest(BaseModel):
    rows: int = Field(6, ge=1, le=200, description="Rows sampled from the slice, and as many from the rest.")
    target: str | None = None


def _rec(request: Request, run_id: str) -> dict[str, Any]:
    rec = run_or_404(request, run_id)
    if rec["status"] != "succeeded":
        raise HTTPException(409, f"Run {run_id} is {rec['status']}; it needs to finish first.")
    cfg = rec["config"]
    if cfg.get("kind") in ("forecast", "anomaly"):
        raise HTTPException(400, "Weak slices are searched on a tabular run's holdout; this is a "
                                 f"{cfg.get('kind')} run.")
    return rec


def _path(rec: dict[str, Any]) -> Path:
    return Path(rec["config"]["run_dir"]) / "slices.json"


def _types(request: Request, rec: dict[str, Any]) -> dict[str, str]:
    ds = request.app.state.db.get_dataset(rec["dataset_id"])
    types = {c["name"]: c["type"] for c in ((ds or {}).get("profile") or {}).get("columns", [])}
    cfg = rec["config"]
    types.update({k: v for k, v in (cfg.get("column_types") or {}).items() if v})
    for c in cfg.get("text_columns") or []:
        types[c] = "text"
    return types


def _frame(rec: dict[str, Any], hp: pd.DataFrame, columns: list[str]) -> pd.DataFrame:
    cols = set(columns)
    data = pd.read_csv(rec["config"]["csv_path"], usecols=lambda c: c in cols)
    return data.iloc[hp["row"].to_numpy(dtype=int)].reset_index(drop=True)


def _loss(rec: dict[str, Any], hp: pd.DataFrame, loss: str) -> tuple[np.ndarray, dict[str, Any]]:
    res = rec.get("result") or {}
    pt = res.get("problem_type")
    thr = (res.get("threshold") or {}).get("value") if pt == "binary" else None
    return sl.row_loss(hp, problem_type=pt, metric=(res.get("eval_metric") or {}).get("name"), cost=rec["config"].get("cost"),
                       positive=res.get("positive_class"), threshold=None if thr is None else float(thr), loss=loss)


def _group_columns(request: Request, rec: dict[str, Any]) -> set[str]:
    ds = request.app.state.db.get_dataset(rec["dataset_id"])
    res = rec.get("result") or {}
    return {c["column"] for c in explain.group_candidates(rec["config"], res.get("features") or [], (ds or {}).get("profile"))}


def compute(request: Request, rec: dict[str, Any], body: SliceRequest) -> dict[str, Any]:
    run_dir = Path(rec["config"]["run_dir"])
    hp_path = run_dir / "holdout_predictions.csv"
    if not hp_path.exists():
        raise HTTPException(404, "This run has no saved holdout predictions.")
    res = rec.get("result") or {}
    features = list(res.get("features") or [])
    if body.columns:
        bad = [c for c in body.columns if c not in features]
        if bad:
            raise HTTPException(400, f"Not a feature of this run: {', '.join(bad)}.")
        features = list(body.columns)
    if not features:
        raise HTTPException(400, "This run has no features to slice by.")
    t0 = time.time()
    hp = explain.load_holdout(run_dir)
    try:
        loss, info = _loss(rec, hp, body.loss)
        frame = _frame(rec, hp, features)
        out = sl.search(frame, hp, loss, info, features, _types(request, rec), min_support=body.min_support,
                        bins=body.bins, max_levels=body.max_levels, alpha=body.alpha, pairs=body.pairs,
                        null_permutations=body.null_permutations)
    except sl.SliceError as e:
        raise HTTPException(400, str(e)) from e
    groups = _group_columns(request, rec)
    for s in out["slices"]:
        s["fairness_columns"] = [c for c in s["columns"] if c in groups]
    out.update({"run_id": rec["id"], "model": res.get("best_model"), "problem_type": res.get("problem_type"),
                "eval_metric": (res.get("eval_metric") or {}).get("name"), "label": rec["label"],
                "threshold": (res.get("threshold") or {}).get("value") if res.get("problem_type") == "binary" else None,
                "holdout_sha": sl.file_sha(hp_path), "computed_at": now(), "seconds": time.time() - t0,
                "request": body.model_dump()})
    out = jsonable(out)
    _path(rec).write_text(json.dumps(out))
    request.app.state.runs.merge_result(rec["id"], slices=sl.summary(out))
    return out


def _view(out: dict[str, Any], rows: bool = False) -> dict[str, Any]:
    """The response: slice row lists only when asked (they can be long); their counts are `n`."""
    if rows:
        return out
    return {**out, "slices": [{k: v for k, v in s.items() if k != "rows"} for s in out["slices"]],
            "summary": sl.summary(out)}


def _latest(request: Request, rec: dict[str, Any]) -> dict[str, Any]:
    p = _path(rec)
    hp_path = Path(rec["config"]["run_dir"]) / "holdout_predictions.csv"
    if p.exists():
        out = json.loads(p.read_text())
        if hp_path.exists() and out.get("holdout_sha") == sl.file_sha(hp_path):
            return out
        body = SliceRequest(**(out.get("request") or {}))  # predictions changed (a new threshold): search again
    else:
        body = SliceRequest()
    return compute(request, rec, body)


@router.post("/runs/{run_id}/slices")
async def search_slices(run_id: str, body: SliceRequest, request: Request) -> dict[str, Any]:
    """F16: search the holdout for feature-value slices (single values and pairs, numbers binned) whose loss is
    significantly worse than the rest: Welch t per slice, Benjamini–Hochberg over all slices tested, Δ with its 95%
    interval, ranked by the interval's lower bound."""
    rec = _rec(request, run_id)
    out = await run_in_threadpool(compute, request, rec, body)
    return _view(out)


@router.get("/runs/{run_id}/slices")
async def get_slices(run_id: str, request: Request) -> dict[str, Any]:
    rec = _rec(request, run_id)
    return _view(await run_in_threadpool(_latest, request, rec))


def _slice(request: Request, rec: dict[str, Any], sid: str) -> tuple[dict[str, Any], dict[str, Any]]:
    out = _latest(request, rec)
    s = next((s for s in out["slices"] if s["id"] == sid), None)
    if s is None:
        raise HTTPException(404, f"No weak slice {sid!r} in the latest search of run {rec['id']}.")
    return out, s


@router.get("/runs/{run_id}/slices/{sid}/rows")
async def slice_rows(run_id: str, sid: str, request: Request, limit: int | None = Query(None, ge=1),
                     offset: int = Query(0, ge=0)) -> dict[str, Any]:
    """The slice's holdout rows (data.csv row numbers) with their features, truth, prediction and per-row loss,
    worst first."""
    rec = _rec(request, run_id)

    def work():
        out, s = _slice(request, rec, sid)
        hp = explain.load_holdout(Path(rec["config"]["run_dir"]))
        loss, _ = _loss(rec, hp, (out.get("request") or {}).get("loss", "auto"))
        hp = hp.assign(loss=loss)
        sub = hp[hp["row"].isin(set(s["rows"]))].sort_values("loss", ascending=False, kind="stable")
        feats = list((rec.get("result") or {}).get("features") or [])
        data = pd.read_csv(rec["config"]["csv_path"], usecols=lambda c: c in set(feats))
        total = len(sub)
        sub = sub.iloc[offset: offset + limit if limit else None]
        items = []
        for _, r in sub.iterrows():
            row = int(r["row"])
            items.append({"row": row, "y_true": jsonable(r["y_true"]), "y_pred": jsonable(r["y_pred"]), "loss": float(r["loss"]),
                          "p": {c[2:]: float(r[c]) for c in hp.columns if c.startswith("p_")} or None,
                          "features": jsonable(data.loc[row].to_dict())})
        return {"run_id": run_id, "slice": {k: v for k, v in s.items() if k != "rows"}, "loss": out["loss"],
                "total": total, "offset": offset, "rows": items}

    return await run_in_threadpool(work)


@router.post("/runs/{run_id}/slices/{sid}/shap")
async def slice_shap(run_id: str, sid: str, body: ShapRequest, request: Request) -> dict[str, Any]:
    """Kernel SHAP (the S4 worker, cached per row) for `rows` rows sampled from the slice and as many from the rest of
    the holdout (seeded): the mean φᵢ per feature in each, and the difference. What pushes the model differently
    inside the slice."""
    rec = _rec(request, run_id)
    out, s = await run_in_threadpool(_slice, request, rec, sid)
    hp = explain.load_holdout(Path(rec["config"]["run_dir"]))
    inside = np.array(sorted(s["rows"]), dtype=int)
    rest = np.setdiff1d(hp["row"].to_numpy(dtype=int), inside)
    rng = np.random.default_rng(16)
    a = sorted(int(x) for x in rng.choice(inside, size=min(body.rows, len(inside)), replace=False))
    b = sorted(int(x) for x in rng.choice(rest, size=min(body.rows, len(rest)), replace=False))
    t0 = time.time()
    try:
        got = await asyncio.gather(*[request.app.state.heavy.run(heavy.shap_holdout_row, rec["config"], r, body.target) for r in a + b])
        res_a, res_b = list(got[:len(a)]), list(got[len(a):])
    except explain.ExplainError as e:
        raise HTTPException(400, str(e)) from e

    def mean_phi(rs):
        acc: dict[str, list[float]] = {}
        for r in rs:
            for c in r["contributions"]:
                acc.setdefault(c["feature"], []).append(float(c["phi"]))
        return acc

    pa, pb = mean_phi(res_a), mean_phi(res_b)
    feats = []
    for f in sorted(set(pa) | set(pb)):
        xa, xb = np.array(pa.get(f, [0.0])), np.array(pb.get(f, [0.0]))
        feats.append({"feature": f, "phi_slice": float(xa.mean()), "phi_rest": float(xb.mean()),
                      "abs_slice": float(np.abs(xa).mean()), "abs_rest": float(np.abs(xb).mean()),
                      "diff": float(xa.mean() - xb.mean()), "in_slice_definition": f in s["columns"]})
    feats.sort(key=lambda d: -abs(d["diff"]))
    first = (res_a or res_b)[0]
    return {"run_id": run_id, "slice": {k: v for k, v in s.items() if k != "rows"}, "model": first["model"],
            "target": first["target"], "output": first["output"], "rows_slice": a, "rows_rest": b,
            "base_value": first["base_value"],
            "fx_slice": float(np.mean([r["fx"] for r in res_a])) if res_a else None,
            "fx_rest": float(np.mean([r["fx"] for r in res_b])) if res_b else None,
            "features": feats, "per_row": [{"row": r["row"], "in_slice": r["row"] in set(a), "fx": r["fx"],
                                            "y_true": r.get("y_true"), "cached": r.get("cached")} for r in res_a + res_b],
            "background_size": first["background_size"], "nsamples": first["nsamples"], "seconds": time.time() - t0,
            "method": first["method"]}


@router.get("/runs/{run_id}/slices/{sid}/pool")
async def slice_pool(run_id: str, sid: str, request: Request) -> dict[str, Any]:
    """The run's training rows (outside the holdout) inside the slice: what the F13 labelling queue can pick from when
    it is pre-filtered to this slice."""
    rec = _rec(request, run_id)

    def work():
        _, s = _slice(request, rec, sid)
        cfg = rec["config"]
        cols = {c["column"] for c in s["conditions"]} | {rec["label"]}
        data = pd.read_csv(cfg["csv_path"], usecols=lambda c: c in cols)
        splits = json.loads(Path(cfg["splits_path"]).read_text())
        is_h = holdout_mask(splits, len(data))
        m = sl.slice_mask(data, s["conditions"]) & ~is_h
        return {"run_id": run_id, "slice_id": sid, "label": s["label"], "conditions": s["conditions"],
                "train_rows": int((~is_h).sum()), "in_slice": int(m.sum()),
                "labelled_in_slice": int((m & data[rec["label"]].notna().to_numpy()).sum())}

    return await run_in_threadpool(work)
