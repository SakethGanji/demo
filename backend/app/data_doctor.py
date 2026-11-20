"""D1 Data Doctor: what is wrong with a table before any training, and how to fix it.

Every check returns a finding

    {id, severity, title, numbers, rule, evidence: {columns, rows}, fix: {kind, text, transforms, selected}}

with the rule written out (it is shown on screen), the numbers it measured, a few example rows, and a fix. A fix is
either typed transforms (`TRANSFORMS`, applied with one click to make a new dataset version; the original is never
changed), a question (the decision-time question, fairness-only columns) or advice (out-of-time split, collect more).

Rules, not a model, except the label check: it fits a quick out-of-fold model (≤ 60 s, in a background thread) and
asks cleanlab which labels disagree with it. Everything else is synchronous and takes seconds on 40k rows.

    report(df, target, roles)            -> {"findings": [...], "decision_time": {...}, ...}
    apply(df, transforms)                -> (new frame, kept-row mask, effects)
"""

from __future__ import annotations

import json
import logging
import math
import re
import threading
import time
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from .profiling import MAX_CLASSES, REF_NAME, detect_type

log = logging.getLogger("ensemble.data_doctor")

SEVERITIES = ("high", "medium", "low", "info")
# The order findings are listed in within a severity: the most actionable first.
ORDER = ("leakage", "coerced_numbers", "coded_values", "disguised_missing", "rare_codes", "missing_words", "label_quality", "duplicates", "repeated_entities", "conflicts",
         "class_balance", "sample_size", "time", "unit_change", "informative_missing", "ids_constants", "protected")

NUM_SENTINELS = (-1, -9, -99, -999, -9999, 99, 999, 9999, 99999, 999999)
STR_SENTINELS = ("unknown", "?", "n/a", "na", "null", "-", "--", "missing", "not available", "undefined", "")  # not 'none': a real category
SIGNED_NAME = re.compile(r"(bal|amt|amount|change|diff|delta|pnl|profit|return|rate|idx|index|growth|net|score|temp)", re.I)
AGE_NAME = re.compile(r"(^|_|\.)age($|_|\.)|^age\w*$", re.I)
PROTECTED_EXTRA = re.compile(r"(^|_|\.)(sex|gender|race|ethnic\w*|religion|marital\w*|marriage|national\w*|disab\w*|"
                             r"pregnan\w*|citizenship)($|_|\.)", re.I)
WEAK_SUSPECT_AUC = 0.75
SEX_WORDS = re.compile(r"(?i)\b(?:male|female|man|woman|men|women)\b")
VALUE_AFTER = re.compile(r"(assess|apprais|valuation|settle|final|closing|sold|sale_?price|paid|payout|recover)", re.I)
CALL_AFTER = re.compile(r"(duration|call_?len|talk_?time|handle_?time|outcome|result|response|status_after)", re.I)


# --------------------------------------------------------------------------------------------------- small helpers

def _finding(fid: str, severity: str, title: str, numbers: dict[str, Any], rule: str, evidence: dict[str, Any] | None,
             fix: dict[str, Any], columns: list[str] | None = None) -> dict[str, Any]:
    return {"id": fid, "severity": severity, "title": title, "numbers": numbers, "rule": rule,
            "evidence": evidence or {"columns": [], "rows": []}, "fix": fix, "columns": columns or []}


def _fix(kind: str, text: str, transforms: list[dict[str, Any]] | None = None, selected: bool = True) -> dict[str, Any]:
    return {"kind": kind, "text": text, "transforms": transforms or [], "selected": bool(transforms) and selected}


def _jsonable(v: Any) -> Any:
    if v is None or (isinstance(v, float) and not math.isfinite(v)):
        return None
    if isinstance(v, (np.integer,)):
        return int(v)
    if isinstance(v, (np.floating,)):
        f = float(v)
        return f if math.isfinite(f) else None
    if isinstance(v, (np.bool_,)):
        return bool(v)
    if isinstance(v, float) and v.is_integer() and abs(v) < 1e15:
        return int(v)
    if pd.isna(v) if not isinstance(v, (list, dict, str)) else False:
        return None
    return v


def _rows(df: pd.DataFrame, mask: np.ndarray | pd.Series, cols: list[str], n: int = 5) -> dict[str, Any]:
    """Up to n example rows (with their row number) where `mask` holds, showing `cols`."""
    idx = np.flatnonzero(np.asarray(mask))[:n]
    cols = [c for c in dict.fromkeys(cols) if c in df.columns]
    return {"columns": ["row", *cols],
            "rows": [[int(i), *[_jsonable(df.iat[i, df.columns.get_loc(c)]) for c in cols]] for i in idx]}


def _pos(y: pd.Series) -> tuple[pd.Series, str] | None:
    """A binary target as 0/1 and the name of its '1' class (the minority, or the larger value when equal)."""
    v = y.dropna()
    if v.nunique() != 2:
        return None
    counts = v.astype(str).value_counts()
    one = counts.index[-1] if counts.iloc[0] != counts.iloc[-1] else sorted(counts.index)[1]
    return (y.astype(str) == one).astype(float).where(y.notna()), one


def single_auc(x: pd.Series, y01: pd.Series) -> float | None:
    """AUC of one column alone against a 0/1 target (max of it and 1 − it). Numbers rank as they are; a category
    ranks by its own target rate (in sample, so only for ≤ 50 values with ≥ 20 rows each)."""
    from scipy.stats import rankdata

    ok = x.notna() & y01.notna()
    if ok.sum() < 50:
        return None
    xs, ys = x[ok], y01[ok].to_numpy()
    if not pd.api.types.is_numeric_dtype(xs) or pd.api.types.is_bool_dtype(xs):
        vc = xs.astype(str).value_counts()
        if len(vc) > MAX_CLASSES or len(vc) < 2 or vc.min() < 20:
            return None
        xs = xs.astype(str).map(pd.Series(ys, index=xs.index).groupby(xs.astype(str)).mean())
    n1, n0 = int(ys.sum()), int((1 - ys).sum())
    if n1 == 0 or n0 == 0:
        return None
    r = rankdata(xs.to_numpy(dtype=float))
    auc = (r[ys == 1].sum() - n1 * (n1 + 1) / 2) / (n1 * n0)
    return float(max(auc, 1 - auc))


def _cramers_v(a: pd.Series, b: pd.Series) -> float | None:
    from scipy.stats import chi2_contingency

    t = pd.crosstab(a, b)
    if t.shape[0] < 2 or t.shape[1] < 2:
        return None
    chi2 = chi2_contingency(t, correction=False)[0]
    n = t.to_numpy().sum()
    return float(math.sqrt(chi2 / (n * (min(t.shape) - 1))))


def _binned(s: pd.Series) -> pd.Series:
    """A column as at most ~10 groups: numbers by decile, categories as they are (missing is its own group)."""
    if pd.api.types.is_numeric_dtype(s) and s.nunique() > 10:
        try:
            return pd.qcut(s, 10, duplicates="drop").astype(str)
        except ValueError:
            return s.astype(str)
    return s.astype(str)


def auc_halfwidth(auc: float, n_pos: int, n_neg: int) -> float:
    """95 % half-width of an AUC on n_pos positives and n_neg negatives (Hanley & McNeil 1982)."""
    if n_pos < 1 or n_neg < 1:
        return float("nan")
    q1, q2 = auc / (2 - auc), 2 * auc * auc / (1 + auc)
    var = (auc * (1 - auc) + (n_pos - 1) * (q1 - auc * auc) + (n_neg - 1) * (q2 - auc * auc)) / (n_pos * n_neg)
    return 1.96 * math.sqrt(max(var, 0.0))


# ------------------------------------------------------------------------------------------------------- the checks

class Ctx:
    def __init__(self, df: pd.DataFrame, target: str | None, roles: list[dict[str, Any]]):
        self.df, self.target, self.n = df, target, len(df)
        self.cols = {c["name"]: c for c in roles}
        self.types = {c: (self.cols[c]["type"] if c in self.cols else detect_type(str(c), df[c])) for c in df.columns}
        y = df[target] if target else None
        self.y = y
        k = int(y.nunique(dropna=True)) if y is not None else 0
        multilabel = target and ((self.cols.get(target) or {}).get("role") == "multilabel"
                                 or any(a["role"] == "multilabel" for a in (self.cols.get(target) or {}).get("also", [])))
        if y is None or k < 2 or multilabel:
            self.task = None  # no target, or a multi-label list (its labels are checked by the multi-label flow)
        elif pd.api.types.is_numeric_dtype(y) and k > MAX_CLASSES:
            self.task = "regression"
        else:
            self.task = "binary" if k == 2 else "multiclass"
        b = _pos(y) if self.task == "binary" else None
        self.y01, self.pos = (b if b else (None, None))
        self.excluded_roles = {"id", "constant"}
        self.coded: list[str] = []  # set by check_coded_values (rare codes skips them)
        self.coerced: list[dict[str, Any]] = []  # columns read as numbers at upload (profiling.coerce_numeric)

    def role(self, c: str) -> str:
        return (self.cols.get(c) or {}).get("role", "feature")

    def also(self, c: str) -> list[dict[str, Any]]:
        return (self.cols.get(c) or {}).get("also", [])

    def features(self) -> list[str]:
        return [c for c in self.df.columns if c != self.target]

    def model_features(self) -> list[str]:
        return [c for c in self.features() if self.role(c) not in ("id", "constant", "leak", "protected", "decision", "surname", "zip")]


def check_leakage(cx: Ctx) -> list[dict[str, Any]]:
    """Columns that may be known only after the outcome: Smart Intake's leak roles (names, values filled only for one
    class, purity), and any column that alone predicts the target too well."""
    if not cx.target or not cx.task:
        return []
    sus: dict[str, dict[str, Any]] = {}
    for c in cx.features():
        roles = [{"role": cx.role(c), "confidence": cx.cols.get(c, {}).get("confidence", 0), "evidence": cx.cols.get(c, {}).get("evidence", [])},
                 *cx.also(c)]
        for r in roles:
            if r["role"] == "leak":
                s = sus.setdefault(c, {"column": c, "confidence": 0.0, "reasons": [], "auc": None})
                s["confidence"] = max(s["confidence"], float(r["confidence"]))
                s["reasons"] += [e for e in r["evidence"] if e not in s["reasons"]]
    rule_auc = None
    for c in cx.features():
        if cx.types[c] in ("id", "text"):
            continue
        x = cx.df[c]
        if cx.task == "binary":
            a = single_auc(x, cx.y01)
            if a is not None and c in sus:
                sus[c]["auc"] = round(a, 4)
            if a is not None and a >= 0.9:
                s = sus.setdefault(c, {"column": c, "confidence": 0.0, "reasons": [], "auc": round(a, 4)})
                s["confidence"] = max(s["confidence"], 0.9 if a >= 0.97 else 0.6)
                s["reasons"].append(f"alone it separates {cx.target} with AUC {a:.3f}")
        elif cx.task == "regression" and pd.api.types.is_numeric_dtype(x):
            rho = x.corr(cx.y, method="spearman")
            if rho is not None and np.isfinite(rho) and abs(rho) >= 0.85:
                s = sus.setdefault(c, {"column": c, "confidence": 0.0, "reasons": [], "auc": None})
                s["confidence"] = max(s["confidence"], 0.9 if abs(rho) >= 0.99 else 0.6)
                s["reasons"].append(f"alone its rank correlation with {cx.target} is {rho:+.3f}")
                s["rho"] = round(float(rho), 4)
                if VALUE_AFTER.search(str(c)):
                    s["reasons"].append(f"its name ('{c}') reads like a value set at or after the outcome (an assessment, a "
                                        "valuation, a settlement)")
    if cx.task == "binary":  # a name-only suspect (confidence < 0.85) must also be strong alone: German 'duration' is a loan term
        sus = {c: s for c, s in sus.items() if s["confidence"] >= 0.85 or (s["auc"] or 0) >= WEAK_SUSPECT_AUC}
    rule_auc = ("binary: single-column AUC ≥ 0.90 is a suspect, ≥ 0.97 is high; a suspect by name alone must reach AUC ≥ 0.75" if cx.task == "binary" else
                "regression: |Spearman ρ| ≥ 0.85 is a suspect (a single column that close to the target is rare unless it is "
                "derived from it), ≥ 0.99 is high" if cx.task == "regression" else "")
    if not sus:
        return []
    items = sorted(sus.values(), key=lambda s: (-s["confidence"], -(s["auc"] or 0)))
    high = [s for s in items if s["confidence"] >= 0.85]
    cols = [s["column"] for s in items]
    nums: dict[str, Any] = {"suspects": len(items)}
    for s in items[:3]:
        if s["auc"] is not None:
            nums[f"AUC of {s['column']} alone"] = s["auc"]
        x = cx.df[s["column"]]
        if cx.task == "binary" and pd.api.types.is_numeric_dtype(x) and not pd.api.types.is_bool_dtype(x):
            nums[f"median {s['column']} when {cx.target} = {cx.pos}"] = _jsonable(round(float(x[cx.y01 == 1].median()), 4))
            nums[f"median {s['column']} otherwise"] = _jsonable(round(float(x[cx.y01 == 0].median()), 4))
    for s in items:  # what the suspicion rests on: a name / values rule, or only how strong the column is alone
        s["basis"] = "strength only" if all(r.startswith("alone ") for r in s["reasons"]) else "name or values"
    sev = "high" if high else "medium" if cx.task in ("binary", "regression") or any(s["auc"] for s in items) else "low"
    strength_only = all(s["basis"] == "strength only" for s in items)
    names = f"{', '.join(cols[:4])}{'…' if len(cols) > 4 else ''}"
    title = (f"Leak or strong signal? {names} alone predict{'s' if len(items) == 1 else ''} {cx.target} very well: "
             f"confirm {'it is' if len(items) == 1 else 'each is'} known when you decide" if strength_only and not high else
             f"Leakage suspect{'s' if len(items) > 1 else ''}: {names} may be known only after the outcome")
    ev_cols = cols[:4] + [cx.target]
    ev_mask = np.ones(cx.n, bool)
    if cx.task in ("binary", "multiclass"):  # three example rows of each of the two largest classes, side by side
        ev_mask = np.zeros(cx.n, bool)
        for v in cx.y.astype(str).value_counts().index[:2][::-1]:
            ev_mask[np.flatnonzero((cx.y.astype(str) == v).to_numpy())[:3]] = True
    return [_finding("leakage", sev, title, nums,
                     "A column is a suspect when its name or values say it is filled in after the outcome (Smart Intake's "
                     "leak rules), or when it alone predicts the target (" + rule_auc + "). Only you know what is available when the decision is made.",
                     _rows(cx.df, ev_mask, ev_cols, n=6),
                     _fix("question", "Answer 'known when you decide?' below for each column. 'Not known' drops it from every "
                                      "run on this dataset; 'known' keeps it. Until you answer, it stays in every run, the run "
                                      "records it as unanswered, and the Model Doctor treats it as an open leak question. "
                                      "Strength alone is not proof of a leak: a strong real signal looks the same."),
                     columns=cols) | {"suspects": items}]


def check_coded_values(cx: Ctx) -> list[dict[str, Any]]:
    """Negative codes in a count-like column: Taiwan's PAY_* use -2 / -1 for 'no use' / 'paid on time' among months
    of delay. Integer column, ≤ 20 values, ≥ 50 % non-negative, 1–3 negative values holding ≥ 1 % of rows; not named
    like a signed quantity (balance, change, rate, …)."""
    groups: list[dict[str, Any]] = []
    for c in cx.model_features():
        s = cx.df[c]
        if not pd.api.types.is_integer_dtype(s) or SIGNED_NAME.search(str(c)):
            continue
        nn = s.dropna()
        k = nn.nunique()
        if k > 20 or k < 3 or (nn >= 0).mean() < 0.5:
            continue
        neg = nn[nn < 0].value_counts()
        if not 1 <= len(neg) <= 3 or neg.sum() / len(nn) < 0.01 or neg.index.min() < -9:
            continue
        groups.append({"column": c, "codes": sorted(int(v) for v in neg.index), "rows": int(neg.sum()),
                       "share": round(float(neg.sum() / len(nn)), 4)})
    cx.coded = [g["column"] for g in groups]
    if not groups:
        return []
    cols = [g["column"] for g in groups]
    codes = sorted({v for g in groups for v in g["codes"]})
    share = max(g["share"] for g in groups)
    mask = cx.df[cols].lt(0).any(axis=1)
    return [_finding("coded_values", "medium",
                     f"Coded values: {', '.join(map(str, codes))} in {', '.join(cols[:6])}{'…' if len(cols) > 6 else ''} are codes, not amounts",
                     {"columns": len(cols), "rows with a code": int(mask.sum()), "largest share": share},
                     "An integer column with ≤ 20 values, mostly ≥ 0, and 1–3 negative values holding ≥ 1 % of rows reads as a "
                     "count with codes mixed in (e.g. −2 'no use', −1 'paid on time'). As numbers, the model treats them as "
                     "less than zero months.",
                     _rows(cx.df, mask, cols[:6] + ([cx.target] if cx.target else [])),
                     _fix("transform", f"Split each into the count (codes → 0) and a separate code column ({cols[0]}_code, …).",
                          [{"op": "split_codes", "column": g["column"], "codes": g["codes"], "fill": 0} for g in groups]),
                     columns=cols) | {"items": groups}]


def check_disguised_missing(cx: Ctx) -> list[dict[str, Any]]:
    """Missing values written as a number or a word: 999 / -999 / 99999 far outside the rest, or 'unknown', '?', 'N/A'."""
    items: list[dict[str, Any]] = []
    for c in cx.model_features():
        s = cx.df[c]
        nn = s.dropna()
        if len(nn) < 20:
            continue
        if pd.api.types.is_numeric_dtype(s) and not pd.api.types.is_bool_dtype(s):
            for v in NUM_SENTINELS:
                m = nn == v
                share = float(m.mean())
                if share < 0.005:
                    continue
                rest = nn[~m]
                if rest.empty:
                    continue
                lo, hi = float(rest.quantile(0.001)), float(rest.quantile(0.999))
                spread = max(hi - lo, 1.0)
                far = (v > hi + spread) if v > 0 else (v < lo - spread / 10 and lo >= 0)
                if far:
                    items.append({"column": c, "values": [v], "rows": int(m.sum()), "share": round(share, 4),
                                  "rest": f"{lo:g} … {hi:g}"})
        elif s.dtype == object:
            low = nn.astype(str).str.strip().str.lower()
            hit = low.isin(STR_SENTINELS)
            if hit.mean() >= 0.005:
                vals = sorted(set(nn[hit].astype(str)))
                items.append({"column": c, "values": vals, "rows": int(hit.sum()), "share": round(float(hit.mean()), 4),
                              "rest": f"{nn[~hit].nunique()} other values"})
    out = []
    numeric = [i for i in items if not isinstance(i["values"][0], str)]
    words = [i for i in items if isinstance(i["values"][0], str)]
    if numeric:
        out.append(_missing_finding(cx, "disguised_missing", "medium", "Disguised missing values", numeric))
    if words:
        out.append(_missing_finding(cx, "missing_words", "low", "Missing written as a word", words))
    return out


def _missing_finding(cx: Ctx, fid: str, sev: str, what: str, items: list[dict[str, Any]]) -> dict[str, Any]:
    big = max(items, key=lambda i: i["share"])
    cols = [i["column"] for i in items]
    desc = ", ".join(f"{i['column']} = {i['values'][0]!r} ({i['share']:.0%})" for i in items[:4])
    mask = np.zeros(cx.n, bool)
    for i in items:
        s = cx.df[i["column"]]
        mask |= (s.isin(i["values"]) if not isinstance(i["values"][0], str) else s.astype(str).isin(i["values"])).to_numpy()
    return _finding(fid, sev, f"{what}: {desc}{'…' if len(items) > 4 else ''}",
                     {"columns": len(items), "largest share": big["share"], "rows affected": int(mask.sum())},
                     "Numbers: −1, −9, −99, −999, 99, 999, 9999, … on ≥ 0.5 % of rows and beyond the rest of the column's range "
                     "(more than its own spread above the 99.9th percentile, or below 0 in a column that is otherwise ≥ 0). Words: "
                     "'unknown', '?', 'N/A', 'null', '-' on ≥ 0.5 % of rows. As a number, 999 days pulls every average "
                     "and split towards it.",
                     _rows(cx.df, mask, cols[:5] + ([cx.target] if cx.target else [])),
                     _fix("transform", "Make them missing and keep a 'was missing' flag column, so the information stays.",
                          [{"op": "to_missing", "column": i["column"], "values": i["values"], "flag": True} for i in items],
                          selected=fid == "disguised_missing"),
                     columns=cols) | {"items": items}


def check_rare_codes(cx: Ctx) -> list[dict[str, Any]]:
    """Undocumented codes: in an integer code column (≤ 12 values), values outside the main contiguous run that are
    each < 1 % of rows (Taiwan EDUCATION 1–4 documented, 0 / 5 / 6 not; MARRIAGE 0)."""
    items = []
    coded = set(cx.coded)
    for c in cx.model_features():
        s = cx.df[c]
        if not pd.api.types.is_integer_dtype(s) or c in coded:
            continue
        vc = s.dropna().value_counts()
        if not 3 <= len(vc) <= 12:
            continue
        share = vc / vc.sum()
        main = sorted(int(v) for v in share[share >= 0.01].index)
        if len(main) < 2:
            continue
        run = set(range(main[0], main[-1] + 1))
        if not set(main) == run:
            continue
        rare = sorted(int(v) for v in vc.index if int(v) not in run)
        if not rare or float(share[rare].sum()) >= 0.05:
            continue
        above = [v for v in rare if v > main[-1]]
        below = [v for v in rare if v < main[0]][::-1]
        thin = all(vc[a] >= vc[b] for side in (above, below) for a, b in zip(side, side[1:]))
        if rare == above and thin:
            continue  # a count's thinning tail (previous = 3, 4, 5 …), not codes
        if thin and cx.target and cx.task in ("regression", "binary"):
            from scipy.stats import spearmanr

            yy = cx.y.astype(float) if cx.task == "regression" else cx.y01
            m_ = yy.groupby(s).mean().dropna()
            if len(m_) >= 4 and abs(spearmanr(m_.index.astype(float), m_.to_numpy())[0]) >= 0.9:
                continue  # an ordinal scale whose rare ends continue the outcome's trend (house grade 1 … 13), not codes
        items.append({"column": c, "codes": rare, "documented": f"{main[0]}…{main[-1]}", "rows": int(vc[rare].sum()),
                      "share": round(float(share[rare].sum()), 4)})
    if not items:
        return []
    cols = [i["column"] for i in items]
    mask = np.zeros(cx.n, bool)
    for i in items:
        mask |= cx.df[i["column"]].isin(i["codes"]).to_numpy()
    desc = "; ".join(f"{i['column']} {', '.join(map(str, i['codes']))} outside {i['documented']}" for i in items[:3])
    return [_finding("rare_codes", "low", f"Undocumented codes: {desc}",
                     {"columns": len(items), "rows affected": int(mask.sum()), "largest share": max(i["share"] for i in items)},
                     "An integer code column (3–12 values) whose common values (≥ 1 % each) form a run, plus values outside "
                     "the run holding < 5 % together: codes the data dictionary probably does not define. A thinning tail above the run (a count's "
                     "large values: 3, 4, 5 … each rarer) is not flagged, nor thinning tails at both ends of a scale whose outcome "
                     "keeps its trend through them (|Spearman| ≥ 0.9 of value vs mean outcome: a grade 1 … 13).",
                     _rows(cx.df, mask, cols + ([cx.target] if cx.target else [])),
                     _fix("transform", "Map them to missing ('unknown') with a flag; check the data dictionary.",
                          [{"op": "to_missing", "column": i["column"], "values": i["codes"], "flag": True} for i in items], selected=False),
                     columns=cols) | {"items": items}]


def check_informative_missing(cx: Ctx) -> list[dict[str, Any]]:
    """Columns whose missingness itself predicts the target: the outcome rate differs between rows with and without a
    value (two-proportion z, p < 0.001, Δ ≥ 5 points)."""
    if cx.task not in ("binary", "regression") or not cx.target:
        return []
    from scipy.stats import norm

    items = []
    for c in cx.model_features():
        m = cx.df[c].isna()
        r = float(m.mean())
        if r < 0.01 or r > 0.95:
            continue
        y = cx.y01 if cx.task == "binary" else cx.y.astype(float)
        a, b = y[m].dropna(), y[~m].dropna()
        if len(a) < 20 or len(b) < 20:
            continue
        d = float(a.mean() - b.mean())
        se = math.sqrt(a.var(ddof=1) / len(a) + b.var(ddof=1) / len(b)) or 1e-12
        p = float(2 * norm.sf(abs(d) / se))
        scale = 1.0 if cx.task == "binary" else float(cx.y.std() or 1)
        if p < 0.001 and abs(d) / scale >= 0.05:
            items.append({"column": c, "missing_share": round(r, 4), "rate_missing": round(float(a.mean()), 4),
                          "rate_present": round(float(b.mean()), 4), "p": p})
    if not items:
        return []
    it = items[0]
    what = f"{cx.pos!r} rate" if cx.task == "binary" else f"mean {cx.target}"
    cols = [i["column"] for i in items]
    return [_finding("informative_missing", "low",
                     f"Missing says something: where {it['column']} is missing, the {what} is {it['rate_missing']:.3g} vs {it['rate_present']:.3g}",
                     {"columns": len(items), f"{what} when missing": it["rate_missing"], f"{what} when present": it["rate_present"],
                      "missing share": it["missing_share"]},
                     "Missing 1–95 % of rows, and the outcome differs between rows with and without a value: two-sample z test "
                     "p < 0.001 and a gap ≥ 5 points (regression: ≥ 0.05 standard deviations).",
                     _rows(cx.df, cx.df[cols[0]].isna(), cols[:4] + [cx.target]),
                     _fix("transform", "Keep a 'was missing' flag for each, so the signal survives any imputation.",
                          [{"op": "add_missing_flag", "column": i["column"]} for i in items]),
                     columns=cols) | {"items": items}]


def _row_keys(df: pd.DataFrame) -> list[str]:
    """Columns that are unique per row (a row key): they would hide every duplicate. Anything else counts, including a
    column the profile calls an ID that repeats (a customer seen twice) or a mistyped one."""
    n = len(df)
    return [c for c in df.columns if n > 1 and df[c].notna().all() and df[c].nunique() == n
            and not pd.api.types.is_float_dtype(df[c])]


def _content_cols(cx: Ctx) -> list[str]:
    keys = set(_row_keys(cx.df))
    return [c for c in cx.df.columns if c not in keys]


def check_duplicates(cx: Ctx) -> list[dict[str, Any]]:
    """Exact duplicates (every column but row keys, which are unique per row) and near-duplicates (same after trimming and lower-casing text and rounding
    numbers to 4 significant digits)."""
    cols = _content_cols(cx) or list(cx.df.columns)
    dup = cx.df.duplicated(subset=cols, keep="first")
    n_dup = int(dup.sum())
    norm = cx.df[cols].copy()
    for c in cols:
        s = norm[c]
        if pd.api.types.is_float_dtype(s):
            norm[c] = s.map(lambda v: float(f"{v:.4g}") if pd.notna(v) else v)
        elif s.dtype == object:
            norm[c] = s.astype(str).str.strip().str.lower().str.replace(r"\s+", " ", regex=True)
    near = norm.duplicated(keep="first") & ~dup
    n_near = int(near.sum())
    if n_dup + n_near == 0:
        return []
    share = n_dup / max(cx.n, 1)
    sev = "medium" if share >= 0.02 else "low"
    first_dup = dup | near
    title = (f"{n_dup:,} exact duplicate rows ({share:.1%})" + (f" and {n_near:,} near-duplicates" if n_near else "")) if n_dup \
        else f"{n_near:,} near-duplicate rows (same after trimming, case and rounding)"
    return [_finding("duplicates", sev, title,
                     {"exact duplicates": n_dup, "share": round(share, 4), "near-duplicates": n_near},
                     "Medium when exact duplicates are ≥ 2 % of rows. Exact: the same values in every column except row keys (columns unique per row). Near: the same after trimming and lower-casing text "
                     "and rounding numbers to 4 significant digits. A duplicate split across training and holdout makes the "
                     "score optimistic.",
                     _rows(cx.df, first_dup, cols[:6]),
                     _fix("transform", "Drop the exact duplicates (keep the first)." + (" Near-duplicates are listed, not dropped." if n_near else ""),
                          [{"op": "drop_duplicates"}] if n_dup else [], selected=True))]


def check_repeated_entities(cx: Ctx) -> list[dict[str, Any]]:
    """An ID-like column (the profile's ID type, or text with more distinct values than half the rows) whose values
    repeat: the same customer more than once. A random split then puts copies of one entity on both sides, and the
    holdout score is optimistic."""
    items = []
    for c in cx.features():
        s = cx.df[c]
        if cx.types[c] != "id" and not (s.dtype == object and s.nunique() > 0.5 * cx.n):
            continue
        if pd.api.types.is_float_dtype(s):
            continue
        v = s.astype(str).where(s.notna())
        rep = v.duplicated(keep=False) & v.notna()
        if rep.sum() >= max(10, 0.01 * cx.n):
            items.append({"column": c, "rows": int(rep.sum()), "entities": int(v[rep].nunique()), "share": round(float(rep.mean()), 4),
                          "_mask": rep.to_numpy()})
    if not items:
        return []
    it = items[0]
    mask = it.pop("_mask")
    for i in items[1:]:
        i.pop("_mask", None)
    return [_finding("repeated_entities", "medium" if it["share"] >= 0.02 else "low",
                     f"The same {it['column']} appears more than once: {it['entities']:,} values on {it['rows']:,} rows ({it['share']:.1%})",
                     {"rows": it["rows"], "entities": it["entities"], "share": it["share"]},
                     "An ID-like column whose values repeat (≥ 1 % of rows): the same entity several times, as a later snapshot or a "
                     "copy. A random split puts copies on both sides, so the model is scored on entities it has seen. Medium when "
                     "≥ 2 % of rows.",
                     _rows(cx.df, mask, [it["column"]] + [c for c in cx.features() if c != it["column"]][:4] + ([cx.target] if cx.target else [])),
                     _fix("advice", f"Split by {it['column']} (New Model → split → group, pre-filled): every {it['column']} then sits "
                                    "in training or in the holdout, never both. If the repeats are copies, de-duplicate them."),
                     columns=[i["column"] for i in items]) | {"items": items}]


def check_conflicts(cx: Ctx) -> list[dict[str, Any]]:
    """Identical rows (every feature, ignoring IDs) with different labels."""
    if not cx.target or not cx.task or cx.task == "regression":
        return []
    feats = [c for c in _content_cols(cx) if c != cx.target]
    if not feats:
        return []
    key = pd.util.hash_pandas_object(cx.df[feats].astype(str), index=False)
    g = cx.df[cx.target].astype(str).groupby(key.to_numpy()).transform("nunique")
    mask = (g > 1).to_numpy()
    n = int(mask.sum())
    if n == 0:
        return []
    groups = int(pd.Series(key[mask]).nunique())
    share = n / cx.n
    sev = "medium" if share >= 0.02 else "low"
    rows = np.flatnonzero(mask).tolist()
    return [_finding("conflicts", sev, f"{groups:,} sets of identical rows carry different labels ({n:,} rows)",
                     {"rows": n, "groups": groups, "share": round(share, 4)},
                     "Rows with the same value in every feature (IDs ignored) but a different label. No model can get both "
                     "right: either the label is wrong, or the table lacks the column that separates them.",
                     _rows(cx.df, mask, feats[:5] + [cx.target]),
                     _fix("transform", "Send them to label review (Models → Doctor → Labels after a run), or drop them.",
                          [{"op": "drop_rows", "rows": rows[:20000], "reason": "conflicting labels"}], selected=False),
                     columns=[cx.target])]


def check_class_balance(cx: Ctx) -> list[dict[str, Any]]:
    """Class balance and rows per class: a class with fewer than ~50 rows is hard to learn and to score."""
    if cx.task not in ("binary", "multiclass"):
        return []
    vc = cx.y.dropna().astype(str).value_counts()
    k, small, big = len(vc), int(vc.min()), int(vc.max())
    rare_share = small / vc.sum()
    few = vc[vc < 50]
    under100 = int((vc < 100).sum())
    nums = {"classes": k, "rows in the smallest": small, "rows in the largest": big,
            "smallest share": round(float(rare_share), 4), "classes under 100 rows": under100}
    sev, parts = None, []
    if small < 10:
        sev = "high"
    elif small < 50:
        sev = "medium"
    elif rare_share < 0.05 or (k > 2 and under100 > 0):
        sev = "low"
    if sev is None:
        return []
    if k > 2:
        title = f"{k} classes with {small:,}–{big:,} rows each" + (f": {len(few)} have under 50" if len(few) else "")
    else:
        title = f"Imbalanced: '{vc.index[-1]}' is {rare_share:.1%} of rows ({small:,})"
    holdout = max(1, round(small * 0.2))
    nums["smallest class in a 20 % holdout"] = holdout
    transforms = []
    tiny = vc[vc < 20]
    if len(tiny) >= 2 and k > 3:  # two or more tiny classes become one 'other' class (one alone would just be renamed)
        transforms = [{"op": "merge_classes", "column": cx.target, "classes": list(tiny.index), "into": "other"}]
        parts.append(f"merge the {len(tiny)} classes under 20 rows into 'other'")
    text = ("Collect more rows for the smallest classes (≥ 100 each is a good floor)"
            + (", or " + parts[0] if parts else "") + ". Expect them to be the weakest; read per-class recall, not accuracy."
            if k > 2 else "Score it with a business cost or a balanced metric (the binary suggestion pre-fills it), not accuracy.")
    smallest = vc.iloc[::-1].head(6)  # evidence: the smallest classes and their rows (all and in a 20 % holdout)
    ev = {"columns": ["class", "rows", "in a 20 % holdout"],
          "rows": [[str(c), int(v), int(round(v * 0.2))] for c, v in smallest.items()]}
    return [_finding("class_balance", sev, title, nums,
                     "Rows per class: < 10 is high (it cannot be learned or scored), < 50 is medium; a minority share < 5 %, or "
                     "any class under 100 rows when there are more than two, is low. The holdout keeps 20 % of each class.",
                     ev,
                     _fix("transform" if transforms else "advice", text, transforms, selected=False),
                     columns=[cx.target]) | {"classes": [{"name": n, "count": int(v)} for n, v in vc.items()][-12:]}]


def check_sample_size(cx: Ctx) -> list[dict[str, Any]]:
    """Rows per feature, and events per variable (EPV: rows of the smallest class per feature)."""
    if not cx.task:
        return []
    p = len([c for c in cx.model_features() if cx.types[c] != "text"]) or 1
    has_text = any(cx.types[c] == "text" for c in cx.model_features())
    nums: dict[str, Any] = {"rows": cx.n, "features": p, "rows per feature": round(cx.n / p, 1)}
    sev = None
    if cx.task == "binary":
        events = int(cx.y.dropna().astype(str).value_counts().min())
        epv = events / p
        nums.update({"events (smallest class)": events, "events per feature": round(epv, 1)})
        if cx.task == "binary" and not has_text:
            n1 = round(events * 0.2)
            n0 = round((cx.y.notna().sum() - events) * 0.2)
            hw = auc_halfwidth(0.75, n1, n0)
            nums["holdout AUC ± (at AUC 0.75)"] = round(hw, 3)
        if epv < 5:
            sev = "high"
        elif cx.n >= 50_000 and epv >= 10:
            sev = "low"  # plenty of rows: the rare class is what is thin, not the table
        elif epv < 20 or cx.n < 2000:
            sev = "medium"
        title = (f"Few events: {events:,} rows of the rarest class for {p} features ({epv:.0f} per feature) in {cx.n:,} rows"
                 if cx.n >= 50_000 else f"Small sample: {events:,} rows of the rarest class for {p} features ({epv:.0f} per feature)")
    else:  # multiclass: rows per class is the class-balance check; here rows per feature
        rpf = cx.n / p
        if rpf < 10:
            sev = "high"
        elif rpf < 50 or cx.n < 1000:
            sev = "medium"
        title = f"Small sample: {cx.n:,} rows for {p} features ({rpf:.0f} per feature)"
    if sev is None:
        return []
    return [_finding("sample_size", sev, title, nums,
                     "Events per feature (rows of the smallest class ÷ model features): < 5 high; < 20, or fewer than 2,000 rows, "
                     "medium (binary); low instead of medium when the table has ≥ 50,000 rows and ≥ 10 events per feature. Regression and multiclass: rows per feature < 10 high, < 50 medium. "
                     "The AUC ± is the 95 % half-width a 20 % holdout of this size gives (Hanley–McNeil at AUC 0.75).",
                     None,
                     _fix("advice", "Prefer simpler models (scorecard, a single GBM) and read the confidence intervals; "
                                    "the ladder recommends the simplest model within the CI of the best. Collecting more "
                                    "rows of the rarest class helps most."))]


def check_time(cx: Ctx) -> list[dict[str, Any]]:
    """Time coverage and drift, if a date exists: rows per period, gaps, and the outcome across five time-ordered bins."""
    date = next((c for c in cx.df.columns if cx.role(c) == "date" or any(a["role"] == "date" for a in cx.also(c))), None)
    if not date:
        return []
    from .splits import time_values
    from scipy.stats import chi2_contingency, kruskal

    t = time_values(cx.df[date])
    ok = t.notna()
    if ok.sum() < 50:
        return []
    nums: dict[str, Any] = {"from": str(t[ok].min())[:10], "to": str(t[ok].max())[:10]}
    gaps = 0
    if pd.api.types.is_datetime64_any_dtype(t):
        per = t[ok].dt.to_period("M").value_counts().sort_index()
        full = pd.period_range(per.index.min(), per.index.max(), freq="M")
        gaps = int(len(full) - len(per))
        nums.update({"months": len(full), "months without rows": gaps, "rows per month (median)": int(per.median())})
    sev, drift = None, None
    if cx.target and cx.task:
        order = t.rank(method="first")
        bins = pd.qcut(order[ok], 5, labels=False)
        y = cx.df.loc[ok, cx.target]
        if cx.task == "regression":
            means = y.groupby(bins).mean()
            p = float(kruskal(*[y[bins == b].dropna() for b in range(5)]).pvalue)
            rel = float((means.max() - means.min()) / (abs(y.std()) or 1))
            drift = {"first": float(means.iloc[0]), "last": float(means.iloc[-1]), "p": p}
            if p < 0.001 and rel >= 0.1:
                sev = "medium"
        else:
            tab = pd.crosstab(bins, y.astype(str))
            p = float(chi2_contingency(tab)[1])
            yy = (y.astype(str) == (cx.pos or tab.columns[-1])).astype(float)
            rate = yy.groupby(bins).mean()
            drift = {"first": float(rate.iloc[0]), "last": float(rate.iloc[-1]), "p": p}
            if p < 0.001 and abs(rate.max() - rate.min()) >= 0.05:
                sev = "medium"
        nums.update({"outcome, first fifth": round(drift["first"], 4), "outcome, last fifth": round(drift["last"], 4),
                     "p (drift across time)": float(f"{drift['p']:.3g}")})
    if gaps and sev is None:
        sev = "low"
    if sev is None:
        sev = "info"
    title = (f"Outcome drifts over time ({date}): {drift['first']:.3g} in the first fifth → {drift['last']:.3g} in the last"
             if sev == "medium" else f"{date} covers {nums['from']} → {nums['to']}" + (f" with {gaps} empty months" if gaps else ""))
    return [_finding("time", sev, title, nums,
                     "Rows ordered by the date and cut into five equal bins; drift when the outcome differs across them "
                     "(χ² or Kruskal–Wallis p < 0.001) by ≥ 5 points (regression: ≥ 0.1 standard deviations). Gaps: calendar "
                     "months with no rows.",
                     None,
                     _fix("advice", f"Use an out-of-time split by {date} (New Model pre-fills it): a random split trains on the "
                                    "future and over-states the score."), columns=[date])]


def _numeric_id(c: str, s: pd.Series, n: int) -> bool:
    """An integer column that is (almost) unique per row and named like a reference, or a run of consecutive numbers
    (a sequence): a key, not a quantity."""
    if not pd.api.types.is_integer_dtype(s) or n < 200:
        return False
    nn = s.dropna()
    if nn.nunique() < 0.95 * len(nn):
        return False
    if REF_NAME.search(str(c)):
        return True
    d = np.diff(np.sort(nn.to_numpy()))
    return bool(len(d) and (d == 1).mean() >= 0.9 and nn.min() >= 1000)


UNIT_FACTORS = {100: "× 100 (pounds → pence, dollars → cents)", 1000: "× 1,000 (thousands, or kg → g)",
                10.7639: "× 10.76 (m² → ft²)", 0.092903: "× 0.0929 (ft² → m²)", 0.01: "÷ 100 (pence → pounds)",
                0.001: "÷ 1,000", 2.54: "× 2.54 (inches → cm)", 0.3937: "÷ 2.54 (cm → inches)",
                2.2046: "× 2.205 (kg → lb)", 0.4536: "× 0.454 (lb → kg)", 12: "× 12 (months / years)", 0.0833: "÷ 12"}


def check_unit_change(cx: Ctx) -> list[dict[str, Any]]:
    """A number column whose scale jumps at one date and stays there (a unit change: pounds → pence, ft² → m²), when a
    date exists. Rows ordered by the date, cut into 12 equal bins; the median of the column's non-zero values per bin;
    a step where the medians after a cut differ from those before by a factor ≥ 3 (or ≤ 1/3), consistently (every bin
    on each side within a factor 1.6 of its side's median)."""
    date = next((c for c in cx.df.columns if cx.role(c) == "date" or any(a["role"] == "date" for a in cx.also(c))), None)
    if not date:
        return []
    from .splits import time_values

    t = time_values(cx.df[date])
    ok = t.notna().to_numpy()
    if ok.sum() < 240:
        return []
    order = np.argsort(t[ok].to_numpy(), kind="stable")
    rows = np.flatnonzero(ok)[order]
    bins = np.array_split(rows, 12)
    items = []
    for c in cx.model_features():
        x = cx.df[c]
        if c == date or not pd.api.types.is_numeric_dtype(x) or pd.api.types.is_bool_dtype(x) or x.nunique() < 20:
            continue
        med = []
        for b in bins:
            v = x.iloc[b]
            v = v[(v != 0) & v.notna()].abs()
            med.append(float(v.median()) if len(v) >= 10 else np.nan)
        med = np.asarray(med)
        if np.isnan(med).any() or (med <= 0).any():
            continue
        best = None
        for k in range(2, 11):  # at least two bins on each side; the bin holding the change may be mixed (skip it)
            for gap in (0, 1):
                if k + gap > 10:
                    continue
                left, right = med[:k], med[k + gap:]
                a_, b_ = np.median(left), np.median(right)
                r = b_ / a_
                dev = max(np.abs(np.log(left / a_)).max(), np.abs(np.log(right / b_)).max())
                if (r >= 3 or r <= 1 / 3) and dev <= np.log(2.0) and abs(np.log(r)) >= 2 * dev:
                    if best is None or abs(np.log(r)) > abs(np.log(best["factor"])) * 1.1:
                        best = {"k": k + gap, "factor": float(r)}
        if not best:
            continue
        k = best["k"]
        cut_row = bins[k][0]
        cut = t.iloc[cut_row]
        near = min(UNIT_FACTORS, key=lambda f: abs(np.log(best["factor"] / f)))
        known = abs(np.log(best["factor"] / near)) <= np.log(1.25)
        after = (t >= cut).to_numpy() & ok
        items.append({"column": c, "from": str(cut)[:10], "factor": round(best["factor"], 4),
                      "unit": UNIT_FACTORS[near] if known else None, "rows_after": int(after.sum()),
                      "median_before": round(float(np.median(med[:k])), 4), "median_after": round(float(np.median(med[k:])), 4),
                      "rescale": round(1 / near, 6) if known else round(1 / best["factor"], 6), "_mask": after})
    if not items:
        return []
    it = items[0]
    mask = it.pop("_mask")
    for i in items[1:]:
        i.pop("_mask", None)
    cols = [i["column"] for i in items]
    desc = "; ".join(f"{i['column']} × {i['factor']:.3g} from {i['from']}" + (f" ({i['unit']})" if i["unit"] else "") for i in items[:3])
    return [_finding("unit_change", "medium", f"The scale changes over time: {desc}",
                     {"columns": len(items), "rows after the change": it["rows_after"], "factor": it["factor"],
                      f"median {it['column']} before": it["median_before"], f"median {it['column']} after": it["median_after"]},
                     f"Rows ordered by {date} and cut into 12 equal bins; the median of each number column's non-zero values per "
                     "bin. A unit change: the medians after one cut differ from those before by a factor ≥ 3 (or ≤ 1/3), every "
                     "bin on each side stays within a factor 2 of its side, and the jump is at least twice that spread (in logs); the "
                     "bin holding the change may be mixed. A model trained across the change mixes two units.",
                     _rows(cx.df, mask, [date] + cols[:4] + ([cx.target] if cx.target else [])),
                     _fix("transform", "Ask the data owner which unit each period uses; if the factor is a unit change, rescale the "
                                       "later rows back (the fix below does that and keeps a flag).",
                          [{"op": "rescale", "column": i["column"], "date_column": date, "from": i["from"], "factor": i["rescale"]}
                           for i in items], selected=False),
                     columns=cols) | {"items": items}]


def check_ids_constants(cx: Ctx) -> list[dict[str, Any]]:
    """Row IDs, constants and high-cardinality categories that are really IDs or free text."""
    items = []
    for c in cx.features():
        s, t, r = cx.df[c], cx.types[c], cx.role(c)
        k = int(s.nunique(dropna=True))
        if r == "id" or t == "id":
            items.append({"column": c, "why": f"a row ID ({k:,} distinct)"})
        elif _numeric_id(c, s, cx.n):
            items.append({"column": c, "why": f"an ID-like number ({k:,} distinct integers in {cx.n:,} rows, no order to learn)"})
        elif k <= 1:
            items.append({"column": c, "why": "constant"})
        elif t == "category" and s.dtype == object and k > max(50, 0.5 * cx.n) and r not in ("entity", "date", "surname", "zip"):
            items.append({"column": c, "why": f"{k:,} distinct values in {cx.n:,} rows: an ID or free text, not a category"})
    if not items:
        return []
    cols = [i["column"] for i in items]
    return [_finding("ids_constants", "low", f"Not features: {', '.join(f'{i['column']} ({i['why'].split(' (')[0]})' for i in items[:4])}",
                     {"columns": len(items)},
                     "A row ID (unique per row, or an ID-like name), an ID-like integer (unique per row and named like a reference — "
                     "_ref, _no, acct… — or consecutive numbers ≥ 1,000), a constant, or a text category with more distinct values "
                     "than half the rows. They can only memorise rows.",
                     _rows(cx.df, np.ones(cx.n, bool), cols[:5]),
                     _fix("transform", "Drop them from the dataset (runs already leave row IDs out).",
                          [{"op": "drop_columns", "columns": cols}]), columns=cols) | {"items": items}]


def check_protected(cx: Ctx) -> list[dict[str, Any]]:
    """Protected attributes (sex, age, race, marital status, …) and their proxies (Cramér's V ≥ 0.5 with one)."""
    prot = []
    for c in cx.features():
        s = cx.df[c]
        k = int(s.nunique(dropna=True))
        if cx.role(c) == "protected" or any(a["role"] == "protected" for a in cx.also(c)):
            prot.append({"column": c, "why": "Smart Intake: name reads like a protected attribute"})
        elif PROTECTED_EXTRA.search(str(c)) and 2 <= k <= 20:
            prot.append({"column": c, "why": f"name '{c}' reads like a protected attribute"})
        elif s.dtype == object and 2 <= k <= 20 and s.dropna().astype(str).str.contains(SEX_WORDS).mean() >= 0.5:
            prot.append({"column": c, "why": "its values name a sex (male / female)"})
        elif AGE_NAME.search(str(c)) and pd.api.types.is_numeric_dtype(s) and s.dropna().between(10, 120).mean() > 0.98:
            prot.append({"column": c, "why": "age (ECOA: usable only in an empirically derived, demonstrably sound score)"})
    if not prot:
        return []
    names = {p["column"] for p in prot}
    proxies = []
    for c in cx.model_features():
        if c in names or cx.types[c] in ("text", "id"):
            continue
        b = _binned(cx.df[c])
        for p in names:
            v = _cramers_v(b, _binned(cx.df[p]))
            if v is not None and v >= 0.5:
                proxies.append({"column": c, "of": p, "v": round(v, 3)})
    cols = [p["column"] for p in prot]
    nums = {"protected": len(prot), "proxies (V ≥ 0.5)": len(proxies)}
    for x in proxies[:3]:
        nums[f"V({x['column']}, {x['of']})"] = x["v"]
    return [_finding("protected", "info",
                     f"Protected attributes: {', '.join(cols)}" + (f"; proxies: {', '.join(x['column'] for x in proxies[:3])}" if proxies else ""),
                     nums,
                     "Names that read like sex, gender, race, ethnicity, religion, marital status, nationality, disability; values "
                     "that name a sex (German credit's personal_status); or a numeric age (10–120). A proxy is another column with Cramér's V ≥ 0.5 against one of them (numbers in deciles).",
                     _rows(cx.df, np.ones(cx.n, bool), cols + [x["column"] for x in proxies[:2]]),
                     _fix("question", "Use them for the fairness check only: ticked columns are left out of every run on this "
                                      "dataset and stay in the data for Explain → Fairness."),
                     columns=cols) | {"items": prot, "proxies": proxies}]


def check_coerced(cx: Ctx) -> list[dict[str, Any]]:
    """Columns read as numbers although some cells were not (A1 #5): blanks, 'n/a', '$12,000'. Recorded at upload."""
    items = [i for i in cx.coerced if i["column"] in cx.df.columns]
    if not items:
        return []
    bad = sum(i["blank"] + i["unparsed"] for i in items)
    desc = "; ".join(f"{i['column']}: {i['blank']:,} blank" + (f", {i['unparsed']:,} not numbers ({', '.join(map(repr, i['unparsed_values'][:3]))})"
                                                              if i["unparsed"] else "")
                     + (f", {i['reformatted']:,} reformatted (currency, thousands separators)" if i["reformatted"] else "")
                     for i in items[:3])
    share = max((i["blank"] + i["unparsed"]) / max(i["rows"], 1) for i in items)
    return [_finding("coerced_numbers", "medium" if share >= 0.05 else "low",
                     f"Read as numbers: {desc}",
                     {"columns": len(items), "cells made missing": bad, "largest share": round(share, 4)},
                     "A text column where ≥ 90 % of the non-blank cells are numbers ('12,000', '$12,000', '1e5') is read as a "
                     "number column; blank and non-numeric cells ('n/a', '?') become missing. As text it would be a category with "
                     "thousands of levels (or an ID) and the model could not use its order.",
                     None, _fix("advice", "Check what the blank and coded cells mean (not applicable, not yet billed, unknown): "
                                          "if they mean something, keep a 'was missing' flag (Informative missingness lists it)."),
                     columns=[i["column"] for i in items]) | {"items": items}]


CHECKS = (check_coerced, check_leakage, check_coded_values, check_disguised_missing, check_rare_codes, check_duplicates,
          check_repeated_entities,
          check_conflicts, check_class_balance, check_sample_size, check_time, check_unit_change, check_informative_missing,
          check_ids_constants, check_protected)


def report(df: pd.DataFrame, target: str | None, roles: list[dict[str, Any]], answers: dict[str, Any] | None = None,
           labels: dict[str, Any] | None = None, coerced: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    """Every check on the frame. `roles`: Smart Intake's columns; `answers`: the stored decision-time / fairness
    answers; `labels`: the label-quality job's state (its finding is built from it)."""
    t0 = time.perf_counter()
    cx = Ctx(df, target, roles)
    cx.coerced = coerced or []
    findings: list[dict[str, Any]] = []
    timings = {}
    for chk in CHECKS:
        t1 = time.perf_counter()
        try:
            findings += chk(cx)
        except Exception as e:  # noqa: BLE001 - one broken check must not hide the others
            log.exception("data doctor check %s failed", chk.__name__)
            findings.append(_finding(chk.__name__.removeprefix("check_"), "info", f"Check failed: {type(e).__name__}",
                                     {}, str(e)[:200], None, _fix("advice", "")))
        timings[chk.__name__.removeprefix("check_")] = round(time.perf_counter() - t1, 3)
    lf = label_finding(cx, labels)
    if lf:
        findings.append(lf)
    rank = {s: i for i, s in enumerate(SEVERITIES)}
    findings.sort(key=lambda f: (rank[f["severity"]], ORDER.index(f["id"]) if f["id"] in ORDER else 99))
    for i, f in enumerate(findings):
        f["key"] = f"{f['id']}"
    answers = answers or {}
    leak = next((f for f in findings if f["id"] == "leakage"), None)
    prot = next((f for f in findings if f["id"] == "protected"), None)
    suspects = leak["suspects"] if leak else []
    known = answers.get("known") or {}
    decision = {
        "question": "Is each of these columns known at the moment the decision is made?",
        "rule": "'Not known' columns are dropped from every run started on this dataset (and its fixed versions); 'known' "
                "columns are kept. Unanswered columns are kept too, and every run records them as unanswered.",
        "columns": [{"column": s["column"], "suspect": True, "reasons": s["reasons"], "auc": s["auc"], "basis": s.get("basis"),
                     "confidence": s["confidence"],
                     "known": (bool(known[s["column"]]) if known.get(s["column"]) is not None else None)} for s in suspects]
                   + [{"column": c, "suspect": False, "reasons": [], "auc": None, "confidence": 0, "known": False}
                      for c, v in known.items() if v is False and c not in {s["column"] for s in suspects}],
        "unanswered": [s["column"] for s in suspects if known.get(s["column"]) is None],
        "answered": bool(answers.get("answered_at")),
        "answered_at": answers.get("answered_at"), "by": answers.get("by"),
        "fairness_only": answers.get("fairness_only") if answers.get("answered_at") else
                         [p["column"] for p in (prot or {}).get("items", [])],
        "protected": [p["column"] for p in (prot or {}).get("items", [])],
    }
    if leak and decision["answered"] and all(known.get(s["column"]) is not None for s in suspects):
        dropped = [s["column"] for s in suspects if known.get(s["column"]) is False]
        leak["severity"], leak["answered"] = "info", True
        leak["title"] = (f"Leakage suspects answered: {', '.join(dropped)} dropped from runs" if dropped
                         else "Leakage suspects answered: all known at decision time, kept")
        findings.sort(key=lambda f: (rank[f["severity"]], ORDER.index(f["id"]) if f["id"] in ORDER else 99))
    selected = [t for f in findings if f["fix"]["selected"] for t in f["fix"]["transforms"]]
    counts = {s: sum(1 for f in findings if f["severity"] == s) for s in SEVERITIES}
    return {"target": target, "task": cx.task, "rows": cx.n, "columns": int(df.shape[1]), "findings": findings,
            "counts": counts, "selected_fixes": selected,
            "n_fixes": sum(1 for f in findings if f["fix"]["selected"]),
            "decision_time": decision, "seconds": round(time.perf_counter() - t0, 3), "timings": timings,
            "severity_rule": "high: the model or its score will be wrong unless fixed; medium: it will be worse or "
                             "misleading; low: worth a look; info: context (fairness, coverage)."}


# ---------------------------------------------------------------------------------------------- label quality (bg)

LABEL_BUDGET_S = 60
_label_jobs: dict[str, threading.Thread] = {}
_label_lock = threading.Lock()


def label_key(target: str, csv: Path) -> str:
    st = csv.stat()
    return f"{target}|{st.st_size}|{int(st.st_mtime)}"


def label_state(dirpath: Path, target: str | None, csv: Path) -> dict[str, Any] | None:
    if not target:
        return None
    p = dirpath / "data_doctor_labels.json"
    if p.exists():
        s = json.loads(p.read_text())
        if s.get("key") == label_key(target, csv):
            return s
    key = f"{dirpath}|{target}"
    with _label_lock:
        t = _label_jobs.get(key)
        if t is not None and t.is_alive():
            return {"status": "running"}
    return None


def start_labels(dirpath: Path, target: str, csv: Path, roles: list[dict[str, Any]]) -> dict[str, Any]:
    """Start the quick out-of-fold model + cleanlab in a thread (once per dataset and target)."""
    key = f"{dirpath}|{target}"
    with _label_lock:
        t = _label_jobs.get(key)
        if t is not None and t.is_alive():
            return {"status": "running"}

        def run():
            k = label_key(target, csv)
            t0 = time.perf_counter()
            try:
                out = label_issues(pd.read_csv(csv), target, roles, all_rows=True)
            except Exception as e:  # noqa: BLE001
                log.exception("label quality failed")
                out = {"status": "failed", "error": f"{type(e).__name__}: {e}"[:300]}
            out.update(key=k, seconds=round(time.perf_counter() - t0, 1))
            (dirpath / "data_doctor_labels.json").write_text(json.dumps(out, default=_jsonable))

        th = threading.Thread(target=run, name=f"data-doctor-labels-{dirpath.name}", daemon=True)
        _label_jobs[key] = th
        th.start()
    return {"status": "running"}


def label_issues(df: pd.DataFrame, target: str, roles: list[dict[str, Any]], seed: int = 0, all_rows: bool = False) -> dict[str, Any]:
    """A quick out-of-fold model (3 folds) and cleanlab's find_label_issues on its p̂. Tabular: histogram gradient
    boosting on numbers and category codes. With text: TF-IDF + logistic regression. At most 50,000 rows."""
    from cleanlab.filter import find_label_issues
    from sklearn.model_selection import StratifiedKFold, cross_val_predict

    deadline = time.perf_counter() + LABEL_BUDGET_S
    cols = {c["name"]: c for c in roles}
    drop = {c for c, r in cols.items() if r["role"] in ("id", "constant", "leak", "date", "entity", "surname", "zip")
            or any(a["role"] == "leak" and a["confidence"] >= 0.85 for a in r.get("also", []))}
    y_raw = df[target]
    keep = y_raw.notna().to_numpy()
    if keep.sum() > 50_000:
        rng = np.random.default_rng(seed)
        keep &= np.isin(np.arange(len(df)), rng.choice(np.flatnonzero(keep), 50_000, replace=False))
    rows = np.flatnonzero(keep)
    y_s = y_raw[keep].astype(str)
    vc = y_s.value_counts()
    if len(vc) < 2 or vc.min() < 3 or (pd.api.types.is_numeric_dtype(y_raw) and y_raw.nunique() > MAX_CLASSES):
        return {"status": "n/a", "reason": "needs a classification target with ≥ 3 rows per class"}
    classes = sorted(vc.index)
    yi = y_s.map({c: i for i, c in enumerate(classes)}).to_numpy()
    X = df.loc[keep, [c for c in df.columns if c != target and c not in drop]]
    text = [c for c in X.columns if (cols.get(c) or {}).get("type") == "text"]
    folds = StratifiedKFold(n_splits=3, shuffle=True, random_state=seed)
    if text:
        from scipy.sparse import hstack
        from sklearn.feature_extraction.text import TfidfVectorizer
        from sklearn.linear_model import LogisticRegression
        from sklearn.pipeline import make_pipeline
        from sklearn.preprocessing import FunctionTransformer

        doc = X[text].fillna("").astype(str).agg(" ".join, axis=1)
        model = make_pipeline(TfidfVectorizer(ngram_range=(1, 2), min_df=2, max_features=50_000, sublinear_tf=True),
                              LogisticRegression(max_iter=300, C=5.0))
        P = cross_val_predict(model, doc, yi, cv=folds, method="predict_proba")
        name = "TF-IDF (1–2 grams) + logistic regression, 3-fold out-of-fold"
        _ = (hstack, FunctionTransformer)
    else:
        from sklearn.ensemble import HistGradientBoostingClassifier

        Xn = pd.DataFrame(index=X.index)
        cat_mask = []
        for c in X.columns:
            s = X[c]
            if pd.api.types.is_numeric_dtype(s) and not pd.api.types.is_bool_dtype(s):
                Xn[c] = s.astype(float)
                cat_mask.append(False)
            else:
                top = s.astype(str).value_counts().index[:250]
                Xn[c] = pd.Categorical(s.astype(str).where(s.astype(str).isin(top), "__other__")).codes.astype(float)
                cat_mask.append(True)
        model = HistGradientBoostingClassifier(max_iter=150, learning_rate=0.08, categorical_features=np.array(cat_mask) if any(cat_mask) else None,
                                               early_stopping=True, random_state=seed)
        P = cross_val_predict(model, Xn.to_numpy(), yi, cv=folds, method="predict_proba")
        name = "histogram gradient boosting (150 rounds, early stopping), 3-fold out-of-fold"
    acc = float((P.argmax(1) == yi).mean())
    iss = find_label_issues(yi, P, filter_by="prune_by_noise_rate", return_indices_ranked_by="self_confidence", n_jobs=1)
    # Error floor: the most confident fifth of predictions; noise shows as confident disagreement, overlap does not.
    conf = P.max(1)
    q = np.quantile(conf, 0.8)
    top = conf >= q
    floor = float((P.argmax(1)[top] != yi[top]).mean())
    expected = float((1 - conf[top]).mean())
    examples = [{"row": int(rows[i]), "given": classes[yi[i]], "suggested": classes[int(P[i].argmax())],
                 "p_given": round(float(P[i, yi[i]]), 3), "p_suggested": round(float(P[i].max()), 3)} for i in iss[:200]]
    majority = float(np.bincount(yi).max() / len(yi))
    return {"status": "done", "model": name, "rows": int(len(yi)), "accuracy_oof": round(acc, 4),
            "majority": round(majority, 4), "error_all": round(1 - acc, 4),
            "n": int(len(iss)), "rate": round(len(iss) / len(yi), 4), "floor": round(floor, 4),
            "expected_floor": round(expected, 4), "examples": examples, "classes": classes,
            "over_budget": time.perf_counter() > deadline,
            "method": "cleanlab.filter.find_label_issues(filter_by='prune_by_noise_rate') on out-of-fold p̂",
            **({"issue_rows": [int(rows[i]) for i in iss]} if all_rows else {})}


def label_finding(cx: Ctx, st: dict[str, Any] | None) -> dict[str, Any] | None:
    rule = ("A quick model (≤ 60 s, out-of-fold) predicts every row; cleanlab flags labels it disagrees with confidently. "
            "Medium when the model beats 'always the largest class' by ≥ 5 points, cleanlab flags ≥ 5 %, and the most "
            "confident fifth of predictions is wrong ≥ 3 points more than the model expects and at ≥ 0.6 × its overall "
            "error rate (flipped labels hit confident rows too; overlap does not); low when it flags ≥ 2 %, or ≥ 1 % with that gap ≥ 1 point; else info. A tabular overlap is often the table lacking a column, "
            "not a wrong label. Flags that concentrate in one category level (≥ 2× the rest, ≥ 30 rows) point at a labelling problem there.")
    if cx.task not in ("binary", "multiclass"):
        return None
    if not st or st.get("status") == "running":
        return _finding("label_quality", "info", "Label quality: a quick model is checking every label…",
                        {"budget (s)": LABEL_BUDGET_S}, rule, None, _fix("advice", "Results appear here when it finishes."),
                        columns=[cx.target]) | {"status": "running"}
    if st.get("status") != "done":
        return _finding("label_quality", "info", f"Label quality not checked: {st.get('reason') or st.get('error') or st.get('status')}",
                        {}, rule, None, _fix("advice", ""), columns=[cx.target]) | {"status": st.get("status")}
    gap = st["floor"] - st["expected_floor"]
    signal = st["accuracy_oof"] - st.get("majority", 0) >= 0.05  # the quick model must beat 'always the largest class'
    ratio = st["floor"] / max(st.get("error_all", 1), 1e-9)       # flip noise hits confident rows as often as the rest
    noisy = signal and st["rate"] >= 0.05 and gap >= 0.03 and ratio >= 0.6
    looks_wrong = signal and gap >= 0.01
    sev = "medium" if noisy else ("low" if st["rate"] >= 0.02 or (st["rate"] >= 0.01 and looks_wrong) else "info")
    ex = st["examples"][:5]
    feats = [c for c in cx.model_features()][:4]
    ev = {"columns": ["row", "given", "suggested", "p(suggested)", *feats],
          "rows": [[e["row"], e["given"], e["suggested"], e["p_suggested"],
                    *[_jsonable(cx.df.iat[e["row"], cx.df.columns.get_loc(c)]) for c in feats]] for e in ex]}
    rows = [e["row"] for e in st["examples"]]
    # where the flags concentrate: a category level holding ≥ 2× its share of the flags (≥ 30 flagged) points at a
    # labelling process there, not at classes that overlap everywhere
    conc = None
    iss = st.get("issue_rows")
    if iss and len(iss) >= 30:
        fl = np.zeros(cx.n, bool)
        fl[[i for i in iss if i < cx.n]] = True
        best = None
        for c in cx.model_features():
            x = cx.df[c]
            if pd.api.types.is_numeric_dtype(x) or not 2 <= x.nunique() <= 20:
                continue
            for lv, grp in pd.Series(fl).groupby(x.astype(str).to_numpy()):
                if grp.sum() >= 30 and len(grp) < cx.n:
                    r_in, r_out = float(grp.mean()), float(fl[(x.astype(str) != lv).to_numpy()].mean())
                    if r_in >= 2 * max(r_out, 1e-9) and (best is None or r_in / max(r_out, 1e-9) > best["lift"]):
                        best = {"column": c, "value": lv, "rate_inside": r_in, "rate_outside": r_out,
                                "flagged": int(grp.sum()), "lift": r_in / max(r_out, 1e-9)}
        conc = best
    if conc and sev == "info":
        sev = "low"
    where = (f"; they concentrate where {conc['column']} = {conc['value']} ({conc['rate_inside']:.1%} flagged vs "
             f"{conc['rate_outside']:.1%} elsewhere), which looks like a labelling problem there" if conc else "")
    what = (" that look wrong" if looks_wrong else
            ": wrong labels or classes that overlap (this check cannot tell which; reviewing a sample can)")
    return _finding("label_quality", sev,
                    f"cleanlab flags {st['n']:,} labels ({st['rate']:.1%})" + what + where,
                    {"flagged": st["n"], "rate": st["rate"], "quick model accuracy (out-of-fold)": st["accuracy_oof"],
                     "largest class share": st.get("majority"),
                     "error in the most confident fifth": st["floor"], "error it expects there": st["expected_floor"],
                     "seconds": st.get("seconds")},
                    rule + f" Model: {st['model']}.", ev,
                    _fix("transform", "Review them in Models → Doctor → Labels after the first run; or drop the top flagged rows.",
                         [{"op": "drop_rows", "rows": rows, "reason": "cleanlab label issues"}], selected=False),
                    columns=[cx.target]) | {"status": "done", "concentration": conc}


# ---------------------------------------------------------------------------------------------------- the transforms

TRANSFORMS = {
    "to_missing": "values → missing, with a <column>_was_missing flag when flag is true",
    "split_codes": "codes → fill in the column, and a new <column>_code column holding the code (or 'value')",
    "add_missing_flag": "a new <column>_was_missing 0/1 column",
    "drop_duplicates": "drop exact duplicate rows (row keys ignored), keep the first",
    "drop_rows": "drop the listed row numbers",
    "drop_columns": "drop the listed columns",
    "merge_classes": "the listed classes of the column → one class",
    "rescale": "multiply the column by factor on rows whose date_column is on or after from, with a <column>_rescaled flag",
}


def _match(s: pd.Series, values: list[Any]) -> pd.Series:
    if pd.api.types.is_numeric_dtype(s):
        nums = [float(v) for v in values if not isinstance(v, str) or re.fullmatch(r"-?\d+(\.\d+)?", v)]
        return s.isin(nums)
    return s.astype(str).isin([str(v) for v in values]) & s.notna()


def validate(df: pd.DataFrame, transforms: list[dict[str, Any]], target: str | None) -> None:
    if not isinstance(transforms, list) or not transforms:
        raise ValueError("Send {\"transforms\": [...]} with at least one fix.")
    for t in transforms:
        op = (t or {}).get("op")
        if op not in TRANSFORMS:
            raise ValueError(f"Unknown fix {op!r}; use one of {', '.join(TRANSFORMS)}.")
        for c in ([t["column"]] if "column" in t else []) + list(t.get("columns") or []):
            if c not in df.columns:
                raise ValueError(f"{op}: no column named {c!r}.")
        if op == "rescale":
            try:
                float(t.get("factor"))
            except (TypeError, ValueError):
                raise ValueError("rescale needs a numeric factor.") from None
            if t.get("date_column") not in df.columns or not t.get("from") or "column" not in t:
                raise ValueError("rescale needs column, date_column (a column of the table) and from (a date).")
        if op in ("to_missing", "split_codes", "add_missing_flag", "merge_classes") and "column" not in t:
            raise ValueError(f"{op} needs a column.")
        if op in ("to_missing", "split_codes", "drop_columns", "merge_classes") and target and \
                (t.get("column") == target or target in (t.get("columns") or [])) and op != "merge_classes":
            raise ValueError(f"{op} can't change the target column '{target}'.")


def apply(df: pd.DataFrame, transforms: list[dict[str, Any]], target: str | None) -> tuple[pd.DataFrame, np.ndarray, list[dict[str, Any]]]:
    """Apply the fixes in order. Returns the new frame, the mask of the original rows kept, and each fix's effect."""
    validate(df, transforms, target)
    out = df.copy()
    keep = np.ones(len(df), bool)
    effects = []
    for t in transforms:
        op = t["op"]
        cur = out[keep]
        if op == "to_missing":
            c = t["column"]
            m = _match(out[c], t.get("values") or []) & pd.Series(keep, index=out.index)
            if t.get("flag", True):
                flag = f"{c}_was_missing"
                out[flag] = (out[c].isna() | m).astype(int)
            out.loc[m, c] = np.nan
            effects.append({**t, "cells": int(m.sum())})
        elif op == "split_codes":
            c, codes = t["column"], t.get("codes") or []
            m = _match(out[c], codes)
            out[f"{c}_code"] = np.where(m, out[c].astype(str).str.replace(r"\.0$", "", regex=True), "value")
            out.loc[m, c] = t.get("fill", 0)
            effects.append({**t, "cells": int((m & keep).sum()), "added": f"{c}_code"})
        elif op == "add_missing_flag":
            c = t["column"]
            out[f"{c}_was_missing"] = out[c].isna().astype(int)
            effects.append({**t, "cells": int(out.loc[keep, c].isna().sum()), "added": f"{c}_was_missing"})
        elif op == "drop_duplicates":
            ids = set(_row_keys(out))
            content = [c for c in out.columns if c not in ids] or list(out.columns)
            d = out[keep].duplicated(subset=content, keep="first")
            gone = np.flatnonzero(keep)[d.to_numpy()]
            keep[gone] = False
            effects.append({**t, "rows": int(len(gone))})
        elif op == "drop_rows":
            rows = [int(r) for r in t.get("rows") or [] if 0 <= int(r) < len(df)]
            before = int(keep.sum())
            keep[rows] = False
            effects.append({"op": op, "reason": t.get("reason"), "rows": before - int(keep.sum())})
        elif op == "drop_columns":
            cols = [c for c in t.get("columns") or [] if c != target]
            out = out.drop(columns=cols)
            effects.append({**t, "columns": cols})
        elif op == "rescale":
            from .splits import time_values

            c = t["column"]
            tv = time_values(out[t["date_column"]])
            m = (tv >= pd.Timestamp(t["from"]) if pd.api.types.is_datetime64_any_dtype(tv) else tv >= float(t["from"])).fillna(False).to_numpy()
            out[c] = out[c].astype(float)
            out.loc[m, c] = out.loc[m, c] * float(t["factor"])
            out[f"{c}_rescaled"] = m.astype(int)
            effects.append({**t, "cells": int((m & keep).sum())})
        elif op == "merge_classes":
            c, into = t["column"], str(t.get("into") or "other")
            m = out[c].astype(str).isin([str(v) for v in t.get("classes") or []])
            out[c] = out[c].astype(object)
            out.loc[m, c] = into
            effects.append({**t, "cells": int((m & keep).sum())})
        _ = cur
    return out[keep].reset_index(drop=True), keep, effects
