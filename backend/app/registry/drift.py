"""Population stability: how far a scored batch has moved from the data the model was trained on.

PSI = Σᵢ (aᵢ − eᵢ) · ln(aᵢ / eᵢ), over bins i, where eᵢ is the share of the **training** rows (expected) in bin i
and aᵢ the share of the **batch** rows (actual). Shares are floored at ε = 10⁻⁴ before the sum, so an empty bin
counts instead of dividing by zero. Bands (the usual credit-risk convention): PSI < 0.10 stable,
0.10 ≤ PSI ≤ 0.25 watch, PSI > 0.25 shift.

Bins
- Numbers: the training deciles. Edges e₁ < … < e₉ are np.quantile(train, [0.1, …, 0.9]) with duplicates removed;
  bins are (−∞, e₁], (e₁, e₂], …, (e₉, ∞). Missing values get a bin of their own.
- Categories: each training category among the 19 most frequent, then "(other)" for the rest of training and any
  value the training data never had; missing values get their own bin.
- Text: the length in characters, binned like a number.
- Predictions: binary, p̂ of the positive class on the deciles of the reference p̂; multiclass, the predicted class;
  regression, ŷ on the deciles of the reference ŷ. The reference is the version's own predictions on the holdout.
"""

from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd

EPS = 1e-4
MISSING = "(missing)"
OTHER = "(other)"
MAX_CATS = 19


def band(psi: float) -> str:
    if psi < 0.10:
        return "stable"
    if psi <= 0.25:
        return "watch"
    return "shift"


def psi_from_shares(e: np.ndarray, a: np.ndarray, eps: float = EPS) -> tuple[float, np.ndarray]:
    e = np.maximum(np.asarray(e, dtype=float), eps)
    a = np.maximum(np.asarray(a, dtype=float), eps)
    terms = (a - e) * np.log(a / e)
    return float(terms.sum()), terms


def numeric_edges(ref: pd.Series) -> list[float]:
    v = pd.to_numeric(ref, errors="coerce").dropna().to_numpy(dtype=float)
    if len(v) == 0:
        return []
    return [float(x) for x in np.unique(np.quantile(v, np.linspace(0.1, 0.9, 9)))]


def _numeric_counts(s: pd.Series, edges: list[float]) -> tuple[np.ndarray, int]:
    v = pd.to_numeric(s, errors="coerce")
    miss = int(v.isna().sum())
    x = v.dropna().to_numpy(dtype=float)
    # bin k holds (e_k, e_{k+1}]: searchsorted(side='left') puts a value equal to an edge in the bin that edge closes
    idx = np.searchsorted(np.asarray(edges, dtype=float), x, side="left")
    return np.bincount(idx, minlength=len(edges) + 1), miss


def _numeric_labels(edges: list[float]) -> list[str]:
    def f(x: float) -> str:
        return f"{x:.4g}"

    if not edges:
        return ["all"]
    return [f"≤ {f(edges[0])}"] + [f"({f(a)}, {f(b)}]" for a, b in zip(edges[:-1], edges[1:])] + [f"> {f(edges[-1])}"]


def feature_psi(name: str, kind: str, ref: pd.Series, cur: pd.Series) -> dict[str, Any]:
    """PSI of one column. `kind` is number, category or text."""
    if kind == "text":
        ref = ref.astype("string").str.len()
        cur = cur.astype("string").str.len()
        kind_used = "text length"
    else:
        kind_used = kind
    if kind in ("number", "text"):
        edges = numeric_edges(ref)
        ce, me = _numeric_counts(ref, edges)
        ca, ma = _numeric_counts(cur, edges)
        labels = _numeric_labels(edges)
        ref_num = pd.to_numeric(ref, errors="coerce")
        cur_num = pd.to_numeric(cur, errors="coerce")
        stats = {"mean_expected": _f(ref_num.mean()), "mean_actual": _f(cur_num.mean()),
                 "std_expected": _f(ref_num.std()), "std_actual": _f(cur_num.std())}
    else:
        r = ref.astype("object").where(ref.notna(), None)
        top = [k for k in r.dropna().astype(str).value_counts().index[:MAX_CATS]]
        def cats(s: pd.Series) -> pd.Series:
            s2 = s.astype("object")
            out = s2.where(s2.isna(), s2.astype(str))
            return out.map(lambda v: MISSING if v is None or (isinstance(v, float) and np.isnan(v)) else (v if v in top else OTHER))
        cr, cc = cats(ref), cats(cur)
        labels = top + [OTHER]
        ce = np.array([(cr == k).sum() for k in labels])
        ca = np.array([(cc == k).sum() for k in labels])
        me, ma = int((cr == MISSING).sum()), int((cc == MISSING).sum())
        edges = None
        stats = {"categories_unseen": sorted(set(cur.dropna().astype(str)) - set(ref.dropna().astype(str)))[:20]}
    ce, ca = list(map(int, ce)), list(map(int, ca))
    if me or ma:
        labels = list(labels) + [MISSING]
        ce.append(int(me))
        ca.append(int(ma))
    if kind != "number" and kind != "text" and ce[labels.index(OTHER)] == 0 and ca[labels.index(OTHER)] == 0:
        i = labels.index(OTHER)
        labels.pop(i), ce.pop(i), ca.pop(i)
    n_e, n_a = sum(ce), sum(ca)
    e = np.asarray(ce, dtype=float) / max(n_e, 1)
    a = np.asarray(ca, dtype=float) / max(n_a, 1)
    psi, terms = psi_from_shares(e, a)
    return {"feature": name, "kind": kind_used, "psi": psi, "band": band(psi), "n_expected": n_e, "n_actual": n_a,
            "bins": [{"label": lab, "expected_count": c1, "actual_count": c2, "expected": float(x), "actual": float(y),
                      "term": float(t)} for lab, c1, c2, x, y, t in zip(labels, ce, ca, e, a, terms)],
            "edges": edges, **stats}


def _f(v: Any) -> float | None:
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return f if np.isfinite(f) else None


def prediction_psi(problem_type: str, ref: pd.DataFrame, cur: pd.DataFrame, positive: str | None) -> dict[str, Any]:
    """PSI of the model's output: `ref` and `cur` have y_pred and (classification) p_<class> columns."""
    if problem_type == "binary":
        col = f"p_{positive}"
        out = feature_psi("p̂", "number", ref[col], cur[col])
        out.update(output=f"p̂ = P(y = {positive} | x)", mean_expected=_f(ref[col].mean()), mean_actual=_f(cur[col].mean()))
    elif problem_type == "multiclass":
        out = feature_psi("ŷ", "category", ref["y_pred"].astype(str), cur["y_pred"].astype(str))
        out.update(output="predicted class ŷ")
    else:
        out = feature_psi("ŷ", "number", ref["y_pred"], cur["y_pred"])
        out.update(output="predicted value ŷ")
    from scipy.stats import ks_2samp

    if problem_type != "multiclass":
        col = f"p_{positive}" if problem_type == "binary" else "y_pred"
        ks = ks_2samp(pd.to_numeric(ref[col], errors="coerce").dropna(), pd.to_numeric(cur[col], errors="coerce").dropna())
        out["ks"] = {"statistic": float(ks.statistic), "p_value": float(ks.pvalue)}
    return out


def drift_report(train: pd.DataFrame, batch: pd.DataFrame, kinds: dict[str, str], *, problem_type: str,
                 ref_pred: pd.DataFrame, cur_pred: pd.DataFrame, positive: str | None) -> dict[str, Any]:
    feats = []
    for c, k in kinds.items():
        if c not in batch.columns:
            feats.append({"feature": c, "kind": k, "psi": None, "band": "missing", "note": "column not in the batch"})
            continue
        feats.append(feature_psi(c, k, train[c], batch[c]))
    feats.sort(key=lambda d: -(d["psi"] if d["psi"] is not None else -1))
    scored = [f for f in feats if f["psi"] is not None]
    counts = {b: sum(1 for f in scored if f["band"] == b) for b in ("stable", "watch", "shift")}
    pred = prediction_psi(problem_type, ref_pred, cur_pred, positive)
    return {"features": feats, "prediction": pred, "counts": counts, "eps": EPS,
            "max_psi": max((f["psi"] for f in scored), default=None),
            "bands": [{"band": "stable", "rule": "PSI < 0.10"}, {"band": "watch", "rule": "0.10 ≤ PSI ≤ 0.25"},
                      {"band": "shift", "rule": "PSI > 0.25"}],
            "formula": "PSI = Σᵢ (aᵢ − eᵢ) · ln(aᵢ / eᵢ)", "n_expected": len(train), "n_actual": len(batch),
            "reference": "training rows of the source run (data.csv minus the holdout, labelled)",
            "prediction_reference": "the version's predictions on the holdout"}
