"""Weak-slice discovery (F16): where on the holdout is the model significantly worse than everywhere else?

Everything is computed from the run's saved holdout predictions (`holdout_predictions.csv`, the served model's
predictions on the fixed holdout) and the raw feature values of those rows (data.csv), so every number can be
recomputed from those two files.

1. Per-row loss ℓᵢ, the run's own metric made per-row:
   - F7 cost runs: the cost of the row's decision (c_FP, c_FN, −g_TP or C[y][ŷ]), at the run's threshold;
   - F9 quantile runs: the pinball loss averaged over the levels, (1/|T|) Σ_τ ρ_τ(y − q̂_τ);
   - regression: |y − ŷ|;
   - classification with a probability metric (roc_auc, log_loss, average_precision, …): −log p̂(yᵢ | xᵢ);
   - other classification metrics (accuracy, f1, …): the 0/1 error 1[ŷᵢ ≠ yᵢ] at the run's threshold.
   `loss` in the request overrides the choice.
2. Slices: every value of a feature (numbers with more than `bins` distinct values cut at holdout quantiles into
   `bins` bins; categories with more than `max_levels` values keep the most frequent ones and an "(other)" level;
   missing is a value) and every pair of values of two features. Text and ID columns are left out (with a reason).
3. A slice S with n_S ≥ `min_support` (and its complement too) is tested against its complement C with Welch's t:
   Δ = ℓ̄_S − ℓ̄_C,  se = √(s²_S/n_S + s²_C/n_C),  t = Δ/se,  ν = Welch–Satterthwaite,  p = P(T_ν ≥ t) (one-sided:
   "worse"); the 95% interval of Δ is Δ ± t_{ν,0.975}·se.
4. Benjamini–Hochberg over every slice tested (singles and pairs together): q₍ₖ₎ = min_{j≥k} p₍ⱼ₎·m/j. A slice is
   weak when q ≤ α.
5. A pair is reported only if it is worse than both of its one-feature parents (the lower 95% bound of its mean loss is
   above each parent's mean); otherwise it is "explained by a parent" and only counted. Weak slices are ranked by the
   lower bound of Δ: "at least this much worse than the rest", which weighs effect against its uncertainty.
6. A permutation null: the losses are shuffled across rows R times and the same search is run; the number of weak
   slices it finds shows what the correction lets through when there is nothing to find.
"""

from __future__ import annotations

import hashlib
import itertools
import json
import math
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from .explain import _as_str

SCHEMA = "slices/1"
DEFAULTS = {"min_support": None, "min_support_share": 0.02, "min_support_floor": 30, "bins": 5, "max_levels": 30,
            "alpha": 0.05, "pairs": True, "null_permutations": 5, "loss": "auto"}
PROBA_METRICS = {"roc_auc", "log_loss", "average_precision", "roc_auc_ovo", "roc_auc_ovr", "roc_auc_ovo_macro",
                 "roc_auc_ovr_macro", "roc_auc_ovr_micro", "roc_auc_ovr_weighted", "pac_score", "pac", "brier_score"}
LOSSES = ("auto", "error", "log_loss", "absolute_error", "squared_error", "cost", "pinball")


class SliceError(ValueError):
    """Shown to the user as a 400."""


# --------------------------------------------------------------------------- per-row loss


def row_loss(hp: pd.DataFrame, *, problem_type: str, metric: str | None, cost: dict[str, Any] | None,
             positive: Any, threshold: float | None, loss: str = "auto") -> tuple[np.ndarray, dict[str, Any]]:
    """(ℓ per holdout row, a description of ℓ). Greater is always worse."""
    if loss not in LOSSES:
        raise SliceError(f"loss must be one of {', '.join(LOSSES)}.")
    cls = problem_type in ("binary", "multiclass")
    if loss == "auto":
        if cost:
            loss = "cost"
        elif problem_type == "quantile":
            loss = "pinball"
        elif not cls:
            loss = "absolute_error"
        else:
            loss = "log_loss" if (metric or "") in PROBA_METRICS else "error"
    if loss == "cost":
        if not cost:
            raise SliceError("loss = cost: this run was not trained on a business cost (F7).")
        from . import costs

        l = costs.row_costs(hp, cost, positive=positive, t=threshold)
        unit = "cost per decision"
        if cost["kind"] == "binary":
            formula = f"ℓᵢ = {cost['c_fp']:g}·FPᵢ + {cost['c_fn']:g}·FNᵢ − {cost.get('g_tp', 0):g}·TPᵢ at t = {threshold if threshold is not None else 0.5:.3f}"
        else:
            formula = "ℓᵢ = C[yᵢ][ŷᵢ] (the cost matrix, served decision)"
        return np.asarray(l, dtype=float), {"name": "cost", "formula": formula, "unit": unit, "symbol": "cᵢ"}
    if loss == "pinball":
        from . import quantile as qt

        got = qt.from_saved(hp)
        if got is None:
            raise SliceError("loss = pinball needs a quantile run (F9).")
        levels, Q = got
        y = hp["y_true"].to_numpy(dtype=float)
        L = np.column_stack([qt.pinball(y, Q[:, j], t) for j, t in enumerate(levels)])
        return L.mean(axis=1), {"name": "pinball", "unit": "pinball loss, mean over levels", "symbol": "ρᵢ",
                                "formula": f"ℓᵢ = (1/{len(levels)}) Σ_τ ρ_τ(yᵢ − q̂_τ(xᵢ)), τ ∈ {{{', '.join(f'{t:g}' for t in levels)}}}, ρ_τ(u) = max(τu, (τ−1)u)"}
    if loss in ("absolute_error", "squared_error"):
        if cls:
            raise SliceError(f"loss = {loss} needs a regression or quantile run.")
        y = hp["y_true"].to_numpy(dtype=float)
        yhat = hp["y_pred"].to_numpy(dtype=float)
        if loss == "absolute_error":
            return np.abs(y - yhat), {"name": "absolute_error", "formula": "ℓᵢ = |yᵢ − ŷᵢ|", "unit": "absolute error", "symbol": "|rᵢ|"}
        return (y - yhat) ** 2, {"name": "squared_error", "formula": "ℓᵢ = (yᵢ − ŷᵢ)²", "unit": "squared error", "symbol": "rᵢ²"}
    if not cls:
        raise SliceError(f"loss = {loss} needs a classification run.")
    yt = hp["y_true"].map(_as_str).to_numpy()
    if loss == "error":
        yp = hp["y_pred"].map(_as_str).to_numpy()
        t = f" at t = {threshold:.3f}" if problem_type == "binary" and threshold is not None else ""
        return (yt != yp).astype(float), {"name": "error", "formula": f"ℓᵢ = 1[ŷᵢ ≠ yᵢ]{t}", "unit": "error rate", "symbol": "eᵢ"}
    classes = [c[2:] for c in hp.columns if c.startswith("p_")]
    P = hp[[f"p_{c}" for c in classes]].to_numpy(dtype=float)
    pos = {c: i for i, c in enumerate(classes)}
    idx = np.array([pos.get(v, -1) for v in yt])
    if (idx < 0).any():
        raise SliceError("Some holdout labels have no probability column.")
    p = np.clip(P[np.arange(len(P)), idx], 1e-15, 1.0)
    return -np.log(p), {"name": "log_loss", "formula": "ℓᵢ = −log p̂(yᵢ | xᵢ)", "unit": "log loss", "symbol": "ℓᵢ"}


# --------------------------------------------------------------------------- slices: conditions and binning


def _fmt(v: float) -> str:
    if v is None or not math.isfinite(v):
        return str(v)
    a = abs(v)
    if float(v).is_integer() and 1800 <= v <= 2100:  # A2 m7: a year, not "1,900"
        return f"{v:.0f}"
    if a >= 1000:
        return f"{v:,.0f}"
    if a >= 100 or float(v).is_integer():
        return f"{v:.0f}" if float(v).is_integer() else f"{v:.1f}"
    return f"{v:.3g}"


def describe(cond: dict[str, Any]) -> str:
    c = cond["column"]
    if cond.get("missing"):
        return f"{c} is missing"
    if cond["op"] == "range":
        lo, hi = cond.get("lo"), cond.get("hi")
        if lo is None:
            return f"{c} < {_fmt(hi)}"
        if hi is None:
            return f"{c} ≥ {_fmt(lo)}"
        return f"{_fmt(lo)} ≤ {c} < {_fmt(hi)}"
    if cond["op"] == "not_in":
        return f"{c} ∉ {{{', '.join(map(str, cond['values'][:4]))}{', …' if len(cond['values']) > 4 else ''}}} (other)"
    vals = cond["values"]
    return f"{c} = {vals[0]}" if len(vals) == 1 else f"{c} ∈ {{{', '.join(map(str, vals))}}}"


def condition_mask(frame: pd.DataFrame, cond: dict[str, Any]) -> np.ndarray:
    """Rows of `frame` (raw data.csv values) inside one condition. Applies to any rows: the holdout, or a labelling
    pool (F13)."""
    c = cond["column"]
    if c not in frame.columns:
        raise SliceError(f"No column {c!r}.")
    s = frame[c]
    if cond.get("missing"):
        return s.isna().to_numpy()
    if cond["op"] == "range":
        x = pd.to_numeric(s, errors="coerce") if not cond.get("date") else pd.to_datetime(s, errors="coerce").map(lambda d: d.timestamp() if pd.notna(d) else np.nan)
        x = x.to_numpy(dtype=float)
        m = ~np.isnan(x)
        if cond.get("lo") is not None:
            m &= x >= float(cond["lo"])
        if cond.get("hi") is not None:
            m &= x < float(cond["hi"])
        return m
    vals = set(map(str, cond["values"]))
    sv = s.map(lambda v: None if pd.isna(v) else _as_str(v))
    inside = sv.isin(vals).to_numpy()
    if cond["op"] == "not_in":
        return s.notna().to_numpy() & ~inside
    return inside


def slice_mask(frame: pd.DataFrame, conditions: list[dict[str, Any]]) -> np.ndarray:
    m = np.ones(len(frame), dtype=bool)
    for cond in conditions:
        m &= condition_mask(frame, cond)
    return m


def _bin_column(s: pd.Series, ctype: str, bins: int, max_levels: int) -> tuple[np.ndarray, list[dict[str, Any]], str]:
    """(code per row, condition per code, how). Codes index the conditions."""
    name = str(s.name)
    is_date = ctype == "date"
    if is_date:
        x = pd.to_datetime(s, errors="coerce").map(lambda d: d.timestamp() if pd.notna(d) else np.nan).astype(float)
    elif ctype == "number" or (ctype is None and pd.api.types.is_numeric_dtype(s)):
        x = pd.to_numeric(s, errors="coerce").astype(float)
    else:
        x = None
    if x is not None and x.nunique(dropna=True) > bins:
        q = np.unique(np.nanquantile(x.to_numpy(), np.linspace(0, 1, bins + 1)[1:-1]))
        edges = [None, *[float(e) for e in q], None]
        conds = []
        for lo, hi in zip(edges[:-1], edges[1:]):
            conds.append({"column": name, "op": "range", "lo": lo, "hi": hi, **({"date": True} if is_date else {})})
        codes = np.searchsorted(q, x.to_numpy(), side="right").astype(int)
        miss = x.isna().to_numpy()
        how = f"{len(conds)} quantile bins"
    else:
        sv = s.map(lambda v: None if pd.isna(v) else _as_str(v))
        counts = sv.value_counts()
        keep = list(counts.index[: max_levels - 1]) if len(counts) > max_levels else list(counts.index)
        conds = [{"column": name, "op": "in", "values": [v]} for v in keep]
        pos = {v: i for i, v in enumerate(keep)}
        codes = sv.map(lambda v: pos.get(v, len(keep)) if v is not None else -1).to_numpy().astype(int)
        if len(counts) > len(keep):
            conds.append({"column": name, "op": "not_in", "values": keep})
            how = f"{len(keep)} most frequent values + (other)"
        else:
            how = f"{len(keep)} values"
        miss = sv.isna().to_numpy()
    if miss.any():
        codes = np.where(miss, len(conds), codes)
        conds.append({"column": name, "missing": True, "op": "missing"})
    return codes, conds, how


# --------------------------------------------------------------------------- statistics


def _t_sf(t: np.ndarray, df: np.ndarray) -> np.ndarray:
    from scipy import stats

    return stats.t.sf(t, df)


def _t_q(df: np.ndarray, level: float = 0.975) -> np.ndarray:
    from scipy import stats

    return stats.t.ppf(level, df)


def bh(p: np.ndarray) -> np.ndarray:
    """Benjamini–Hochberg q-values: q₍ₖ₎ = min_{j≥k} p₍ⱼ₎·m/j, capped at 1."""
    m = len(p)
    if not m:
        return p
    order = np.argsort(p, kind="stable")
    ranked = p[order] * m / np.arange(1, m + 1)
    q = np.minimum.accumulate(ranked[::-1])[::-1]
    out = np.empty(m)
    out[order] = np.minimum(q, 1.0)
    return out


def welch(n_s, s1_s, s2_s, n_t, s1_t, s2_t):
    """Vectorised Welch test of slice vs complement from counts and sums (Σℓ, Σℓ²) of the slice and of all rows."""
    n_s = n_s.astype(float)
    n_c = n_t - n_s
    m_s = s1_s / n_s
    m_c = (s1_t - s1_s) / n_c
    v_s = np.maximum((s2_s - n_s * m_s ** 2) / np.maximum(n_s - 1, 1), 0)
    v_c = np.maximum(((s2_t - s2_s) - n_c * m_c ** 2) / np.maximum(n_c - 1, 1), 0)
    a, b = v_s / n_s, v_c / n_c
    se = np.sqrt(a + b)
    d = m_s - m_c
    with np.errstate(divide="ignore", invalid="ignore"):
        df = (a + b) ** 2 / (a ** 2 / np.maximum(n_s - 1, 1) + b ** 2 / np.maximum(n_c - 1, 1))
        df = np.where(np.isfinite(df) & (df > 0), df, np.maximum(n_s + n_c - 2, 1))
        t = np.where(se > 0, d / np.where(se > 0, se, 1), np.where(d > 0, np.inf, np.where(d < 0, -np.inf, 0.0)))
    p = _t_sf(t, df)
    half = _t_q(df) * se
    return {"mean": m_s, "mean_rest": m_c, "sd": np.sqrt(v_s), "delta": d, "se": se, "t": t, "df": df, "p": p,
            "lo": d - half, "hi": d + half, "mean_lo": m_s - _t_q(np.maximum(n_s - 1, 1)) * np.sqrt(v_s / n_s)}


# --------------------------------------------------------------------------- the search


def _search(loss: np.ndarray, cols: list[tuple[str, np.ndarray, int]], min_support: int, pairs: bool):
    """Every slice tested: (keys, stats). A key is ((col_i, code_i),) or ((col_i, code_i), (col_j, code_j))."""
    n = len(loss)
    s1_t, s2_t = float(loss.sum()), float((loss ** 2).sum())
    keys, N, S1, S2 = [], [], [], []
    l2 = loss ** 2
    for ci, (_, codes, k) in enumerate(cols):
        cnt = np.bincount(codes, minlength=k)
        a = np.bincount(codes, weights=loss, minlength=k)
        b = np.bincount(codes, weights=l2, minlength=k)
        for v in range(k):
            if min_support <= cnt[v] <= n - min_support:
                keys.append(((ci, v),)); N.append(cnt[v]); S1.append(a[v]); S2.append(b[v])
    if pairs:
        for (ci, (_, ca, ka)), (cj, (_, cb, kb)) in itertools.combinations(enumerate(cols), 2):
            code = ca * kb + cb
            cnt = np.bincount(code, minlength=ka * kb)
            ok = np.nonzero((cnt >= min_support) & (cnt <= n - min_support))[0]
            if not len(ok):
                continue
            a = np.bincount(code, weights=loss, minlength=ka * kb)
            b = np.bincount(code, weights=l2, minlength=ka * kb)
            for v in ok:
                keys.append(((ci, int(v // kb)), (cj, int(v % kb)))); N.append(cnt[v]); S1.append(a[v]); S2.append(b[v])
    if not keys:
        return keys, None
    st = welch(np.array(N), np.array(S1), np.array(S2), float(n), s1_t, s2_t)
    st["n"] = np.array(N, dtype=int)
    st["q"] = bh(st["p"])
    return keys, st


def _rank(keys, st, alpha: float) -> tuple[list[int], int]:
    """Indices of the weak slices (q ≤ α, pairs worse than both parents), ranked by the lower bound of Δ; and the
    number of weak pairs explained by a parent."""
    single = {k[0]: i for i, k in enumerate(keys) if len(k) == 1}
    out, explained = [], 0
    for i, k in enumerate(keys):
        if not (st["q"][i] <= alpha and st["delta"][i] > 0):
            continue
        if len(k) == 2:
            parents = [single.get(k[0]), single.get(k[1])]
            if any(pi is not None and st["mean_lo"][i] <= st["mean"][pi] for pi in parents):
                explained += 1
                continue
        out.append(i)
    out.sort(key=lambda i: (-st["lo"][i], -st["delta"][i]))
    return out, explained


def search(frame: pd.DataFrame, hp: pd.DataFrame, loss: np.ndarray, loss_info: dict[str, Any], features: list[str],
           types: dict[str, str], *, min_support: int | None = None, min_support_share: float = 0.02,
           min_support_floor: int = 30, bins: int = 5, max_levels: int = 30, alpha: float = 0.05, pairs: bool = True,
           null_permutations: int = 5, top: int = 50) -> dict[str, Any]:
    """`frame`: the raw feature values of the holdout rows, in the order of `hp` (and of `loss`)."""
    n = len(loss)
    if min_support is None:
        min_support = max(int(min_support_floor), int(math.ceil(min_support_share * n)))
    if n < 2 * min_support:
        raise SliceError(f"The holdout has {n} rows; slices of at least {min_support} rows and their complements need {2 * min_support}.")
    cols, skipped, binning = [], [], []
    for f in features:
        t = types.get(f)
        if f not in frame.columns:
            skipped.append({"column": f, "reason": "not in the dataset"})
            continue
        if t in ("text", "id"):
            skipped.append({"column": f, "reason": f"{t} column: free text / identifiers do not form value slices"})
            continue
        codes, conds, how = _bin_column(frame[f], t, bins, max_levels)
        if len(conds) < 2:
            skipped.append({"column": f, "reason": "a single value on the holdout"})
            continue
        cols.append((f, codes, len(conds)))
        binning.append({"column": f, "how": how, "type": t or ("number" if pd.api.types.is_numeric_dtype(frame[f]) else "category"),
                        "conditions": conds, "counts": np.bincount(codes, minlength=len(conds)).tolist()})
    conds_of = {b["column"]: b["conditions"] for b in binning}
    keys, st = _search(loss, cols, min_support, pairs)
    n_single = sum(1 for k in keys if len(k) == 1)
    overall = float(loss.mean())
    result: dict[str, Any] = {
        "schema": SCHEMA, "n": n, "loss": loss_info, "overall": overall, "overall_sd": float(loss.std(ddof=1)) if n > 1 else 0.0,
        "params": {"min_support": int(min_support), "min_support_share": min_support_share, "min_support_floor": min_support_floor,
                   "bins": bins, "max_levels": max_levels, "alpha": alpha, "pairs": pairs, "null_permutations": null_permutations},
        "columns": [{**b, "levels": [describe(c) for c in b["conditions"]]} for b in binning],
        "skipped": skipped,
        "tested": {"total": len(keys), "single": n_single, "pairs": len(keys) - n_single,
                   "features": len(cols), "feature_pairs": len(cols) * (len(cols) - 1) // 2 if pairs else 0},
        "slices": [], "n_significant": 0, "n_explained_by_parent": 0,
    }
    if st is None:
        result["null"] = None
        return result
    weak, explained = _rank(keys, st, alpha)
    result["n_significant"] = int(sum(1 for i in range(len(keys)) if st["q"][i] <= alpha and st["delta"][i] > 0))
    result["n_explained_by_parent"] = explained
    result["n_weak"] = len(weak)
    hp_rows = hp["row"].to_numpy(dtype=int)
    for rank, i in enumerate(weak[:top] if top else weak, start=1):
        k = keys[i]
        conds = [conds_of[cols[ci][0]][v] for ci, v in k]
        m = np.ones(n, dtype=bool)
        for ci, v in k:
            m &= cols[ci][1] == v
        parents = []
        if len(k) == 2:
            single = {kk[0]: j for j, kk in enumerate(keys) if len(kk) == 1}
            for part in k:
                j = single.get(part)
                parents.append({"label": describe(conds_of[cols[part[0]][0]][part[1]]),
                                "mean": None if j is None else float(st["mean"][j]),
                                "n": None if j is None else int(st["n"][j]),
                                "q": None if j is None else float(st["q"][j])})
        result["slices"].append({
            "id": slice_id(conds), "rank": rank, "label": " ∧ ".join(describe(c) for c in conds), "conditions": conds,
            "columns": [c["column"] for c in conds], "order": len(k), "n": int(st["n"][i]), "share": float(st["n"][i] / n),
            "mean": float(st["mean"][i]), "mean_lo": float(st["mean_lo"][i]), "mean_rest": float(st["mean_rest"][i]), "sd": float(st["sd"][i]),
            "delta": float(st["delta"][i]), "ci": [float(st["lo"][i]), float(st["hi"][i])], "se": float(st["se"][i]),
            "t": float(st["t"][i]) if np.isfinite(st["t"][i]) else None, "df": float(st["df"][i]),
            "p": float(st["p"][i]), "q": float(st["q"][i]), "ratio": float(st["mean"][i] / overall) if overall else None,
            "parents": parents, "rows": hp_rows[m].tolist(),
        })
    # The permutation null: the same search with the losses shuffled across rows.
    rng = np.random.default_rng(16)
    counts = []
    for _ in range(int(null_permutations)):
        kk, s0 = _search(rng.permutation(loss), cols, min_support, pairs)
        counts.append(0 if s0 is None else len(_rank(kk, s0, alpha)[0]))
    result["null"] = {"permutations": int(null_permutations), "weak_found": counts,
                      "mean": float(np.mean(counts)) if counts else None, "max": int(max(counts)) if counts else None}
    return result


def slice_id(conds: list[dict[str, Any]]) -> str:
    return "sl_" + hashlib.sha1(json.dumps(conds, sort_keys=True, default=str).encode()).hexdigest()[:10]


def summary(result: dict[str, Any], k: int = 5) -> dict[str, Any]:
    """The small summary F17 (Model Doctor) reads, on the run payload as `slices`."""
    return {"schema": SCHEMA, "computed_at": result.get("computed_at"), "loss": (result.get("loss") or {}).get("name"),
            "overall": result.get("overall"), "n": result.get("n"), "n_tested": (result.get("tested") or {}).get("total"),
            "n_significant": result.get("n_significant"), "n_weak": result.get("n_weak", 0), "alpha": (result.get("params") or {}).get("alpha"),
            "min_support": (result.get("params") or {}).get("min_support"),
            "null_mean": (result.get("null") or {}).get("mean"),
            "top": [{"id": s["id"], "label": s["label"], "columns": s["columns"], "n": s["n"], "mean": s["mean"],
                     "delta": s["delta"], "ci": s["ci"], "ratio": s["ratio"], "q": s["q"]} for s in result.get("slices", [])[:k]]}


def file_sha(path: Path) -> str:
    return hashlib.sha1(Path(path).read_bytes()).hexdigest()[:16]
