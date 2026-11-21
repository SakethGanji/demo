"""D2 Feature Lab routes.

    POST /api/datasets/{id}/feature-lab            {target?, entity?, candidates?, max_candidates?, time_limit?, apply?, by?}
                                                   → 202 {job}; the search runs in a thread
    GET  /api/datasets/{id}/feature-lab            the dataset's jobs, newest first, plus the DSL
    GET  /api/datasets/{id}/feature-lab/{job_id}   one job: status, journal (live), kept transforms, holdout check, version

`candidates` (typed transforms only) replaces the heuristic proposals: D4's language model proposes through this.
With `apply` (default true) the kept transforms become a new dataset version (lineage kind `feature_lab`) with the same
holdout rows; the decision-time answers carry over.
"""

from __future__ import annotations

import json
from typing import Any

from fastapi import APIRouter, HTTPException, Request

from . import featurelab as fl
from . import frames
from .data_doctor_api import answers, run_drops
from .datasets import DatasetError, now
from .http import dataset_or_404
from .splits import holdout_mask

router = APIRouter(prefix="/api")


@router.get("/datasets/{dataset_id}/feature-lab")
def list_feature_lab(dataset_id: str, request: Request) -> dict[str, Any]:
    dataset_or_404(request, dataset_id)
    store = request.app.state.datasets
    return {"dataset_id": dataset_id, "jobs": fl.list_jobs(store.dir(dataset_id)),
            "dsl": {k: {"args": v["args"], "text": v["text"]} for k, v in fl.OPS.items()},
            "agg_fns": list(fl.AGG_FNS), "date_parts": list(fl.DATE_PARTS)}


@router.get("/datasets/{dataset_id}/feature-lab/{job_id}")
def get_feature_lab(dataset_id: str, job_id: str, request: Request) -> dict[str, Any]:
    dataset_or_404(request, dataset_id)
    j = fl.read_job(request.app.state.datasets.dir(dataset_id), job_id)
    if not j:
        raise HTTPException(404, f"No Feature Lab job '{job_id}' on {dataset_id}.")
    return j


@router.post("/datasets/{dataset_id}/feature-lab", status_code=202)
def start_feature_lab(dataset_id: str, request: Request, body: dict[str, Any] | None = None) -> dict[str, Any]:
    body = body or {}
    rec = dataset_or_404(request, dataset_id)
    store = request.app.state.datasets
    df = frames.read_version(dataset_id).reset_index(drop=True)  # P07: rows by position (split masks)
    target = body.get("target") or rec["target"]
    if not target or target not in df.columns:
        raise HTTPException(400, "Name a target column (the dataset has none set).")
    if df[target].nunique(dropna=True) < 2:
        raise HTTPException(400, f"'{target}' has fewer than two values.")
    entity = body.get("entity")
    if entity is not None and entity not in df.columns:
        raise HTTPException(400, f"entity: no column named {entity!r}.")
    if entity == target:
        raise HTTPException(400, "entity: the target can't be the entity column.")
    try:
        max_c = int(body.get("max_candidates", 20))
        limit = float(body.get("time_limit", 240))
    except (TypeError, ValueError):
        raise HTTPException(400, "max_candidates and time_limit are numbers.") from None
    if not 1 <= max_c <= 100 or not 5 <= limit <= 3600:
        raise HTTPException(400, "max_candidates is 1–100 and time_limit 5–3600 seconds.")
    cands = body.get("candidates")
    if cands is not None:
        if not isinstance(cands, list) or not cands:
            raise HTTPException(400, "candidates is a non-empty list of typed transforms ({op, ...}).")
        if any(not isinstance(c, dict) or c.get("op") not in fl.OPS for c in cands):
            bad = next(c for c in cands if not isinstance(c, dict) or c.get("op") not in fl.OPS)
            raise HTTPException(400, f"Not a typed transform: {json.dumps(bad)[:120]}. op is one of {', '.join(fl.OPS)}; free-form code is never run.")
    try:
        _, sp, _ = store.ensure_splits(dataset_id, target)
    except DatasetError as e:
        raise HTTPException(400, str(e)) from e
    if len(sp["fold"]) != len(df):
        raise HTTPException(400, "The dataset's split does not match its rows.")
    from .intake import detect

    roles = detect(df)["columns"]
    drops = run_drops(store, dataset_id, list(df.columns))
    apply = body.get("apply", True) is not False
    from .catalog import governance as G

    # DL3 (m3): the version this job makes records a real name, taken now (the job runs on another thread)
    by = str(body.get("by") or "").strip()[:80] or G.current_actor()
    if not by and apply:
        raise HTTPException(400, "Say who is running Feature Lab: send by (or the X-Ensemble-Actor header); the version it makes records the name.")

    def work(job, progress):
        res = fl.search(df, target, holdout_mask(sp, len(df)), roles=roles, drops=drops, entity=entity, candidates=cands,
                        max_candidates=max_c, time_limit=limit, progress=progress)
        out: dict[str, Any] = {"result": res, "journal": res["journal"], "target": target}
        if apply and res["kept"]:
            out["version"] = _version(request, rec, df, sp, res, by)
        return out

    job = fl.start(store.dir(dataset_id), {**body, "target": target}, work)
    return job


def _version(request: Request, rec: dict[str, Any], df, sp: dict[str, Any], res: dict[str, Any], by: str) -> dict[str, Any]:
    """The kept transforms applied to a copy: a new dataset version, every row kept, the same holdout."""
    from .main import _detail

    store = request.app.state.datasets
    dataset_id = rec["id"]
    new_df = df.copy()
    effects = []
    for t in res["kept"]:
        new_df[t["name"]] = fl.compute(new_df, t)  # bins carry their training-row edges, so this is the same column
        effects.append({"kind": "feature_lab", "transform": t, "text": fl.describe(t), "column": t["name"],
                        "missing": int(new_df[t["name"]].isna().sum())})
    root = (store.lineage(dataset_id) or {}).get("root") or dataset_id
    extra = {"kind": "feature_lab", "fixes": effects, "transforms": res["kept"], "applied_at": now(), "by": by,
             "columns_before": int(df.shape[1]), "columns_after": int(new_df.shape[1]),
             "search": {"metric": res["metric"], "improvement_oof": res["improvement_oof"], "holdout": res.get("holdout")}}
    base = rec["name"].split(" · features")[0]
    n_ver = sum(1 for d in request.app.state.db.list_datasets() if (d["profile"].get("lineage") or {}).get("root") == root
                and (d["profile"].get("lineage") or {}).get("kind") == "feature_lab") + 1
    new = store.derive(dataset_id, new_df, None, "feature_lab", [], splits=sp, extra=extra, by=by,  # P1: every row kept
                       name=f"{base} · features v{n_ver}",
                       note=f"Feature Lab: {len(effects)} feature{'s' if len(effects) != 1 else ''} added to {dataset_id}")
    a = answers(store, dataset_id)
    if a:
        (store.dir(new["id"]) / "decisions.json").write_text(json.dumps({**a, "inherited_from": a.get("inherited_from") or dataset_id}))
    d = _detail(new)
    return {"id": new["id"], "name": new["name"], "columns_added": [t["name"] for t in res["kept"]], "rows": int(len(new_df)),
            "holdout_rows": int(len(sp["holdout"])), "profile_rows": d.get("rows")}
