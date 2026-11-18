"""One-row serving (F3): what a deployed predictor pays per call, and how to make it pay less.

A one-row `predict_proba` on a text predictor costs two things besides the models: AutoGluon's feature pipeline
(`AutoMLPipelineFeatureGenerator`: types, fill, categories, text statistics and ~1,000–3,000 n-gram columns) and
per-call pandas overhead. On support tickets the pipeline alone takes ~9–13 ms per row, whatever model follows.

- `prune_pipeline(p, model)`: keep only the pipeline outputs the served model and the models it needs read
  (`model.features`, unioned over `get_minimum_model_set`), stage by stage from the last, with AutoGluon's own
  `_remove_features_out` on each generator (the mechanism AutoGluon uses after fit to drop unused features). A
  generator left with no outputs is dropped. Predictions are unchanged (checked on the holdout by the build); a model
  that reads raw text only (TF-IDF → logistic regression) skips the n-gram and statistics stages altogether.
- `pruned(p, model)`: the same on a deep copy of the pipeline, swapped in for the duration of a measurement, so
  every model of a run can be measured as it would be served without writing a copy per model.
- `warm(p)`: persist the served models in memory and make one call, so a worker's first real call is not the slow one.
- `one_row(fn, X)` and `batches(fn, X)`: p50 / p95 wall time of one-row calls on holdout rows, and µs per row for
  batch sizes 1 → 1,000.
"""

from __future__ import annotations

import copy
import time
from contextlib import contextmanager
from typing import Any, Callable

import numpy as np
import pandas as pd


def _pct(xs: list[float], q: float) -> float | None:
    return float(np.percentile(np.asarray(xs, dtype=float), q)) if len(xs) else None


def needed_features(p, model: str) -> set[str]:
    """Pipeline outputs read by `model` and every model it needs (stack features are not pipeline outputs)."""
    tr = p._trainer
    try:
        deps = list(tr.get_minimum_model_set(model))
    except Exception:  # noqa: BLE001
        deps = [model]
    out: set[str] = set()
    for m in deps:
        try:
            feats = tr.load_model(m).features
        except Exception:  # noqa: BLE001
            feats = None
        if feats is None:  # a model that reads every feature
            return set(p._learner.feature_generator.features_out)
        out |= set(feats)
    return out & set(p._learner.feature_generator.features_out)


def prune_generator(fg, keep: set[str]) -> dict[str, Any]:
    """Prune a fitted AutoGluon pipeline (Bulk/Pipeline generator) to the outputs in `keep`, in place."""
    n_out0, in0 = len(fg.features_out), list(fg.features_in)
    keep = set(keep) & set(fg.features_out)
    drop_out = [f for f in fg.features_out if f not in keep]
    if fg._post_generators:  # not used by the default pipeline; only prune what we know how to
        return {"pruned": False, "reason": "pipeline has post-generators", "features_in": in0, "n_features_out": n_out0}
    need = keep
    stages_before = [len(s) for s in fg.generators]
    for i in reversed(range(len(fg.generators))):
        stage, need_in = [], set()
        for g in fg.generators[i]:
            drop = [f for f in g.features_out if f not in need]
            if drop:
                g._remove_features_out(drop)
            if g.features_out:
                stage.append(g)
                need_in |= set(g.features_in)
        fg.generators[i] = stage
        need = need_in
    drop_in = [f for f in fg.features_in if f not in need]
    if drop_out:
        fg.feature_metadata = fg.feature_metadata.remove_features(features=drop_out)
        fg.feature_metadata_real = fg.feature_metadata_real.remove_features(features=drop_out)
        fg.features_out = fg.feature_metadata.get_features()
    if drop_in:
        fg.feature_metadata_in = fg.feature_metadata_in.remove_features(features=drop_in)
        fg.features_in = [f for f in fg.features_in if f not in drop_in]
        if fg._pre_astype_generator:
            fg._pre_astype_generator._remove_features_out(drop_in)
    return {"pruned": bool(drop_out), "features_in": list(fg.features_in), "dropped_in": drop_in,
            "n_features_out_before": n_out0, "n_features_out": len(fg.features_out),
            "generators_before": int(sum(stages_before)), "generators": int(sum(len(s) for s in fg.generators))}


def prune_pipeline(p, model: str | None = None) -> dict[str, Any]:
    """Prune the predictor's own pipeline to what `model` (default: the served model) reads. In place."""
    model = model or p.model_best
    return {"model": model, **prune_generator(p._learner.feature_generator, needed_features(p, model))}


@contextmanager
def pruned(p, model: str):
    """The predictor with a pruned copy of its pipeline swapped in (restored on exit)."""
    lr = p._learner
    orig = lr.feature_generator
    fg = copy.deepcopy(orig)
    info = prune_generator(fg, needed_features(p, model))
    lr.feature_generator = fg
    try:
        yield info
    finally:
        lr.feature_generator = orig


def predict_fn(p, model: str | None = None) -> Callable[[pd.DataFrame], Any]:
    if p.problem_type in ("binary", "multiclass"):
        return lambda d: p.predict_proba(d, model=model)
    return lambda d: p.predict(d, model=model)


def warm(p, X: pd.DataFrame | None = None, models: str | list[str] = "best") -> dict[str, Any]:
    """Persist the models in memory and make one-row calls, so the first real call does not load from disk."""
    t0 = time.perf_counter()
    try:
        persisted = p.persist(models=models, with_ancestors=True)
    except Exception:  # noqa: BLE001 — persisting is an optimisation
        persisted = []
    if X is None or not len(X):
        X = pd.DataFrame([{c: np.nan for c in p.original_features}])
    fn = predict_fn(p)
    calls = []
    for i in range(3):
        t = time.perf_counter()
        try:
            fn(X.iloc[[i % len(X)]])
        except Exception:  # noqa: BLE001 — an all-missing row some pipelines reject; warming is best effort
            break
        calls.append((time.perf_counter() - t) * 1e3)
    return {"persisted": list(persisted or []), "warm_calls_ms": calls, "seconds": time.perf_counter() - t0}


def one_row(fn: Callable[[pd.DataFrame], Any], X: pd.DataFrame, n: int = 60, budget_s: float = 3.0,
            warmup: int = 3) -> dict[str, Any]:
    """p50 / p95 wall time (ms) of `n` one-row calls on successive holdout rows, after `warmup` calls."""
    for i in range(min(warmup, len(X))):
        fn(X.iloc[[i]])
    xs: list[float] = []
    t_end = time.time() + budget_s
    for i in range(n):
        row = X.iloc[[i % len(X)]]
        t0 = time.perf_counter()
        fn(row)
        xs.append((time.perf_counter() - t0) * 1e3)
        if time.time() > t_end and len(xs) >= 20:
            break
    return {"calls": len(xs), "ms_p50": _pct(xs, 50), "ms_p95": _pct(xs, 95), "ms_max": max(xs) if xs else None}


def batches(fn: Callable[[pd.DataFrame], Any], X: pd.DataFrame, sizes: tuple[int, ...] = (1, 10, 100, 1000),
            repeats: int = 5, budget_s: float = 4.0) -> list[dict[str, Any]]:
    """For each batch size b: wall time per call (ms, p50 / p95 over repeats) and µs per row."""
    out = []
    for b in sizes:
        xb = X.iloc[:b]
        if len(xb) < b:
            xb = pd.concat([xb] * int(np.ceil(b / max(1, len(xb)))), ignore_index=True).iloc[:b]
        fn(xb)
        xs: list[float] = []
        t_end = time.time() + budget_s / len(sizes)
        for r in range(repeats if b > 1 else repeats * 6):
            off = (r * b) % max(1, len(X) - b) if len(X) > b else 0
            part = X.iloc[off:off + b] if len(X) >= b else xb
            t0 = time.perf_counter()
            fn(part)
            xs.append((time.perf_counter() - t0) * 1e3)
            if time.time() > t_end and len(xs) >= 3:
                break
        p50, p95 = _pct(xs, 50), _pct(xs, 95)
        out.append({"batch": b, "calls": len(xs), "ms_p50": p50, "ms_p95": p95,
                    "us_per_row_p50": None if p50 is None else p50 * 1e3 / b,
                    "rows_per_s": None if not p50 else b / (p50 / 1e3)})
    return out


def pareto(rows: list[dict[str, Any]], x: str, y: str, greater: bool) -> list[str]:
    """Models no other model beats on both latency `x` (lower is better) and score `y`."""
    pts = [r for r in rows if r.get(x) is not None and r.get(y) is not None]
    pts.sort(key=lambda r: (r[x], -r[y] if greater else r[y]))
    out, best = [], None
    for r in pts:
        s = r[y]
        if best is None or (s > best if greater else s < best):
            out.append(r["model"])
            best = s
    return out
