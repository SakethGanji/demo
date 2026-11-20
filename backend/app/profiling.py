"""Profile a tabular dataset: column types (with text detection), missing values, duplicates,
class balance and a suggested label column."""

from __future__ import annotations

import re
from typing import Any

import numpy as np
import pandas as pd

ColumnType = str  # 'text' | 'category' | 'number' | 'id'

LABEL_NAME = re.compile(r"(^|_)(label|target|class|category|categor\w*|sentiment|churn\w*|outcome|y|fraud|default\w*)$|^is_\w+|^has_\w+", re.I)
ID_NAME = re.compile(r"(^id$|_id$|^id_|uuid|^key$)", re.I)
# a number named like a reference (acct_ref, loan_no, account_number): an ID when it is (almost) unique per row
REF_NAME = re.compile(r"(_ref$|^ref_|_no$|_nbr$|_num$|_number$|^acct|account_?(no|num|ref|id)|serial|reference)", re.I)
MAX_CLASSES = 50  # a label column has at most this many distinct values


NUM_TEXT = re.compile(r"^[-+]?[$€£]?\s*[-+]?(\d{1,3}(,\d{3})+|\d+)?(\.\d+)?([eE][-+]?\d+)?\s*%?$")


def _parse_number(v: str) -> float | None:
    t = v.strip()
    if not t or not NUM_TEXT.match(t) or not re.search(r"\d", t):
        return None
    t = re.sub(r"[$€£,%\s]", "", t)
    try:
        return float(t)
    except ValueError:
        return None


def coerce_numeric(df: pd.DataFrame, min_share: float = 0.9) -> tuple[pd.DataFrame, list[dict[str, Any]]]:
    """Text columns that are mostly numbers (≥ 90 % of the non-blank cells parse: '12,000', '$12,000', '1e5', ' 42 ')
    become numbers; blanks and cells that do not parse ('n/a', '?', 'unknown') become missing. Codes with leading zeros
    ('007', zip codes) are left as text. Returns the frame and one record per converted column, so the Data Doctor can
    list what was blank or coded."""
    out = df.copy()
    info: list[dict[str, Any]] = []
    for c in df.columns:
        s = df[c]
        if s.dtype != object:
            continue
        txt = s.dropna().astype(str)
        nonblank = txt[txt.str.strip() != ""]
        if len(nonblank) < 10:
            continue
        if nonblank.str.match(r"^\s*0\d").mean() > 0.05:  # codes like 007 or 02134
            continue
        parsed = nonblank.map(_parse_number)
        ok = parsed.notna()
        if ok.mean() < min_share:
            continue
        vals = s.astype(str).map(lambda v: _parse_number(v) if v is not None else None)
        vals = pd.to_numeric(vals.where(s.notna()), errors="coerce")
        blank = int((txt.str.strip() == "").sum())
        bad = nonblank[~ok]
        reformatted = int((ok & ~nonblank.str.strip().str.match(r"^[-+]?(\d+)?(\.\d+)?([eE][-+]?\d+)?$")).sum())
        out[c] = vals
        info.append({"column": c, "blank": blank, "unparsed": int(len(bad)), "reformatted": reformatted,
                     "unparsed_values": sorted(bad.str.strip().value_counts().index[:6].tolist()),
                     "share_numeric": round(float(ok.mean()), 4), "rows": int(len(s))})
    return out, info


def _example(series: pd.Series) -> str:
    vals = series.dropna()
    return "" if vals.empty else str(vals.iloc[0])[:80]


def detect_type(name: str, s: pd.Series) -> ColumnType:
    non_null = s.dropna()
    n = len(non_null)
    if n == 0:
        return "category"
    unique = non_null.nunique()
    unique_ratio = unique / n
    if pd.api.types.is_bool_dtype(s):
        return "category"
    if pd.api.types.is_numeric_dtype(s):
        is_int = pd.api.types.is_integer_dtype(s) or bool(np.all(np.mod(non_null, 1) == 0))
        if (ID_NAME.search(name) or REF_NAME.search(name)) and is_int and unique_ratio > 0.95:
            return "id"
        return "number"
    as_str = non_null.astype(str)
    lengths = as_str.str.len()
    words = as_str.str.count(r"\s+") + 1
    if ID_NAME.search(name) and unique_ratio > 0.9:
        return "id"
    # Free text: long strings with several words that mostly don't repeat exactly.
    if (lengths.mean() >= 40 or words.mean() >= 6) and unique_ratio > 0.2:
        return "text"
    # Short strings with several words and many distinct values are text too (subject lines, titles).
    if words.mean() >= 2.5 and unique > 50 and unique_ratio > 0.05 and not LABEL_NAME.search(name):
        return "text"
    if unique_ratio > 0.9 and n > 20 and words.mean() < 2:
        return "id"
    return "category"


def suggest_label(df: pd.DataFrame, types: dict[str, ColumnType]) -> tuple[str | None, list[str]]:
    """Pick the column most likely to be the label. Candidates: category-like columns with 2..50 values."""
    candidates: list[tuple[float, int, str]] = []
    for i, col in enumerate(df.columns):
        t = types[col]
        if t in ("text", "id"):
            continue
        k = df[col].nunique(dropna=True)
        if k < 2 or k > MAX_CLASSES:
            continue
        if t == "number" and k > 10:
            continue  # a measurement, not a label
        score = 0.0
        if LABEL_NAME.search(str(col)):
            score += 10
        if t == "category":
            score += 2
        score += i / max(1, len(df.columns))  # labels are usually towards the right
        if df[col].isna().any():
            score -= 3
        candidates.append((score, i, col))
    candidates.sort(reverse=True)
    names = [c for _, _, c in candidates]
    return (names[0] if names else None), names


def _numeric_stats(s: pd.Series) -> dict[str, float] | None:
    vals = s.dropna()
    if vals.empty:
        return None
    return {k: round(float(v), 6) for k, v in
            {"min": vals.min(), "max": vals.max(), "mean": vals.mean(), "std": vals.std(ddof=0), "median": vals.median()}.items()}


def class_balance(s: pd.Series) -> list[dict[str, Any]]:
    counts = s.dropna().astype(str).value_counts()
    total = int(counts.sum()) or 1
    return [{"name": str(k), "count": int(v), "share": round(int(v) / total, 6)} for k, v in counts.items()]


def profile_frame(df: pd.DataFrame, target: str | None = None) -> dict[str, Any]:
    n_rows, n_cols = df.shape
    types = {col: detect_type(str(col), df[col]) for col in df.columns}
    suggested, candidates = suggest_label(df, types)
    if target is not None and target not in df.columns:
        raise ValueError(f"Column '{target}' is not in the file.")
    target = target or suggested

    columns = []
    for col in df.columns:
        s = df[col]
        missing = int(s.isna().sum())
        t = types[col]
        role = "target" if col == target else ("ignored" if t == "id" else "feature")
        info: dict[str, Any] = {
            "name": str(col), "type": t, "dtype": str(s.dtype), "role": role,
            "missing": missing, "missing_share": round(missing / n_rows, 6) if n_rows else 0.0,
            "unique": int(s.nunique(dropna=True)), "example": _example(s),
        }
        if t == "number":
            info["stats"] = _numeric_stats(s)
        elif t == "text":
            lens = s.dropna().astype(str).str.len()
            info["stats"] = {"mean_chars": round(float(lens.mean()), 2) if len(lens) else 0.0,
                             "max_chars": int(lens.max()) if len(lens) else 0}
        elif t == "category":
            top = s.dropna().astype(str).value_counts().head(5)
            info["top"] = [{"value": str(k), "count": int(v)} for k, v in top.items()]
        columns.append(info)

    # Exact duplicates ignore ID columns: a resubmitted row with a new ID is still a duplicate.
    content_cols = [c for c in df.columns if types[c] != "id"] or list(df.columns)
    dup_rows = int(df.duplicated(subset=content_cols).sum()) if n_rows else 0
    text_cols = [c for c in df.columns if types[c] == "text"]
    dup_text = {str(c): int(df[c].dropna().duplicated().sum()) for c in text_cols}
    conflicting = 0
    if target and content_cols:
        feats = [c for c in content_cols if c != target]
        if feats:
            g = df.groupby(feats, dropna=False)[target].nunique()
            conflicting = int((g > 1).sum())

    # A numeric label with many distinct values is a regression target: no classes, just its spread.
    task = None
    target_stats = None
    if target:
        y = df[target]
        k = int(y.nunique(dropna=True))
        if types[target] == "number" and k > MAX_CLASSES:
            task, target_stats = "regression", _numeric_stats(y)
        else:
            task = "binary" if k == 2 else ("multiclass" if k > 2 else None)
    classes = class_balance(df[target]) if target and task != "regression" else []
    counts = [c["count"] for c in classes]
    warnings: list[str] = []
    if target is None:
        warnings.append("No column looks like a label; choose one before training.")
    if classes and min(counts) / max(counts) < 0.2:
        rare = classes[-1]
        warnings.append(f"Class '{rare['name']}' is rare ({rare['share'] * 100:.1f}% of rows).")
    if dup_rows:
        warnings.append(f"{dup_rows} rows are exact duplicates of another row.")
    if target and df[target].isna().any():
        warnings.append(f"{int(df[target].isna().sum())} rows have no label and will be dropped.")

    missing_cells = int(df.isna().sum().sum())
    return {
        "rows": n_rows,
        "n_cols": n_cols,
        "kind": "text" if text_cols else "tabular",
        "target": target,
        "suggested_target": suggested,
        "label_candidates": candidates,
        "columns": columns,
        "classes": classes,
        "n_classes": len(classes),
        "task": task,
        "target_stats": target_stats,
        "imbalance_ratio": round(max(counts) / min(counts), 4) if counts else None,
        "missing": {
            "cells": missing_cells,
            "share": round(missing_cells / (n_rows * n_cols), 6) if n_rows and n_cols else 0.0,
            "rows_with_missing": int(df.isna().any(axis=1).sum()),
            "columns_with_missing": [str(c) for c in df.columns if df[c].isna().any()],
        },
        "duplicates": {
            "rows": dup_rows,
            "share": round(dup_rows / n_rows, 6) if n_rows else 0.0,
            "text": dup_text,
            "conflicting_labels": conflicting,
            "ignoring": [str(c) for c in df.columns if types[c] == "id"],
        },
        "warnings": warnings,
    }
