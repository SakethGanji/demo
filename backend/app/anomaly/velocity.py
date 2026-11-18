"""Velocity features for anomaly detection (F2): an isolation forest scores each row alone, so a burst of
transactions on one card is invisible unless the row carries its own recent history. With an entity column and a
time column mapped, every row i of entity e at time t_i gets, from the entity's *earlier* rows only (t_j < t_i;
causal, so the same numbers can be computed when the transaction arrives):

    n_W(i)   = #{j : e_j = e_i, t_i − W ≤ t_j < t_i}                    count in the last W (W = 1 h, 24 h)
    Σ_W(i)   = Σ_{j : e_j = e_i, t_i − W ≤ t_j < t_i} x_j                 amount in the last W
    k_24h(i) = #distinct categories among those j in the last 24 h        (with a category column)
    ρ(i)     = x_i / x̄_{e,<i},  x̄_{e,<i} = mean of x_j over all t_j < t_i  amount ÷ the entity's running mean
    Δ(i)     = ln(1 + hours since the entity's previous transaction)

ρ and Δ are NaN for an entity's first row ("no history": median-imputed with a missing flag by the forest's
preprocessing). New rows (batch scoring) are computed against the training rows plus the batch, a row already in the
training history (same entity, time and amount) counted once.
"""

from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd

WINDOWS = (("1h", 1.0), ("24h", 24.0))
CATEGORY_HINT = ("merchant", "mcc", "category", "counterparty", "payee")


def suggest_category(df: pd.DataFrame, types: dict[str, str], exclude: set[str]) -> str | None:
    """A merchant / category column: categorical, named like one, 2 ≤ distinct values ≤ 5000."""
    for c in df.columns:
        if c in exclude or types.get(c) not in ("category", "id", None):
            continue
        if any(h in c.lower() for h in CATEGORY_HINT) and 2 <= df[c].nunique(dropna=True) <= 5000:
            return c
    return None


def names(spec: dict[str, Any]) -> list[str]:
    a = spec["amount_column"]
    out = []
    for w, _ in WINDOWS:
        out += [f"txns_prev_{w}", f"{a}_sum_prev_{w}"]
    if spec.get("category_column"):
        out.append(f"distinct_{spec['category_column']}_prev_24h")
    out += [f"{a}_over_running_mean", "log_hours_since_prev"]
    return out


def default_in_forest(spec: dict[str, Any]) -> list[str]:
    """The velocity columns the isolation forest sees by default: one per signal. Each split of an isolation tree
    picks a feature uniformly at random, so k near-duplicate columns give their signal k times the say; Σ_W ≈ n_W·x̄,
    k_24h ≈ n_24h on most rows, and ρ duplicates the group-relative x ÷ x̄_e. On the card sample (seeds 0–3,
    ψ = 1024) all seven columns gave P@90 0.70–0.71 with 13–14/18 bursts; these three gave 0.80–0.82 with 13–17/18.
    Every column is still computed, shown, and given to the supervised model (a GBM is not hurt by redundancy)."""
    return ["txns_prev_1h", "txns_prev_24h", "log_hours_since_prev"]


def _frame(df: pd.DataFrame, spec: dict[str, Any]) -> pd.DataFrame:
    fr = pd.DataFrame({"e": df[spec["entity_column"]].astype(str).to_numpy(),
                       "t": pd.to_datetime(df[spec["time_column"]], errors="coerce").to_numpy(),
                       "x": pd.to_numeric(df[spec["amount_column"]], errors="coerce").to_numpy(dtype=float)})
    if spec.get("category_column"):
        fr["c"] = df[spec["category_column"]].astype(str).to_numpy()
    return fr


def _compute(fr: pd.DataFrame, spec: dict[str, Any]) -> pd.DataFrame:
    """Features for every row of fr (columns e, t, x, [c]), in fr's order."""
    n = len(fr)
    cols = names(spec)
    out = {c: np.full(n, np.nan) for c in cols}
    a = spec["amount_column"]
    has_c = "c" in fr.columns
    ok_t = ~pd.isna(fr["t"].to_numpy())
    order = np.lexsort((fr["t"].to_numpy(), fr["e"].to_numpy()))
    order = order[ok_t[order]]
    e_sorted = fr["e"].to_numpy()[order]
    bounds = np.flatnonzero(np.r_[True, e_sorted[1:] != e_sorted[:-1], True])
    tns = fr["t"].to_numpy().astype("datetime64[ns]").astype(np.int64)
    xs = fr["x"].to_numpy()
    cs = fr["c"].to_numpy() if has_c else None
    hour = 3600e9
    for g in range(len(bounds) - 1):
        rows = order[bounds[g]:bounds[g + 1]]
        t = tns[rows]
        x = np.nan_to_num(xs[rows], nan=0.0)
        valid = ~np.isnan(xs[rows])
        cx = np.r_[0.0, np.cumsum(x)]
        cn = np.r_[0, np.cumsum(valid)]
        first = np.searchsorted(t, t, side="left")  # rows strictly earlier: [.., first)
        for w, hrs in WINDOWS:
            lo = np.searchsorted(t, t - int(hrs * hour), side="left")
            out[f"txns_prev_{w}"][rows] = first - lo
            out[f"{a}_sum_prev_{w}"][rows] = cx[first] - cx[lo]
        if has_c:
            lo = np.searchsorted(t, t - int(24 * hour), side="left")
            c = cs[rows]
            out[f"distinct_{spec['category_column']}_prev_24h"][rows] = [len(set(c[l:f])) for l, f in zip(lo, first)]
        with np.errstate(invalid="ignore", divide="ignore"):
            mean = np.where(cn[first] > 0, cx[first] / np.maximum(cn[first], 1), np.nan)
            out[f"{a}_over_running_mean"][rows] = np.where(mean > 0, xs[rows] / mean, np.nan)
        prev = np.where(first > 0, t[np.maximum(first - 1, 0)], np.iinfo(np.int64).min)
        gap = np.where(first > 0, (t - prev) / hour, np.nan)
        out["log_hours_since_prev"][rows] = np.log1p(np.clip(gap, 0, None))
    return pd.DataFrame(out, index=fr.index)


def history(df: pd.DataFrame, spec: dict[str, Any]) -> pd.DataFrame:
    """The compact history saved with the model (entity, time, amount, category) for scoring new rows."""
    return _frame(df, spec).reset_index(drop=True)


def add(df: pd.DataFrame, spec: dict[str, Any], hist: pd.DataFrame | None = None) -> pd.DataFrame:
    """df with the velocity columns added. hist None: within df (training). hist given: against the saved training
    rows plus df, rows of df already in hist (same entity, time, amount) counted once."""
    fr = _frame(df, spec)
    if hist is None:
        feats = _compute(fr.reset_index(drop=True), spec)
    else:
        key = lambda f: f["e"].astype(str) + "|" + pd.Series(f["t"]).astype(str).to_numpy() + "|" + f["x"].round(4).astype(str)
        known = set(key(hist))
        new = fr.reset_index(drop=True)
        dup = key(new).isin(known).to_numpy()
        both = pd.concat([hist, new.loc[~dup]], ignore_index=True)
        f_all = _compute(both, spec)
        # map each row of df to its row in `both`: new rows are appended; duplicates are found in hist
        pos = np.empty(len(new), dtype=int)
        pos[~dup] = len(hist) + np.arange(int((~dup).sum()))
        hk = pd.Series(np.arange(len(hist)), index=key(hist).to_numpy())
        hk = hk[~hk.index.duplicated()]
        pos[dup] = hk.loc[key(new)[dup].to_numpy()].to_numpy()
        feats = f_all.iloc[pos].reset_index(drop=True)
    out = df.copy()
    for c in feats.columns:
        out[c] = feats[c].to_numpy()
    return out
