"""G6 · Time to event (survival): Cox proportional hazards and a random survival forest, from scikit-survival.

AutoGluon has no survival models, so this is its own small engine path (like anomaly). Each row is one subject followed
from the start of its observation for `duration` time units, ending either in the event (`event` = 1: the account closed)
or censored (`event` = 0: still open when the data ends, or lost to follow-up).

    survival      S(t | x) = P(T > t | x)
    Cox PH        h(t | x) = h₀(t) · exp(βᵀz),   S(t | x) = S₀(t)^exp(βᵀz)     z = the standardised / one-hot inputs
                  β by maximum partial likelihood (Breslow ties, a tiny ridge α); HR_j = exp(β_j) per unit of z_j
    forest        S(t | x) from the Nelson–Aalen estimates in the leaves x falls into, averaged over B trees

Evaluation is on the F4/F6 holdout (rows no model saw):
    Harrell's C   = P(r̂_i > r̂_j | T_i < T_j, i had the event)   (the larger risk score fails first), bootstrap 95 % CI
    AUC(τ)        cumulative/dynamic AUC at each horizon τ: cases T ≤ τ with the event, controls T > τ, IPCW-weighted
    Brier(τ)      mean (1{T > τ} − Ŝ(τ | x))², IPCW-weighted
    calibration   the holdout in four risk groups (quartiles of r̂); per group, the mean predicted Ŝ(t) against the
                  Kaplan–Meier Ŝ_KM(t) of the same rows, with its 95 % log-log CI

The served model is the one with the lower integrated Brier score on a validation slice (the split's tuning slice, else
training fold 0), never chosen on the holdout.
"""

from __future__ import annotations

import json
import re
import time
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

GROUPS = ("low", "medium-low", "medium-high", "high")
MAX_CATS = 30
EXAMPLES = 6
_CACHE: dict[str, tuple[float, dict[str, Any]]] = {}

# Explicit time-to-event names make a strong pair; tenure-like ones may be a snapshot of time so far (G0's caveat).
EXPLICIT = re.compile(r"(duration|time_to|survival|days_to|weeks_to|months_to|years_to|follow_?up|_time$|^time$|time_on_study)", re.I)
UNITS = (("day", (90, 180, 365)), ("week", (13, 26, 52)), ("month", (12, 24, 36)), ("year", (1, 2, 3)))


# ------------------------------------------------------------------------------------------- detection (G0 roles)

def unit_of(name: str) -> str | None:
    return next((u for u, _ in UNITS if u in name.lower()), None)


def default_horizons(name: str, t: pd.Series, e: pd.Series | None = None) -> list[float]:
    """Round horizons in the duration's unit (months → 12, 24, 36), kept where enough subjects are still followed;
    else the quartiles of the observed times."""
    t = pd.to_numeric(t, errors="coerce").dropna()
    hi = float(t.quantile(0.9)) if len(t) else 0.0
    u = unit_of(name)
    if u:
        hs = [h for h in dict(UNITS)[u] if h < hi]
        if hs:
            return [float(h) for h in hs]
    q = sorted({float(np.round(t.quantile(p))) for p in (0.25, 0.5, 0.75)} - {0.0})
    return q or [float(t.median())]


def pairs(df: pd.DataFrame, cols: list[dict[str, Any]], target: str | None) -> dict[str, Any]:
    """The duration and event columns, with their evidence and whether the pair reads as time to event ('strong': an
    explicit time-to-event name) or may be a snapshot of time so far ('weak': tenure-like names, G0's caveat)."""
    def roles(c: dict[str, Any]) -> list[str]:
        return [c["role"]] + [a["role"] for a in c["also"]]

    def ev(c: dict[str, Any], role: str) -> list[str]:
        if c["role"] == role:
            return list(c["evidence"])
        return next((list(a["evidence"]) for a in c["also"] if a["role"] == role), [])

    durs, evts = [], []
    for c in cols:
        s = df[c["name"]]
        if "duration" in roles(c) and pd.api.types.is_numeric_dtype(s) and s.nunique() > 5 and (s.dropna() >= 0).all():
            strong = bool(EXPLICIT.search(c["name"]))
            durs.append({"column": c["name"], "strong": strong,
                         "evidence": ev(c, "duration") + ([f"'{c['name']}' names a time to an event"] if strong else
                                                          [f"'{c['name']}' may be time so far (a snapshot), not time to the event"])})
        is_target_event = c["name"] == target and c["unique"] == 2 and set(s.dropna().unique()) <= {0, 1, True, False}
        if "event" in roles(c) or (is_target_event and re.search(r"(event|churn|default|died|death|closed|attrit|lapsed|prepa|observed)", c["name"], re.I)):
            rate = float(pd.to_numeric(s, errors="coerce").mean())
            if 0.02 <= rate <= 0.98:
                evts.append({"column": c["name"], "rate": rate,
                             "evidence": (ev(c, "event") or [f"0/1 flag named '{c['name']}'"]) + [f"{rate * 100:.1f}% had the event, "
                                                                                                   f"{(1 - rate) * 100:.1f}% censored"]})
    durs.sort(key=lambda d: not d["strong"])
    return {"durations": durs, "events": evts}


# ---------------------------------------------------------------------------------------------------------- design

def _design(df: pd.DataFrame, inputs: list[str], numeric: list[str]):
    from sklearn.compose import ColumnTransformer
    from sklearn.impute import SimpleImputer
    from sklearn.pipeline import Pipeline
    from sklearn.preprocessing import OneHotEncoder, StandardScaler

    cats = [c for c in inputs if c not in numeric]
    pre = ColumnTransformer([
        ("num", Pipeline([("impute", SimpleImputer(strategy="median")), ("scale", StandardScaler())]), numeric),
        ("cat", Pipeline([("impute", SimpleImputer(strategy="constant", fill_value="missing")),
                          ("onehot", OneHotEncoder(drop="first", handle_unknown="ignore", sparse_output=False))]), cats),
    ], verbose_feature_names_out=False)
    pre.fit(_frame_in(df, inputs, numeric))
    return pre


def _frame_in(df: pd.DataFrame, inputs: list[str], numeric: list[str]) -> pd.DataFrame:
    X = df.reindex(columns=inputs).copy()
    for c in inputs:
        X[c] = pd.to_numeric(X[c], errors="coerce") if c in numeric else X[c].map(lambda v: "missing" if pd.isna(v) else str(v)).astype(object)
    return X


def _y(t: np.ndarray, e: np.ndarray):
    from sksurv.util import Surv

    return Surv.from_arrays(event=e.astype(bool), time=t.astype(float))


def cox_se(Z: np.ndarray, t: np.ndarray, e: np.ndarray, beta: np.ndarray, alpha: float) -> np.ndarray:
    """Standard errors of β̂ from the observed information of the Breslow partial likelihood (plus the ridge α):
    I = Σ_events [S₂/S₀ − (S₁/S₀)(S₁/S₀)ᵀ] + αI, with S_k the risk-set sums of w·z^⊗k, w = exp(βᵀz)."""
    o = np.argsort(-t, kind="stable")
    Z, t, e = Z[o], t[o], e[o].astype(bool)
    w = np.exp(Z @ beta)
    S0 = np.cumsum(w)
    S1 = np.cumsum(w[:, None] * Z, axis=0)
    S2 = np.cumsum(w[:, None, None] * (Z[:, :, None] * Z[:, None, :]), axis=0)
    # The risk set at time t_i is every row with t ≥ t_i: in descending order, the last row of t_i's tie block.
    last = np.searchsorted(-t, -t, side="right") - 1
    p = Z.shape[1]
    info = alpha * np.eye(p)
    for i in np.flatnonzero(e):
        j = last[i]
        m = S1[j] / S0[j]
        info += S2[j] / S0[j] - np.outer(m, m)
    try:
        return np.sqrt(np.clip(np.diag(np.linalg.inv(info)), 0, None))
    except np.linalg.LinAlgError:
        return np.full(p, np.nan)


def _surv_at(fns, times: np.ndarray) -> np.ndarray:
    """Step functions from predict_survival_function → an (n, len(times)) array: Ŝ = 1 before the first event time,
    the last value held past the domain."""
    out = np.empty((len(fns), len(times)))
    for i, f in enumerate(fns):
        lo, hi = f.domain
        out[i] = np.where(times < lo, 1.0, f(np.clip(times, lo, hi)))
    return out


def surv_matrix(model, Z: np.ndarray, times: np.ndarray) -> np.ndarray:
    return _surv_at(model.predict_survival_function(Z), times)


def km(t: np.ndarray, e: np.ndarray, grid: np.ndarray) -> dict[str, list[float]]:
    """Kaplan–Meier Ŝ_KM on the grid with its 95 % log-log CI (right-continuous steps)."""
    from sksurv.nonparametric import kaplan_meier_estimator

    x, s, ci = kaplan_meier_estimator(e.astype(bool), t.astype(float), conf_type="log-log")
    idx = np.searchsorted(x, grid, side="right") - 1
    pick = lambda a: np.where(idx >= 0, a[np.clip(idx, 0, None)], 1.0)  # noqa: E731
    return {"s": pick(s).tolist(), "lo": pick(ci[0]).tolist(), "hi": pick(ci[1]).tolist()}


def c_index(t: np.ndarray, e: np.ndarray, r: np.ndarray) -> float:
    from sksurv.metrics import concordance_index_censored

    return float(concordance_index_censored(e.astype(bool), t.astype(float), r)[0])


def c_index_ci(t: np.ndarray, e: np.ndarray, r: np.ndarray, B: int = 200, seed: int = 0) -> list[float]:
    rng = np.random.default_rng(seed)
    n = len(t)
    vals = []
    for _ in range(B):
        i = rng.integers(0, n, n)
        if e[i].any():
            vals.append(c_index(t[i], e[i], r[i]))
    return [float(np.percentile(vals, 2.5)), float(np.percentile(vals, 97.5))]


def median_time(S: np.ndarray, grid: np.ndarray) -> float | None:
    """The first grid time where Ŝ ≤ 0.5; None when Ŝ stays above 0.5 over the follow-up."""
    below = np.flatnonzero(S <= 0.5)
    return float(grid[below[0]]) if len(below) else None


# ------------------------------------------------------------------------------------------------------------- run

def run(cfg: dict[str, Any], run_dir: Path, emit, merge) -> dict[str, Any]:
    import joblib
    from sksurv.ensemble import RandomSurvivalForest
    from sksurv.linear_model import CoxPHSurvivalAnalysis
    from sksurv.metrics import brier_score, cumulative_dynamic_auc, integrated_brier_score

    from ..engine.worker import GROUP_FOLD_COLUMN, load_frames

    dur, evt = cfg["duration_column"], cfg["event_column"]
    lcfg = {**cfg, "label": evt}
    train, holdout, info, tuning = load_frames(lcfg)
    for f in (train, holdout, tuning):
        if f is not None:
            f.dropna(subset=[dur], inplace=True)
    emit("data_ready", **info)
    merge(data=info)
    splits = json.loads(Path(cfg["splits_path"]).read_text())
    inputs = [c for c in train.columns if c not in (dur, evt, GROUP_FOLD_COLUMN)]
    numeric = [c for c in inputs if pd.api.types.is_numeric_dtype(train[c]) and not pd.api.types.is_bool_dtype(train[c])]
    dropped_cats = [c for c in inputs if c not in numeric and train[c].nunique() > MAX_CATS]
    inputs = [c for c in inputs if c not in dropped_cats]

    # Validation slice for choosing the served model: the split's tuning slice, else training fold 0 (F4's folds).
    if tuning is not None and len(tuning) >= 200:
        fit_rows, val, val_from = train, tuning, "the split's tuning slice"
    else:
        fold = np.asarray(splits["fold"], dtype=int)[train.index.to_numpy()]
        fit_rows, val, val_from = train[fold != 0], train[fold == 0], "training fold 0 (held back from both fits)"
    ttr, etr = fit_rows[dur].to_numpy(float), fit_rows[evt].to_numpy(int)
    pre = _design(fit_rows, inputs, numeric)
    Zt = pre.transform(_frame_in(fit_rows, inputs, numeric))
    names = list(pre.get_feature_names_out())
    ytr = _y(ttr, etr)
    alpha = float(cfg.get("ridge") or 1e-3)

    emit("surv_model_started", model="cox", rows=int(len(fit_rows)))
    emit("log", level="info", logger="ensemble", message=f"survival: Cox PH on {len(fit_rows):,} rows, {Zt.shape[1]} terms")
    t0 = time.time()
    cox = CoxPHSurvivalAnalysis(alpha=alpha, ties="breslow").fit(Zt, ytr)
    cox_s = time.time() - t0
    emit("surv_model_finished", model="cox", seconds=cox_s)

    n_trees = int(cfg.get("n_estimators") or 200)
    emit("surv_model_started", model="rsf", rows=int(len(fit_rows)))
    emit("log", level="info", logger="ensemble", message=f"survival: random survival forest, {n_trees} trees")
    t0 = time.time()
    rsf = RandomSurvivalForest(n_estimators=n_trees, min_samples_leaf=int(cfg.get("min_samples_leaf") or 15), max_features="sqrt",
                               n_jobs=-1, random_state=0).fit(Zt, ytr)
    rsf_s = time.time() - t0
    emit("surv_model_finished", model="rsf", seconds=rsf_s)
    models = {"cox": cox, "rsf": rsf}

    # The served model: the lower integrated Brier score on the validation slice (it rewards ranking and calibrated
    # Ŝ(t) together; a C-index alone cannot see a forest's curves shrunk toward the average).
    Zv = pre.transform(_frame_in(val, inputs, numeric))
    tv, ev_ = val[dur].to_numpy(float), val[evt].to_numpy(int)
    val_c = {k: c_index(tv, ev_, m.predict(Zv)) for k, m in models.items()}
    vt = np.unique(np.quantile(tv[ev_ == 1], np.linspace(0.05, 0.9, 20)))
    vt = vt[(vt > tv.min()) & (vt < tv.max())]
    val_ibs = {k: float(integrated_brier_score(ytr, _y(tv, ev_), surv_matrix(m, Zv, vt), vt)) for k, m in models.items()}
    served = min(val_ibs, key=val_ibs.get)

    # Holdout: every number below is on rows neither model saw.
    emit("log", level="info", logger="ensemble", message="survival: holdout C-index, AUC(τ), Brier(τ) and calibration by risk group")
    th, eh = holdout[dur].to_numpy(float), holdout[evt].to_numpy(int)
    Zh = pre.transform(_frame_in(holdout, inputs, numeric))
    yh = _y(th, eh)
    hmax = float(min(th.max(), ttr.max()))
    horizons = [float(h) for h in (cfg.get("horizons") or default_horizons(dur, train[dur])) if 0 < h < hmax]
    grid = np.unique(np.concatenate([np.linspace(0, float(np.quantile(th, 0.98)), 61), horizons]))
    per: dict[str, Any] = {}
    risk_h: dict[str, np.ndarray] = {}
    S_h: dict[str, np.ndarray] = {}
    for k, m in models.items():
        r = m.predict(Zh)
        risk_h[k] = r
        S = surv_matrix(m, Zh, grid)
        S_h[k] = S
        hs = np.array(horizons)
        out: dict[str, Any] = {"c_index": c_index(th, eh, r), "c_index_ci": c_index_ci(th, eh, r), "val_c_index": val_c[k],
                               "val_ibs": val_ibs[k]}
        try:
            Sh = surv_matrix(m, Zh, hs)
            auc, mean_auc = cumulative_dynamic_auc(ytr, yh, r, hs)
            _, bs = brier_score(ytr, yh, Sh, hs)
            out["at"] = [{"t": float(h), "auc": float(a), "brier": float(b), "mean_S": float(Sh[:, i].mean())}
                         for i, (h, a, b) in enumerate(zip(hs, auc, bs))]
            out["mean_auc"] = float(mean_auc)
        except ValueError as e:  # horizons outside the follow-up
            out["at"], out["at_error"] = [], str(e)
        per[k] = out

    # Calibration by risk group (quartiles of each model's own risk score on the holdout): mean Ŝ(t) against KM.
    def calibrate(k: str) -> tuple[list[dict[str, Any]], dict[str, Any], list[float], np.ndarray]:
        r = risk_h[k]
        cuts = np.quantile(r, [0.25, 0.5, 0.75]).tolist()
        g = np.searchsorted(cuts, r, side="right")
        groups, worst, inside, cells = [], 0.0, 0, 0
        for gi, gname in enumerate(GROUPS):
            msk = g == gi
            if msk.sum() < 20:
                continue
            k_ = km(th[msk], eh[msk], grid)
            pred = S_h[k][msk].mean(axis=0)
            at = []
            for h in horizons:
                j = int(np.searchsorted(grid, h))
                d = float(pred[j] - k_["s"][j])
                ok = k_["lo"][j] - 1e-9 <= pred[j] <= k_["hi"][j] + 1e-9
                worst, inside, cells = max(worst, abs(d)), inside + int(ok), cells + 1
                at.append({"t": h, "pred": float(pred[j]), "km": k_["s"][j], "lo": k_["lo"][j], "hi": k_["hi"][j], "diff": d, "in_ci": bool(ok)})
            groups.append({"group": gname, "n": int(msk.sum()), "events": int(eh[msk].sum()), "risk_from": float(r[msk].min()),
                           "risk_to": float(r[msk].max()), "pred": pred.round(5).tolist(), "km": k_, "at": at})
        cal = {"max_abs_diff": worst, "in_ci": inside, "cells": cells, "ok": bool(worst <= 0.05 or inside == cells),
               "rule": "|mean Ŝ − Ŝ_KM| ≤ 0.05 at every horizon in every risk group, or Ŝ inside the KM 95% CI"}
        return groups, cal, cuts, g

    for k in models:
        per[k]["calibration"] = calibrate(k)[1]
    groups, calibration, cuts, g = calibrate(served)
    r = risk_h[served]
    km_all = km(th, eh, grid)

    # Feature effects: Cox hazard ratios with Wald CIs (from the information matrix), per unit of the original input.
    se = cox_se(Zt, ttr, etr, cox.coef_, alpha)
    scale = dict(zip(numeric, pre.named_transformers_["num"].named_steps["scale"].scale_)) if numeric else {}
    hr = []
    for j, n in enumerate(names):
        b, s = float(cox.coef_[j]), float(se[j])
        src = n if n in numeric else next((c for c in inputs if c not in numeric and n.startswith(c + "_")), n)
        unit = float(scale.get(n, 1.0)) if n in numeric else 1.0
        hr.append({"term": n, "feature": src, "kind": "number" if n in numeric else "category",
                   "level": None if n in numeric else n[len(src) + 1:], "beta": b, "se": s, "z": b / s if s else None,
                   "p_value": float(_p(b / s)) if s else None, "sd": unit if n in numeric else None,
                   "hr_sd": float(np.exp(b)) if n in numeric else None,
                   "hr": float(np.exp(b / unit)), "hr_ci": [float(np.exp((b - 1.96 * s) / unit)), float(np.exp((b + 1.96 * s) / unit))]})
    hr.sort(key=lambda x: -abs(x["z"] or 0))
    ref_levels = {c: str(cats[0]) for c, cats in zip([c for c in inputs if c not in numeric],
                                                    pre.named_transformers_["cat"].named_steps["onehot"].categories_)} if len(inputs) > len(numeric) else {}

    # Save: the design, both models, and what serving needs.
    root = Path(cfg["predictor_path"])
    root.mkdir(parents=True, exist_ok=True)
    joblib.dump({"pre": pre, "cox": cox, "rsf": rsf}, root / "models.joblib", compress=3)
    zbar = Zt.mean(axis=0)
    meta = {"duration_column": dur, "event_column": evt, "unit": unit_of(dur), "inputs": inputs, "numeric": numeric, "terms": names,
            "served": served, "horizons": horizons, "grid": grid.tolist(), "risk_cuts": cuts, "groups": list(GROUPS),
            "zbar": zbar.tolist(), "ref_levels": ref_levels, "baseline_curve": S_h[served].mean(axis=0).round(5).tolist(),
            "km_all": km_all["s"]}
    (root / "meta.json").write_text(json.dumps(meta, indent=1, default=str))
    pd.DataFrame({"row": holdout.index.to_numpy(), "t": th, "e": eh, "risk": r, "group": [GROUPS[i] for i in g],
                  **{f"S_{h:g}": S_h[served][:, int(np.searchsorted(grid, h))] for h in horizons}}).to_csv(run_dir / "surv_holdout.csv", index=False)

    ex_idx = list(np.argsort(r)[[0, len(r) // 3, len(r) // 2, 2 * len(r) // 3, len(r) - 1]])
    examples = [{k: (None if pd.isna(v) else v.item() if isinstance(v, np.generic) else v) for k, v in holdout.iloc[i][inputs].items()}
                for i in ex_idx][:EXAMPLES]
    hc = per[served]
    out = {"duration_column": dur, "event_column": evt, "unit": unit_of(dur), "served": served, "val_from": val_from,
           "horizons": horizons, "grid": grid.tolist(), "models": per, "fit_seconds": {"cox": cox_s, "rsf": rsf_s},
           "n_trees": n_trees, "ridge": alpha, "groups": groups, "km_all": km_all,
           "calibration": calibration, "chosen_by": "the lower integrated Brier score on the validation slice",
           "hazard_ratios": hr, "ref_levels": ref_levels, "inputs": inputs, "numeric": numeric, "dropped_categories": dropped_cats,
           "train_rows": int(len(fit_rows)), "val_rows": int(len(val)), "holdout_rows": int(len(holdout)),
           "events": {"train": int(etr.sum()), "holdout": int(eh.sum())}, "examples": examples,
           "detected": cfg.get("detected") or {}}
    merge(survival=out, problem_type="survival", best_model=f"{'Cox PH' if served == 'cox' else 'random survival forest'}",
          features=inputs, holdout={"metrics": {"c_index": hc["c_index"], **{f"auc_{a['t']:g}": a["auc"] for a in hc.get("at") or []}}},
          eval_metric={"name": "c_index", "greater_is_better": True})
    return {"served": served, "c_index": hc["c_index"]}


def _p(z: float) -> float:
    from scipy.stats import norm

    return 2 * norm.sf(abs(z))


# --------------------------------------------------------------------------------------------- serving (Registry)

def load(path: str) -> dict[str, Any]:
    import joblib

    p = Path(path)
    m = (p / "meta.json").stat().st_mtime
    hit = _CACHE.get(str(p))
    if hit and hit[0] == m:
        return hit[1]
    out = {"meta": json.loads((p / "meta.json").read_text()), **joblib.load(p / "models.joblib")}
    _CACHE[str(p)] = (m, out)
    return out


def _rows(meta: dict[str, Any], rows: pd.DataFrame | list[dict[str, Any]]) -> pd.DataFrame:
    df = pd.DataFrame(rows) if isinstance(rows, list) else rows
    if df.empty:
        raise ValueError("Give at least one row.")
    if not any(c in df.columns for c in meta["inputs"]):
        raise ValueError(f"None of the model's inputs ({', '.join(meta['inputs'][:5])}, …) are in the rows.")
    return _frame_in(df.reset_index(drop=True), meta["inputs"], meta["numeric"])


def score_rows(path: str, rows: pd.DataFrame | list[dict[str, Any]], horizons: list[float] | None = None,
               curve: bool = False) -> dict[str, Any]:
    """Rows → the served model's risk score, risk group, Ŝ(τ) and P(event by τ) at each horizon, and the median time."""
    t0 = time.perf_counter()
    M = load(path)
    meta = M["meta"]
    X = _rows(meta, rows)
    Z = M["pre"].transform(X)
    m = M[meta["served"]]
    hs = [float(h) for h in (horizons or meta["horizons"])]
    grid = np.array(meta["grid"])
    r = m.predict(Z)
    S = surv_matrix(m, Z, grid)
    Sh = surv_matrix(m, Z, np.array(hs)) if hs else np.empty((len(X), 0))
    g = np.searchsorted(meta["risk_cuts"], r, side="right")
    res = []
    for i in range(len(X)):
        x = {"risk": float(r[i]), "risk_group": meta["groups"][int(g[i])],
             "at": [{"t": h, "survival": float(Sh[i, j]), "event_by": float(1 - Sh[i, j])} for j, h in enumerate(hs)],
             "median_time": median_time(S[i], grid)}
        if curve:
            x["curve"] = S[i].round(5).tolist()
        res.append(x)
    return {"problem_type": "survival", "served": meta["served"], "unit": meta.get("unit"), "horizons": hs,
            "duration_column": meta["duration_column"], "event_column": meta["event_column"], "n_rows": len(res),
            "results": res, "predict_ms": (time.perf_counter() - t0) * 1e3}


def try_row(path: str, row: dict[str, Any]) -> dict[str, Any]:
    """Try It: one subject's survival curve against the average holdout subject's, Ŝ(τ) at the horizons and, when Cox
    serves, what moves its hazard: the terms β_j (z_j − z̄_j), whose sum is ln HR against the average subject."""
    M = load(path)
    meta = M["meta"]
    out = score_rows(path, [row], curve=True)
    r0 = out["results"][0]
    given = {k for k, v in row.items() if v is not None and v != ""}
    res = {**out, **r0, "grid": meta["grid"], "baseline_curve": meta["baseline_curve"], "km_all": meta["km_all"],
           "features": meta["inputs"], "missing_columns": [c for c in meta["inputs"] if c not in given]}
    res.pop("results", None)
    if meta["served"] == "cox":
        z = M["pre"].transform(_rows(meta, [row]))[0]
        b = M["cox"].coef_
        terms = b * (z - np.array(meta["zbar"]))
        by: dict[str, float] = {}
        for n, v in zip(meta["terms"], terms):
            src = n if n in meta["numeric"] else next((c for c in meta["inputs"] if c not in meta["numeric"] and n.startswith(c + "_")), n)
            by[src] = by.get(src, 0.0) + float(v)
        contrib = sorted(({"feature": k, "value": row.get(k), "log_hr": v, "hr": float(np.exp(v))} for k, v in by.items()),
                         key=lambda x: -abs(x["log_hr"]))
        res["contributions"] = contrib
        res["hr_vs_average"] = float(np.exp(terms.sum()))
    return res


def score_batch(path: str, df: pd.DataFrame) -> tuple[pd.DataFrame, dict[str, Any]]:
    """A file → the file with risk_score, risk_group, S_τ (and P(event by τ)) per horizon, and median_time."""
    r = score_rows(path, df)
    out = df.copy()
    res = r["results"]
    out["risk_score"] = [x["risk"] for x in res]
    out["risk_group"] = [x["risk_group"] for x in res]
    for j, h in enumerate(r["horizons"]):
        out[f"S_{h:g}"] = [x["at"][j]["survival"] for x in res]
    out["median_time"] = [x["median_time"] for x in res]
    counts = pd.Series(out["risk_group"]).value_counts()
    return out, {"groups": {g: int(counts.get(g, 0)) for g in GROUPS},
                 "mean_survival": {f"{h:g}": float(out[f"S_{h:g}"].mean()) for h in r["horizons"]}}
