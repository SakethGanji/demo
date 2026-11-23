"""Group-relative features for anomaly detection: an amount is often unusual only for *this* card (or account,
customer, merchant). With an entity column mapped, each relative column x gets, per row i of entity e:

    r_i = x_i / x̄_e,−i                 the ratio to the entity's mean over its other rows (leave-one-out)
    z_i = (x_i − x̄_e,−i) / s̃_e,−i      the z-score within the entity, s̃ = max(s_e,−i, 0.1·|x̄_e,−i|, ε)

and, with a time column, Δt_i = ln(1 + hours since the entity's previous row). Leave-one-out keeps a spike from
inflating its own baseline. Entities with one row get no baseline (NaN: median-imputed with a missing flag, so "no
history" is itself visible to the forest). New rows (batch scoring) are compared with the training rows' entity
statistics; Δt uses the newest earlier row of the same entity in the training data or the batch.
"""

from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd

AMOUNT_HINT = ("amount", "amt", "value", "spend", "balance", "price", "sum", "total")


def suggest(df: pd.DataFrame, types: dict[str, str]) -> dict[str, Any]:
    """Entity, time and relative columns: a repeated ID-like column (2 ≤ mean group size ≤ n/5), a column that parses
    as datetimes, and amount-like numeric columns."""
    n = len(df)
    ent = None
    for c in df.columns:
        if pd.api.types.is_float_dtype(df[c]):
            continue
        k = df[c].nunique(dropna=True)
        if k >= 2 and 2 <= n / max(k, 1) <= n / 5 and any(h in c.lower() for h in ("id", "card", "account", "customer", "client", "merchant", "user")):
            ent = c
            break
    tcol = None
    for c in df.columns:
        if types.get(c) == "number":
            continue
        if any(h in c.lower() for h in ("time", "date", "_ts", "timestamp")):
            parsed = pd.to_datetime(df[c].head(200), errors="coerce")
            if parsed.notna().mean() > 0.9:
                tcol = c
                break
    rel = [c for c in df.columns if types.get(c) == "number" and any(h in c.lower() for h in AMOUNT_HINT)
           and not c.lower().startswith("avg")]
    return {"entity_column": ent, "time_column": tcol, "relative_columns": rel[:3],
            "source": "names and types" if ent else "no repeated entity column found"}


def _num(s: pd.Series) -> np.ndarray:
    return pd.to_numeric(s, errors="coerce").to_numpy(dtype=float)


def fit_stats(df: pd.DataFrame, spec: dict[str, Any]) -> dict[str, Any]:
    """Per entity: n, Σx, Σx² per relative column and the last timestamp (for scoring new rows)."""
    e = df[spec["entity_column"]].astype(str)
    out: dict[str, Any] = {"cols": {}, "last": {}}
    for c in spec["relative_columns"]:
        x = pd.Series(_num(df[c]), index=df.index)
        g = pd.DataFrame({"e": e, "x": x, "x2": x * x}).dropna().groupby("e")
        agg = g.agg(n=("x", "size"), s=("x", "sum"), s2=("x2", "sum"))
        out["cols"][c] = {k: (int(r.n), float(r.s), float(r.s2)) for k, r in agg.iterrows()}
    if spec.get("time_column"):
        t = pd.to_datetime(df[spec["time_column"]], errors="coerce")
        out["last"] = {k: v.isoformat() for k, v in pd.DataFrame({"e": e, "t": t}).dropna().groupby("e")["t"].max().items()}
    return out


def names(spec: dict[str, Any]) -> list[str]:
    out = []
    for c in spec["relative_columns"]:
        out += [f"{c}_over_entity_mean", f"{c}_entity_z"]
    if spec.get("time_column"):
        out.append("log_hours_since_entity_prev")
    return out


def add(df: pd.DataFrame, spec: dict[str, Any], stats: dict[str, Any] | None = None) -> pd.DataFrame:
    """The frame with the group-relative columns added. stats None: leave-one-out within df (training).
    stats given: against the training entities (new rows)."""
    out = df.copy()
    e = df[spec["entity_column"]].astype(str).to_numpy()
    for c in spec["relative_columns"]:
        x = _num(df[c])
        if stats is None:
            fr = pd.DataFrame({"e": e, "x": x})
            fr["x2"] = fr["x"] ** 2
            g = fr.groupby("e")
            n = g["x"].transform("count").to_numpy(dtype=float)
            s = g["x"].transform("sum").to_numpy(dtype=float)
            s2 = g["x2"].transform("sum").to_numpy(dtype=float)
            ok = ~np.isnan(x)
            n = n - ok
            s = s - np.where(ok, x, 0)
            s2 = s2 - np.where(ok, x * x, 0)
        else:
            st = stats["cols"].get(c, {})
            arr = np.array([st.get(k, (0, 0.0, 0.0)) for k in e], dtype=float).reshape(-1, 3)
            n, s, s2 = arr[:, 0], arr[:, 1], arr[:, 2]
        with np.errstate(invalid="ignore", divide="ignore"):
            mean = np.where(n >= 1, s / np.maximum(n, 1), np.nan)
            var = np.where(n >= 2, (s2 - n * mean * mean) / np.maximum(n - 1, 1), np.nan)
            sd = np.sqrt(np.clip(var, 0, None))
            floor = np.maximum(0.1 * np.abs(mean), 1e-6)
            sd = np.where(np.isnan(sd), floor, np.maximum(sd, floor))
            out[f"{c}_over_entity_mean"] = np.where(mean > 0, x / mean, np.nan)
            out[f"{c}_entity_z"] = (x - mean) / sd
    if spec.get("time_column"):
        t = pd.to_datetime(df[spec["time_column"]], errors="coerce")
        fr = pd.DataFrame({"e": e, "t": t, "i": np.arange(len(df))}).sort_values(["e", "t"])
        prev = fr.groupby("e")["t"].shift(1)
        if stats is not None and stats.get("last"):
            last = fr["e"].map(lambda k: pd.Timestamp(stats["last"][k]) if k in stats["last"] else pd.NaT)
            prev = prev.fillna(last.where(last < fr["t"]))
        gap = (fr["t"] - prev).dt.total_seconds() / 3600.0
        val = np.full(len(df), np.nan)
        val[fr["i"].to_numpy()] = np.log1p(np.clip(gap.to_numpy(dtype=float), 0, None))
        out["log_hours_since_entity_prev"] = val
    return out
