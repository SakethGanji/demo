"""D2 Feature Lab: an AIDE-style journal search over a whitelisted, typed feature DSL. Never free-form code.

The DSL (every transform is JSON; nothing else can run):

    ratio      {op, a, b}               a / b (b = 0 → missing)
    diff       {op, a, b}               a − b
    agg        {op, entity, col, fn}    per-entity mean | sum | max | min | std of `col`, or count of rows (fn = count)
    date_part  {op, col, part}          year | quarter | month | dayofweek | dayofyear | hour of a date column
    bin        {op, col, k}             k quantile bins; edges from the training rows only, stored in the spec
    log        {op, col}                sign(x) · log(1 + |x|)
    cross      {op, a, b}               the interaction of two categories, "a|b"

The search (propose → score → keep, greedy, one journal):

1. Proposals come from heuristics (ratios and differences of amount-like columns ranked by a quick model's importance,
   date parts, per-entity aggregates when an entity column is named, crosses of the top categories), or are given
   (`candidates`: D4's LLM proposes through the same typed API).
2. Every candidate is checked first: the columns it reads must be known at decision time (D1's answers), not a
   leakage suspect, not the target; then its own single-column AUC (D1 `single_auc`; |Spearman ρ| for regression) must
   stay below the leakage line. A failed check rejects it whatever it scores.
3. It is scored on the **training rows only** (never the holdout): out-of-fold predictions of a fixed LightGBM
   (3 folds, no column or row subsampling, so the only change is the new column) with and without it. ΔAUC (binary),
   Δ log loss (multiclass) or ΔRMSE (regression) on the same out-of-fold rows, with a paired bootstrap 95 % CI.
4. Kept only if the lower end of its family-wise 95 % CI (Bonferroni over the candidates tried) is above 0; kept
   features join the base for the next candidate (greedy).
5. Budget: at most `max_candidates` candidates and `time_limit` seconds.

The kept transforms become a new dataset version (D1's `create_version`; lineage kind `feature_lab`, the holdout rows
unchanged), and the holdout is used once, at the end: the same LightGBM fit on all training rows, with and without the
kept features, scored on the holdout with a paired bootstrap CI.
"""

from __future__ import annotations

import json
import logging
import math
import re
import secrets
import threading
import time
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

log = logging.getLogger("ensemble.featurelab")

OPS = {
    "ratio": {"args": ["a", "b"], "text": "a ÷ b (b = 0 → missing)"},
    "diff": {"args": ["a", "b"], "text": "a − b"},
    "agg": {"args": ["entity", "col", "fn"], "text": "per-entity mean / sum / max / min / std of a column, or the entity's row count"},
    "date_part": {"args": ["col", "part"], "text": "year, quarter, month, day of week, day of year or hour of a date"},
    "bin": {"args": ["col", "k"], "text": "k quantile bins (edges from the training rows)"},
    "log": {"args": ["col"], "text": "sign(x) · log(1 + |x|)"},
    "cross": {"args": ["a", "b"], "text": "two categories combined, a|b"},
}
AGG_FNS = ("mean", "sum", "max", "min", "std", "count")
DATE_PARTS = ("year", "quarter", "month", "dayofweek", "dayofyear", "hour")
LEAK_AUC = 0.90
LEAK_RHO = 0.95
FOLDS = 3
MAX_ROWS = 30_000
B = 400
AMOUNT = re.compile(r"amt|amount|bal|limit|income|debt|loan|pay|bill|salary|value|price|credit|cost|spend|sum|total|"
                    r"revenue|asset|liab|deposit|withdraw|fee|rent|mortgage|savings", re.I)
DENOM = re.compile(r"limit|income|salary|total|bal|asset|revenue|value|price", re.I)

_jobs: dict[str, threading.Thread] = {}
_lock = threading.Lock()


# ------------------------------------------------------------------------------------------------ the DSL

def _num(df: pd.DataFrame, c: str) -> pd.Series:
    return pd.to_numeric(df[c], errors="coerce")


def name_of(t: dict[str, Any]) -> str:
    op = t["op"]
    if op == "ratio":
        return f"{t['a']}_per_{t['b']}"
    if op == "diff":
        return f"{t['a']}_minus_{t['b']}"
    if op == "agg":
        return f"{t['entity']}_count" if t["fn"] == "count" else f"{t['col']}_{t['fn']}_by_{t['entity']}"
    if op == "date_part":
        return f"{t['col']}_{t['part']}"
    if op == "bin":
        return f"{t['col']}_bin{t['k']}"
    if op == "log":
        return f"log_{t['col']}"
    return f"{t['a']}_x_{t['b']}"


def validate(df: pd.DataFrame, t: Any, target: str | None) -> dict[str, Any]:
    """A typed transform, checked and normalised (raises ValueError with a sentence)."""
    if not isinstance(t, dict) or t.get("op") not in OPS:
        raise ValueError(f"Each candidate is a typed transform: op is one of {', '.join(OPS)} (no free-form code).")
    op = t["op"]
    out: dict[str, Any] = {"op": op}
    for a in OPS[op]["args"]:
        if a not in t:
            raise ValueError(f"{op} needs '{a}'.")
        out[a] = t[a]
    extra = [k for k in t if k not in out and k not in ("name", "edges", "why")]
    if extra:
        raise ValueError(f"{op}: unknown field {extra[0]!r}.")
    cols = [out[a] for a in ("a", "b", "col", "entity") if a in out and not (op == "agg" and a == "col" and out.get("fn") == "count")]
    for c in cols:
        if c not in df.columns:
            raise ValueError(f"{op}: no column named {c!r}.")
        if c == target:
            raise ValueError(f"{op}: {c!r} is the target; a feature can't be built from it.")
    if op in ("ratio", "diff", "log", "bin") or (op == "agg" and out["fn"] != "count"):
        for c in [out[a] for a in ("a", "b", "col") if a in out]:
            if _num(df, c).notna().mean() < 0.5 or not pd.api.types.is_numeric_dtype(df[c]):
                raise ValueError(f"{op}: {c!r} is not a number column.")
    if op in ("ratio", "diff", "cross") and out["a"] == out["b"]:
        raise ValueError(f"{op}: a and b must be different columns.")
    if op == "agg":
        if out["fn"] not in AGG_FNS:
            raise ValueError(f"agg: fn is one of {', '.join(AGG_FNS)}.")
        if out["fn"] == "count":
            out["col"] = None
        g = df[out["entity"]].nunique(dropna=True)
        if g >= 0.9 * len(df):
            raise ValueError(f"agg: {out['entity']!r} has a different value on almost every row; it is not an entity column.")
    if op == "date_part":
        if out["part"] not in DATE_PARTS:
            raise ValueError(f"date_part: part is one of {', '.join(DATE_PARTS)}.")
        if pd.to_datetime(df[out["col"]], errors="coerce").notna().mean() < 0.8:
            raise ValueError(f"date_part: {out['col']!r} is not a date column.")
    if op == "bin":
        try:
            out["k"] = int(out["k"])
        except (TypeError, ValueError):
            raise ValueError("bin: k is a whole number.") from None
        if not 2 <= out["k"] <= 20:
            raise ValueError("bin: k must be 2–20.")
        if t.get("edges") is not None:
            out["edges"] = [float(e) for e in t["edges"]]
    if op == "cross":
        for c in (out["a"], out["b"]):
            if df[c].nunique(dropna=True) > 50:
                raise ValueError(f"cross: {c!r} has more than 50 values; cross two categories.")
    out["name"] = name_of(out)
    if out["name"] in df.columns:
        raise ValueError(f"{op}: a column named {out['name']!r} already exists.")
    return out


def compute(df: pd.DataFrame, t: dict[str, Any], train_mask: np.ndarray | None = None) -> pd.Series:
    """The new column for every row. `bin` fits its edges on the training rows (and stores them in `t`)."""
    op = t["op"]
    if op == "ratio":
        b = _num(df, t["b"])
        return (_num(df, t["a"]) / b.where(b != 0)).replace([np.inf, -np.inf], np.nan)
    if op == "diff":
        return _num(df, t["a"]) - _num(df, t["b"])
    if op == "agg":
        g = df.groupby(t["entity"], dropna=False)
        if t["fn"] == "count":
            return g[t["entity"]].transform("size").astype(float)
        return g[t["col"]].transform(t["fn"]).astype(float)
    if op == "date_part":
        d = pd.to_datetime(df[t["col"]], errors="coerce")
        return getattr(d.dt, t["part"]).astype(float)
    if op == "bin":
        x = _num(df, t["col"])
        if t.get("edges") is None:
            fit = x[train_mask] if train_mask is not None else x
            q = np.unique(np.nanquantile(fit.dropna(), np.linspace(0, 1, t["k"] + 1)[1:-1]))
            t["edges"] = [float(e) for e in q]
        return pd.Series(np.searchsorted(np.asarray(t["edges"]), x.to_numpy(), side="left"), index=df.index).where(x.notna()).astype(float)
    if op == "log":
        x = _num(df, t["col"])
        return np.sign(x) * np.log1p(x.abs())
    return df[t["a"]].astype(str) + "|" + df[t["b"]].astype(str)


def reads(t: dict[str, Any]) -> list[str]:
    return [t[a] for a in ("a", "b", "col", "entity") if t.get(a)]


def describe(t: dict[str, Any]) -> str:
    op = t["op"]
    if op == "ratio":
        return f"{t['a']} ÷ {t['b']}"
    if op == "diff":
        return f"{t['a']} − {t['b']}"
    if op == "agg":
        return f"rows per {t['entity']}" if t["fn"] == "count" else f"{t['fn']} of {t['col']} per {t['entity']}"
    if op == "date_part":
        return f"{t['part']} of {t['col']}"
    if op == "bin":
        return f"{t['col']} in {t['k']} quantile bins"
    if op == "log":
        return f"log(1 + |{t['col']}|)"
    return f"{t['a']} × {t['b']} (categories)"


# ------------------------------------------------------------------------------------------------ scoring

class Task:
    def __init__(self, y: pd.Series):
        k = y.nunique(dropna=True)
        from .profiling import MAX_CLASSES

        if pd.api.types.is_numeric_dtype(y) and k > MAX_CLASSES:
            self.kind, self.metric, self.label = "regression", "rmse", "RMSE"
            self.y = pd.to_numeric(y, errors="coerce").to_numpy(dtype=float)
        else:
            self.classes = sorted(y.astype(str).unique())
            yi = y.astype(str).map({c: i for i, c in enumerate(self.classes)}).to_numpy()
            if k == 2:
                self.kind, self.metric, self.label = "binary", "roc_auc", "AUC"
                counts = y.astype(str).value_counts()
                self.pos = counts.index[-1]
                self.y = (y.astype(str) == self.pos).to_numpy().astype(int)
            else:
                self.kind, self.metric, self.label = "multiclass", "log_loss", "log loss"
                self.y = yi.astype(int)

    def model(self):
        import lightgbm as lgb

        kw = dict(n_estimators=200, learning_rate=0.05, num_leaves=31, min_child_samples=20, subsample=1.0,
                  colsample_bytree=1.0, random_state=0, verbose=-1, n_jobs=4)
        return lgb.LGBMRegressor(**kw) if self.kind == "regression" else lgb.LGBMClassifier(**kw)

    def predict(self, m, X):
        if self.kind == "regression":
            return m.predict(X)
        p = m.predict_proba(X)
        return p[:, 1] if self.kind == "binary" else p

    def score(self, y, p) -> float:
        """Higher is better: AUC; −log loss; −RMSE."""
        if self.kind == "binary":
            from .engine.scorecard import auc

            return auc(y, p)
        if self.kind == "multiclass":
            pp = np.clip(p[np.arange(len(y)), y], 1e-12, 1)
            return float(np.mean(np.log(pp)))
        return -float(np.sqrt(np.mean((y - p) ** 2)))

    def natural_delta(self, d: float) -> float:
        return d  # AUC up; log loss and RMSE down, reported as the improvement (positive is better)


def frame(df: pd.DataFrame, cols: list[str]) -> pd.DataFrame:
    X = df[cols].copy()
    for c in X.columns:
        if not pd.api.types.is_numeric_dtype(X[c]):
            X[c] = X[c].astype("category")
    return X


def oof(task: Task, X: pd.DataFrame, y: np.ndarray, folds: list[tuple[np.ndarray, np.ndarray]]):
    p = np.zeros((len(X), len(task.classes))) if task.kind == "multiclass" else np.zeros(len(X))
    for tr, va in folds:
        m = task.model()
        m.fit(X.iloc[tr], y[tr])
        p[va] = task.predict(m, X.iloc[va])
    return p


def paired(task: Task, y: np.ndarray, p_new, p_base, seed: int = 0, b: int = B, tries: int = 1) -> dict[str, Any]:
    rng = np.random.default_rng(seed)
    n = len(y)
    d0 = task.score(y, p_new) - task.score(y, p_base)
    ds = []
    for _ in range(b):
        i = rng.integers(0, n, n)
        if task.kind == "binary" and y[i].min() == y[i].max():
            continue
        ds.append(task.score(y[i], p_new[i]) - task.score(y[i], p_base[i]))
    ds = np.asarray(ds)
    out = {"delta": float(d0), "ci": [float(np.quantile(ds, 0.025)), float(np.quantile(ds, 0.975))], "B": int(len(ds)),
           "se": float(ds.std(ddof=1))}
    if tries > 1:  # family-wise: Bonferroni over the candidates tried, normal interval from the bootstrap SE
        from scipy.stats import norm

        z = float(norm.ppf(1 - 0.025 / tries))
        out.update(ci=[float(d0 - z * out["se"]), float(d0 + z * out["se"])], z=z, tries=tries)
    return out


def _z(q: float) -> float:
    from scipy.stats import norm

    return float(norm.ppf(q))


def single_signal(task: Task, x: pd.Series, y: np.ndarray) -> float | None:
    """The leakage check: D1's single-column AUC (binary), |Spearman ρ| (regression)."""
    from .data_doctor import single_auc

    if task.kind == "binary":
        return single_auc(x.reset_index(drop=True), pd.Series(y, dtype=float))
    if task.kind == "regression":
        ok = x.notna().to_numpy() & np.isfinite(y)
        if ok.sum() < 50 or not pd.api.types.is_numeric_dtype(x):
            return None
        from scipy.stats import spearmanr

        r = spearmanr(x.to_numpy()[ok], y[ok]).statistic
        return None if r is None or np.isnan(r) else float(abs(r))
    return None


# ------------------------------------------------------------------------------------------------ proposals

def propose(df: pd.DataFrame, base_cols: list[str], importance: dict[str, float], entity: str | None,
            types: dict[str, str], limit: int) -> list[dict[str, Any]]:
    """Heuristic candidates, most promising first. Monotone transforms of one column (log, bins) are not proposed:
    tree models are invariant to them. D4's LLM can still send them."""
    # a quantity, not a code: numbers with > 20 distinct values (status codes such as Taiwan's PAY_0, −2…8, are left out)
    nums = [c for c in base_cols if pd.api.types.is_numeric_dtype(df[c]) and not pd.api.types.is_bool_dtype(df[c]) and df[c].nunique() > 20]
    cats = [c for c in base_cols if types.get(c) in ("category", "boolean") or (not pd.api.types.is_numeric_dtype(df[c]) and df[c].nunique() <= 50)]
    dates = [c for c in df.columns if types.get(c) == "date"]
    imp = lambda c: importance.get(c, 0.0)  # noqa: E731
    top = sorted(nums, key=imp, reverse=True)[:8]
    amounts = [c for c in nums if AMOUNT.search(str(c))]
    out: list[tuple[float, dict[str, Any], str]] = []
    pool = list(dict.fromkeys(top + sorted(amounts, key=imp, reverse=True)[:8]))
    for i, a in enumerate(pool):
        for b in pool[i + 1:]:
            both = bool(AMOUNT.search(str(a))) and bool(AMOUNT.search(str(b)))
            # the denominator: the limit / income / total-like column, else the one with the larger median
            if DENOM.search(str(b)) and not DENOM.search(str(a)):
                num, den = a, b
            elif DENOM.search(str(a)) and not DENOM.search(str(b)):
                num, den = b, a
            else:
                num, den = (a, b) if abs(df[a].median()) <= abs(df[b].median()) else (b, a)
            pri = imp(a) + imp(b) + (0.5 if both else 0.0) + (0.3 if DENOM.search(str(den)) else 0.0)
            out.append((pri + 0.01, {"op": "ratio", "a": num, "b": den}, "ratio of two " + ("amounts" if both else "important columns")))
            if both:
                out.append((pri - 0.2, {"op": "diff", "a": num, "b": den}, "difference of two amounts"))
    for d in dates:
        for part in ("month", "dayofweek", "hour"):
            out.append((0.4, {"op": "date_part", "col": d, "part": part}, "date part"))
    if entity and entity in df.columns:
        out.append((0.6, {"op": "agg", "entity": entity, "col": None, "fn": "count"}, "rows per entity"))
        for c in top[:4]:
            out.append((0.5 + imp(c), {"op": "agg", "entity": entity, "col": c, "fn": "mean"}, "per-entity average"))
    cats_top = sorted(cats, key=imp, reverse=True)[:3]
    for i, a in enumerate(cats_top):
        for b in cats_top[i + 1:]:
            out.append((imp(a) + imp(b), {"op": "cross", "a": a, "b": b}, "interaction of two categories"))
    out.sort(key=lambda t: -t[0])
    seen, cands = set(), []
    for _, t, why in out:
        try:
            v = validate(df, t, None)
        except ValueError:
            continue
        if v["name"] in seen:
            continue
        seen.add(v["name"])
        cands.append({**v, "why": why})
        if len(cands) >= limit:
            break
    return cands


# ------------------------------------------------------------------------------------------------ the search

def search(df: pd.DataFrame, target: str, holdout: np.ndarray, *, roles: list[dict[str, Any]], drops: dict[str, Any] | None,
           entity: str | None = None, candidates: list[dict[str, Any]] | None = None, max_candidates: int = 20,
           time_limit: float = 240, progress=None, seed: int = 0) -> dict[str, Any]:
    from .data_doctor import Ctx

    t0 = time.time()
    cx = Ctx(df, target, roles)
    blocked: dict[str, str] = {}
    for c in (drops or {}).get("not_known", []):
        blocked[c] = "answered: not known at decision time"
    for c in (drops or {}).get("fairness_only", []):
        blocked[c] = "fairness check only"
    for c in df.columns:
        if cx.role(c) == "leak" or any(a.get("role") == "leak" for a in cx.also(c)):
            blocked.setdefault(c, "a leakage suspect (Smart Intake)")
    base_cols = [c for c in cx.model_features() if c not in blocked and cx.types.get(c) not in ("text", "date") and c != entity]
    has_label = df[target].notna().to_numpy()
    train_idx = np.flatnonzero(~holdout & has_label)
    hold_idx = np.flatnonzero(holdout & has_label)
    task = Task(df[target].iloc[np.flatnonzero(has_label)])
    rng = np.random.default_rng(seed)
    fit_idx = np.sort(rng.choice(train_idx, MAX_ROWS, replace=False)) if len(train_idx) > MAX_ROWS else train_idx
    yall = _y(task, df[target])
    y = yall[fit_idx]
    from sklearn.model_selection import KFold, StratifiedKFold

    kf = (KFold(FOLDS, shuffle=True, random_state=seed) if task.kind == "regression"
          else StratifiedKFold(FOLDS, shuffle=True, random_state=seed))
    folds = list(kf.split(np.zeros(len(y)), y))
    work = df.iloc[fit_idx].reset_index(drop=True)
    X = frame(work, base_cols)
    p_base = oof(task, X, y, folds)
    base_score = task.score(y, p_base)
    # importance of a quick model on all training rows, for the heuristics
    m = task.model()
    m.fit(X, y)
    gain = dict(zip(base_cols, m.booster_.feature_importance("gain")))
    tot = sum(gain.values()) or 1.0
    importance = {k: float(v) / tot for k, v in gain.items()}
    given = candidates is not None
    if given:
        cands = []
        for c in candidates:
            try:
                cands.append({**validate(df, c, target), "why": str(c.get("why") or "given")})
            except ValueError as e:
                cands.append({"op": c.get("op") if isinstance(c, dict) else None, "spec": c, "invalid": str(e),
                              "name": str(c.get("op")) if isinstance(c, dict) else "?"})
        cands = cands[:max_candidates]
    else:
        cands = propose(df, base_cols, importance, entity, cx.types, max_candidates)
    journal: list[dict[str, Any]] = []
    kept: list[dict[str, Any]] = []
    train_mask = np.zeros(len(df), dtype=bool)
    train_mask[train_idx] = True
    for i, c in enumerate(cands):
        if time.time() - t0 > time_limit:
            journal.append({"step": i + 1, "name": c.get("name"), "status": "skipped", "reason": f"time budget ({time_limit:g} s) spent"})
            continue
        t1 = time.time()
        e: dict[str, Any] = {"step": i + 1, "name": c.get("name"), "transform": {k: v for k, v in c.items() if k not in ("why", "invalid", "spec")},
                             "text": describe(c) if "invalid" not in c else None, "why": c.get("why")}
        if "invalid" in c:
            e.update(status="rejected", reason=f"not a valid typed transform: {c['invalid']}", transform=c.get("spec"))
            journal.append(e)
            continue
        bad = [col for col in reads(c) if col in blocked]
        if bad:
            e.update(status="rejected", reason=f"decision-time rule: {bad[0]} is {blocked[bad[0]]}")
            journal.append(e)
            continue
        col = compute(df, c, train_mask)
        sig = single_signal(task, col.iloc[train_idx], yall[train_idx])
        e["single"] = sig
        line = LEAK_AUC if task.kind == "binary" else LEAK_RHO
        if sig is not None and sig >= line:
            e.update(status="rejected", reason=(f"leakage check: this feature alone has "
                                                + (f"AUC {sig:.3f} ≥ {line}" if task.kind == "binary" else f"|ρ| {sig:.3f} ≥ {line}")
                                                + " on the training rows; it likely encodes the outcome"
                                                + (f". Its columns ({', '.join(reads(c))}) may encode it together: check them in the Data Report"
                                                   if len(reads(c)) > 1 else "")))
            journal.append(e)
            continue
        Xc = X.copy()
        Xc[c["name"]] = col.iloc[fit_idx].reset_index(drop=True)
        if not pd.api.types.is_numeric_dtype(Xc[c["name"]]):
            Xc[c["name"]] = Xc[c["name"]].astype("category")
        p_new = oof(task, Xc, y, folds)
        d = paired(task, y, p_new, p_base, seed=seed + i + 1, tries=max(1, len(cands)))
        e.update(delta=d["delta"], ci=d["ci"], seconds=round(time.time() - t1, 2), score_before=task.score(y, p_base),
                 score_after=task.score(y, p_new))
        if d["ci"][0] > 0:
            e.update(status="kept", reason=f"the gain's family-wise 95 % CI is above 0 ([{d['ci'][0]:+.4f}, {d['ci'][1]:+.4f}])")
            kept.append(c)
            X, p_base = Xc, p_new
        else:
            e.update(status="rejected", reason=f"no clear gain: the family-wise 95 % CI [{d['ci'][0]:+.4f}, {d['ci'][1]:+.4f}] includes 0")
        journal.append(e)
        if progress:
            progress(journal)
    out: dict[str, Any] = {
        "task": task.kind, "metric": task.metric, "metric_label": task.label, "target": target,
        "positive_class": getattr(task, "pos", None), "rows": {"train": int(len(train_idx)), "scored": int(len(fit_idx)), "holdout": int(len(hold_idx))},
        "base_features": base_cols, "blocked": blocked, "entity": entity, "proposed_by": "given" if given else "heuristics",
        "base_score_oof": float(base_score), "final_score_oof": float(task.score(y, p_base)), "journal": journal,
        "kept": [{k: v for k, v in c.items() if k != "why"} for c in kept],
        "rules": {
            "score": f"Out-of-fold {task.label} of a fixed LightGBM (200 trees, {FOLDS} folds) on the training rows only; the holdout is never used in the search.",
            "keep": (f"Kept only if the gain's 95 % CI is above 0: a paired bootstrap ({B} resamples of the out-of-fold rows), "
                     f"Bonferroni-corrected for the {len(cands)} candidates tried (z = {_z(1 - 0.025 / max(1, len(cands))):.2f}). "
                     "Greedy: kept features join the base for the next candidate."),
            "leakage": (f"Rejected if the feature alone has AUC ≥ {LEAK_AUC} (D1 single_auc) on the training rows" if task.kind == "binary"
                        else f"Rejected if |Spearman ρ| with the target ≥ {LEAK_RHO}" if task.kind == "regression" else "No single-column leakage check for multiclass targets")
                       + "; columns not known at decision time, fairness-only or leakage suspects are never read.",
            "budget": f"At most {max_candidates} candidates and {time_limit:g} s.",
            "dsl": {k: v["text"] for k, v in OPS.items()},
        },
        "improvement_oof": task.natural_delta(float(task.score(y, p_base) - base_score)),
    }
    if kept:
        out["holdout"] = holdout_check(df, task, yall, train_idx, hold_idx, base_cols, kept, train_mask, seed)
    out["seconds"] = round(time.time() - t0, 1)
    return out


def _y(task: Task, y: pd.Series) -> np.ndarray:
    if task.kind == "regression":
        return pd.to_numeric(y, errors="coerce").to_numpy(dtype=float)
    if task.kind == "binary":
        return (y.astype(str) == task.pos).to_numpy().astype(int)
    return y.astype(str).map({c: i for i, c in enumerate(task.classes)}).fillna(-1).to_numpy().astype(int)


def holdout_check(df, task: Task, yall, train_idx, hold_idx, base_cols, kept, train_mask, seed) -> dict[str, Any]:
    """The holdout, once: the same LightGBM fit on all training rows with and without the kept features."""
    full = df.copy()
    for c in kept:
        full[c["name"]] = compute(full, c, train_mask)
    cols = base_cols + [c["name"] for c in kept]
    Xa, Xb = frame(full, base_cols), frame(full, cols)
    ma, mb = task.model(), task.model()
    ma.fit(Xa.iloc[train_idx], yall[train_idx])
    mb.fit(Xb.iloc[train_idx], yall[train_idx])
    yh = yall[hold_idx]
    pa, pb = task.predict(ma, Xa.iloc[hold_idx]), task.predict(mb, Xb.iloc[hold_idx])
    d = paired(task, yh, pb, pa, seed=seed + 999, b=1000)
    sa, sb = task.score(yh, pa), task.score(yh, pb)
    nat = (lambda s: -s) if task.kind != "binary" else (lambda s: s)
    return {"before": nat(sa), "after": nat(sb), "delta": d["delta"], "ci": d["ci"], "B": d["B"], "rows": int(len(hold_idx)),
            "model": "the search's LightGBM, fit on all training rows (train a run on the new version for the full ladder)",
            "note": "Improvement in " + task.label + (" (higher is better)" if task.kind == "binary" else " (lower is better; the delta is the reduction)")}


# ------------------------------------------------------------------------------------------------ jobs

def job_dir(ds_dir: Path) -> Path:
    d = ds_dir / "featurelab"
    d.mkdir(parents=True, exist_ok=True)
    return d


def read_job(ds_dir: Path, job_id: str) -> dict[str, Any] | None:
    p = job_dir(ds_dir) / f"{job_id}.json"
    return json.loads(p.read_text()) if p.exists() else None


def list_jobs(ds_dir: Path) -> list[dict[str, Any]]:
    out = []
    for p in sorted(job_dir(ds_dir).glob("fl_*.json"), key=lambda p: p.stat().st_mtime, reverse=True):
        j = json.loads(p.read_text())
        out.append({k: j.get(k) for k in ("id", "status", "started_at", "finished_at", "target", "version", "error")}
                   | {"kept": len((j.get("result") or {}).get("kept") or []), "tried": len((j.get("result") or {}).get("journal") or j.get("journal") or [])})
    return out


def _jsonable(v: Any) -> Any:
    if isinstance(v, (np.integer,)):
        return int(v)
    if isinstance(v, (np.floating,)):
        return None if math.isnan(v) else float(v)
    if isinstance(v, float) and math.isnan(v):
        return None
    return str(v)


def write_job(ds_dir: Path, job: dict[str, Any]) -> None:
    p = job_dir(ds_dir) / f"{job['id']}.json"
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(job, default=_jsonable))
    tmp.replace(p)


def start(ds_dir: Path, body: dict[str, Any], work) -> dict[str, Any]:
    """Run `work(job, progress)` in a thread; the job's JSON is the state GET reads."""
    from .datasets import now

    job = {"id": "fl_" + secrets.token_hex(4), "status": "running", "started_at": now(), "request": body, "journal": []}
    write_job(ds_dir, job)

    def progress(journal):
        job["journal"] = journal
        write_job(ds_dir, job)

    def run():
        try:
            res = work(job, progress)
            job.update(status="succeeded", finished_at=now(), **res)
        except Exception as e:  # noqa: BLE001
            log.exception("feature lab failed")
            job.update(status="failed", finished_at=now(), error=f"{type(e).__name__}: {e}"[:400])
        write_job(ds_dir, job)

    th = threading.Thread(target=run, name=f"feature-lab-{job['id']}", daemon=True)
    with _lock:
        _jobs[job["id"]] = th
    th.start()
    return job
