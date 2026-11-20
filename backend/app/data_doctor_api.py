"""D1 Data Doctor routes.

    GET  /api/datasets/{id}/data-doctor          every check (seconds); starts the label-quality job once per target
    POST /api/datasets/{id}/data-doctor/apply    {transforms, by?} → a new dataset version with lineage (original untouched)
    GET  /api/datasets/{id}/decision-time        the stored answers
    POST /api/datasets/{id}/decision-time        {known: {column: bool}, fairness_only: [column], by?}

The answers live in data/datasets/<id>/decisions.json. Columns answered "not known at decision time" and the
fairness-only columns are dropped from every run started on the dataset (`run_drops`, read by POST /api/runs) unless
the run says `use_decision_time: false`; the run records what was dropped and why.
"""

from __future__ import annotations

import json
from typing import Any

import numpy as np
from fastapi import APIRouter, HTTPException, Request
from fastapi.concurrency import run_in_threadpool

from . import data_doctor as dd
from .datasets import DatasetError, DatasetStore, now
from .http import dataset_or_404

router = APIRouter(prefix="/api")


def _store(request: Request) -> DatasetStore:
    return request.app.state.datasets


def answers(store: DatasetStore, dataset_id: str) -> dict[str, Any]:
    p = store.dir(dataset_id) / "decisions.json"
    return json.loads(p.read_text()) if p.exists() else {}


def run_drops(store: DatasetStore, dataset_id: str, columns: list[str]) -> dict[str, Any] | None:
    """What the stored answers drop from a run: {not_known, fairness_only, columns, answered_at, by}, or None."""
    a = answers(store, dataset_id)
    if not a.get("answered_at"):
        return None
    not_known = [c for c, v in (a.get("known") or {}).items() if v is False and c in columns]
    fair = [c for c in a.get("fairness_only") or [] if c in columns]
    return {"not_known": not_known, "fairness_only": fair, "columns": list(dict.fromkeys(not_known + fair)),
            "answered_at": a.get("answered_at"), "by": a.get("by"), "inherited_from": a.get("inherited_from")}


def _roles(store: DatasetStore, dataset_id: str, df) -> list[dict[str, Any]]:
    from .intake import detect

    return detect(df)["columns"]


def _report(request: Request, dataset_id: str) -> dict[str, Any]:
    rec = dataset_or_404(request, dataset_id)
    store = _store(request)
    df = store.load_frame(dataset_id)
    roles = _roles(store, dataset_id, df)
    target = rec["target"]
    csv = store.csv_path(dataset_id)
    labels = dd.label_state(store.dir(dataset_id), target, csv)
    task_ok = target is not None and not (df[target].dtype.kind in "fi" and df[target].nunique() > dd.MAX_CLASSES)
    if labels is None and task_ok and df[target].nunique(dropna=True) >= 2:
        labels = dd.start_labels(store.dir(dataset_id), target, csv, roles)
    out = dd.report(df, target, roles, answers(store, dataset_id), labels)
    lin = store.lineage(dataset_id) or {}
    out.update(dataset_id=dataset_id, name=rec["name"],
               lineage={k: lin.get(k) for k in ("parent", "root", "kind", "fixes", "applied_at", "by") if k in lin} or None,
               transforms=dd.TRANSFORMS)
    return out


@router.get("/datasets/{dataset_id}/data-doctor")
async def data_doctor(dataset_id: str, request: Request) -> dict[str, Any]:
    """The Data Report: every check with its severity, numbers, rule, example rows and fix; the decision-time question."""
    try:
        return await run_in_threadpool(_report, request, dataset_id)
    except (ValueError, DatasetError) as e:
        raise HTTPException(400, str(e)) from e


@router.post("/datasets/{dataset_id}/data-doctor/apply", status_code=201)
async def apply_fixes(dataset_id: str, request: Request, body: dict[str, Any]) -> dict[str, Any]:
    """Apply typed fixes to a copy: a new dataset version whose lineage names the parent and the fixes. The holdout is
    the parent's holdout rows that were kept, so scores stay comparable. The decision-time answers carry over."""
    body = body or {}
    return await run_in_threadpool(_apply, request, dataset_id, body.get("transforms"), str(body.get("by") or "you"))


def _apply(request: Request, dataset_id: str, transforms: Any, by: str) -> dict[str, Any]:
    from .main import _detail

    rec = dataset_or_404(request, dataset_id)
    store = _store(request)
    df = store.load_frame(dataset_id)
    target = rec["target"]
    try:
        new_df, keep, effects = dd.apply(df, transforms, target)
    except ValueError as e:
        raise HTTPException(400, str(e)) from e
    if len(new_df) < 2:
        raise HTTPException(400, "These fixes leave fewer than two rows.")
    try:
        _, sp, _ = store.ensure_splits(dataset_id, target)
    except DatasetError as e:
        raise HTTPException(400, str(e)) from e
    if len(sp["fold"]) != len(df):
        raise HTTPException(400, "The dataset's split does not match its rows.")
    new_pos = np.cumsum(keep) - 1
    new_sp = {**sp, "holdout": sorted(int(new_pos[i]) for i in sp["holdout"] if keep[i]),
              "fold": [int(v) for v, k in zip(sp["fold"], keep) if k], "derived_from": dataset_id}
    parent_lin = store.lineage(dataset_id) or {}
    roots = np.asarray(parent_lin.get("root_rows") or np.arange(len(df)))
    if len(roots) != len(df):
        roots = np.arange(len(df))
    at = now()
    lineage = {"parent": dataset_id, "root": parent_lin.get("root") or dataset_id, "kind": "data_doctor",
               "fixes": effects, "applied_at": at, "by": by, "rows_before": int(len(df)), "rows_after": int(len(new_df)),
               "columns_before": int(df.shape[1]), "columns_after": int(new_df.shape[1]),
               "root_rows": [int(x) for x in roots[keep]], "changes": []}
    base = rec["name"].split(" · fixed")[0]
    n_ver = sum(1 for d in request.app.state.db.list_datasets() if (d["profile"].get("lineage") or {}).get("root") == lineage["root"]
                and (d["profile"].get("lineage") or {}).get("kind") == "data_doctor") + 1
    try:
        new = store.create_version(dataset_id, new_df, new_sp, name=f"{base} · fixed v{n_ver}",
                                   note=f"Data Doctor: {len(effects)} fix{'es' if len(effects) != 1 else ''} applied to {dataset_id}",
                                   lineage=lineage)
    except DatasetError as e:
        raise HTTPException(400, str(e)) from e
    a = answers(store, dataset_id)
    if a:
        (store.dir(new["id"]) / "decisions.json").write_text(json.dumps({**a, "inherited_from": a.get("inherited_from") or dataset_id}))
    return {"dataset": _detail(new), "parent": dataset_id, "effects": effects,
            "rows_before": lineage["rows_before"], "rows_after": lineage["rows_after"],
            "columns_added": [c for c in new_df.columns if c not in df.columns],
            "columns_removed": [c for c in df.columns if c not in new_df.columns]}


@router.get("/datasets/{dataset_id}/decision-time")
def get_decision_time(dataset_id: str, request: Request) -> dict[str, Any]:
    dataset_or_404(request, dataset_id)
    return {"dataset_id": dataset_id, **answers(_store(request), dataset_id)}


@router.post("/datasets/{dataset_id}/decision-time")
def set_decision_time(dataset_id: str, request: Request, body: dict[str, Any]) -> dict[str, Any]:
    """Store the answers: {known: {column: true|false}, fairness_only: [column], by}. Columns with known = false and the
    fairness-only columns are dropped from later runs on this dataset."""
    rec = dataset_or_404(request, dataset_id)
    body = body or {}
    known = body.get("known") or {}
    fair = body.get("fairness_only") or []
    if not isinstance(known, dict) or not isinstance(fair, list):
        raise HTTPException(400, 'Send {"known": {"<column>": true|false}, "fairness_only": ["<column>"]}.')
    store = _store(request)
    cols = set(store.load_frame(dataset_id).columns)
    bad = [c for c in [*known, *fair] if c not in cols]
    if bad:
        raise HTTPException(400, f"No column named {bad[0]!r}.")
    if rec["target"] in fair or known.get(rec["target"]) is False:
        raise HTTPException(400, f"'{rec['target']}' is the target; it can't be left out.")
    out = {"known": {c: bool(v) for c, v in known.items()}, "fairness_only": list(dict.fromkeys(fair)),
           "answered_at": now(), "by": str(body.get("by") or "you")}
    (store.dir(dataset_id) / "decisions.json").write_text(json.dumps(out))
    return {"dataset_id": dataset_id, **out, "drops": run_drops(store, dataset_id, list(cols))}
