"""The data audit behind the Ground-Truth Inbox, and the measurements the requirements are checked against.

Runs in the worker pool (`HeavyPool`). Inputs are plain data: the dataset CSV, the label, and the baseline's
out-of-sample class probabilities p̂(x) for every row: out-of-fold (5-fold bagging) for training rows, the fitted
model's own prediction for holdout rows (never trained on). Three kinds of issue:

- label    cleanlab `find_label_issues` (confident learning) on p̂, ranked by the normalised margin
           m(x) = p̂_y(x) − max_{k≠y} p̂_k(x)   (y the given label; m < 0 means the model prefers another class).
           Suggestion: relabel to argmax_k p̂_k(x), confidence p̂ of that class.
- conflict near-duplicates (cosine ≥ 0.97 between row embeddings) whose labels disagree. Suggestion: relabel the row
           the model disagrees with.
- duplicate exact copies of another row's features with the same label (a resubmitted ticket). Suggestion: drop the
           later copy; a copy on both sides of the holdout split inflates the holdout score.
- outlier  cleanlab `OutOfDistribution` on the embeddings (mean k-NN distance), the lowest 0.3 %. Suggestion: skip
           unless the label is also in doubt.

Embeddings: TF-IDF (1–2-grams) → 64-d truncated SVD of the text columns, plus standardised numbers and one-hot (F-X5a: at most
30 levels per column, ID-like columns left out) categories; L2-normalised.
"""

from __future__ import annotations

import hashlib
import time
from typing import Any

import numpy as np
import pandas as pd

from .. import frames

#: F-X5a: most frequent values one-hot encoded per category column in the embedding (the rest share one "other" level)
CAT_LEVELS = 30
#: F-X5a: sklearn working memory (MB) for the audit's pairwise-distance chunks
AUDIT_WORKING_MB = 128


def _embed(df: pd.DataFrame, text_cols: list[str], num_cols: list[str], cat_cols: list[str]) -> np.ndarray:
    from sklearn.decomposition import TruncatedSVD
    from sklearn.feature_extraction.text import TfidfVectorizer

    parts = []
    if text_cols:
        txt = df[text_cols].fillna("").astype(str).agg(" . ".join, axis=1)
        X = TfidfVectorizer(min_df=2, ngram_range=(1, 2), sublinear_tf=True, max_features=50_000).fit_transform(txt)
        k = max(2, min(64, X.shape[1] - 1))
        parts.append(TruncatedSVD(k, random_state=0).fit_transform(X))
    if num_cols:
        N = df[num_cols].apply(pd.to_numeric, errors="coerce")
        N = (N - N.mean()) / N.std(ddof=0).replace(0, 1)
        parts.append(N.fillna(0).to_numpy() * (0.5 if text_cols else 1.0))
    if cat_cols:
        # F-X5a (X5 J3): one-hot of every category column made a dense n × (distinct values) matrix: on credit-default
        # (12k rows) applicant_id, customer_id and application_date alone gave ≈ 21k columns (2 GB), copied several times
        # by the scaling, the k-NN and cleanlab's OutOfDistribution, so the audit was OOM-killed at ≥ 10 GB. ID-like
        # columns (distinct values > half the rows) carry no similarity and are left out; the others keep their
        # CAT_LEVELS most frequent values, the rest one "other" level. Width ≤ CAT_LEVELS + 1 per column.
        cats = {}
        for c in cat_cols:
            v = df[c].astype(str)
            vc = v.value_counts()
            if len(vc) > max(CAT_LEVELS, 0.5 * len(v)):
                continue
            top = set(vc.index[:CAT_LEVELS])
            cats[c] = v.where(v.isin(top), "\x00other")
        if cats:
            parts.append(pd.get_dummies(pd.DataFrame(cats), dtype=np.float32).to_numpy() * (0.3 if text_cols else 0.7))
    E = np.hstack(parts).astype(np.float64) if parts else np.zeros((len(df), 1))
    return E / (np.linalg.norm(E, axis=1, keepdims=True) + 1e-12)


def _text_of(row: pd.Series, text_cols: list[str], other: list[str]) -> dict[str, Any]:
    out = {"text": " — ".join(str(row[c]) for c in text_cols if pd.notna(row[c])) if text_cols else None}
    out["features"] = {c: (None if pd.isna(row[c]) else (row[c].item() if hasattr(row[c], "item") else row[c])) for c in other[:8]}
    return out


def audit(csv_path: str, label: str, feature_cols: list[str], text_cols: list[str], num_cols: list[str],
          cat_cols: list[str], classes: list[str], rows: list[int], proba: list[list[float]], holdout_rows: list[int],
          max_label_items: int = 400) -> dict[str, Any]:
    """F-X5a (X5 J3): sklearn's pairwise distances (the cosine k-NN, cleanlab's OutOfDistribution) run in chunks of
    AUDIT_WORKING_MB (sklearn's default is 1 GB per chunk: 2.5 GB on 12k rows); the same neighbours, bounded memory."""
    import sklearn

    with sklearn.config_context(working_memory=AUDIT_WORKING_MB):
        return _audit(csv_path, label, feature_cols, text_cols, num_cols, cat_cols, classes, rows, proba, holdout_rows,
                      max_label_items)


def _audit(csv_path: str, label: str, feature_cols: list[str], text_cols: list[str], num_cols: list[str],
           cat_cols: list[str], classes: list[str], rows: list[int], proba: list[list[float]], holdout_rows: list[int],
           max_label_items: int = 400) -> dict[str, Any]:
    from cleanlab.filter import find_label_issues
    from cleanlab.outlier import OutOfDistribution
    from cleanlab.rank import get_label_quality_scores
    from sklearn.neighbors import NearestNeighbors

    t0 = time.time()
    df = frames.read_csv_path(csv_path)  # typed: the schema beside the file (§6.5 R-D)
    y_all = df[label].astype(str).to_numpy()
    rows_a = np.asarray(rows, dtype=int)
    P = np.asarray(proba, dtype=float)
    cls = [str(c) for c in classes]
    cidx = {c: i for i, c in enumerate(cls)}
    keep = np.array([y_all[r] in cidx for r in rows_a])
    rows_a, P = rows_a[keep], P[keep]
    yi = np.array([cidx[y_all[r]] for r in rows_a])
    hold = set(int(r) for r in holdout_rows)

    # --- label issues (cleanlab confident learning, ranked by normalised margin)
    issues = find_label_issues(yi, P, filter_by="prune_by_noise_rate", return_indices_ranked_by="normalized_margin",
                               n_jobs=1)  # cleanlab's own process pool cannot start inside a spawn worker
    quality = get_label_quality_scores(yi, P, method="normalized_margin")
    p_given = P[np.arange(len(yi)), yi]
    P_other = P.copy()
    P_other[np.arange(len(yi)), yi] = -1
    k_other = P_other.argmax(1)
    p_other = P_other.max(1)
    margin = p_given - p_other
    n_issues = int(len(issues))
    other_cols = [c for c in feature_cols if c not in text_cols]

    items: list[dict[str, Any]] = []
    in_label = set()
    for i in issues[:max_label_items]:
        r = int(rows_a[i])
        in_label.add(r)
        items.append({
            "row": r, "kind": "label", "split": "holdout" if r in hold else "train", "given": cls[yi[i]],
            "suggested": cls[k_other[i]], "suggestion": "relabel", "confidence": round(float(p_other[i]), 4),
            "p_given": round(float(p_given[i]), 4), "margin": round(float(margin[i]), 4),
            "quality": round(float(quality[i]), 4),
            "why": (f"m(x) = p̂_y − max_{{k≠y}} p̂_k = {p_given[i]:.3f} − {p_other[i]:.3f} = {margin[i]:+.3f}: the "
                    f"{'held-out' if r in hold else 'out-of-fold'} model puts {p_other[i]:.0%} on {cls[k_other[i]]} and "
                    f"{p_given[i]:.1%} on the given {cls[yi[i]]}"),
            **_text_of(df.iloc[r], text_cols, other_cols),
        })

    # --- embeddings for duplicates, near-duplicates and outliers
    E = _embed(df, text_cols, num_cols, cat_cols)
    # exact duplicates of the features (ids and label excluded)
    key = df[feature_cols].astype(str).agg("\x1f".join, axis=1).map(lambda s: hashlib.blake2b(s.encode(), digest_size=10).hexdigest())
    first: dict[str, int] = {}
    n_dup = n_dup_conflict = 0
    for r, k in enumerate(key):
        if k in first:
            r0 = first[k]
            same = y_all[r] == y_all[r0]
            n_dup += 1
            if not same:
                n_dup_conflict += 1
            if r in in_label:
                continue
            cross = (r in hold) != (r0 in hold)
            items.append({
                "row": r, "kind": "duplicate" if same else "conflict", "split": "holdout" if r in hold else "train",
                "given": y_all[r], "suggested": y_all[r0] if not same else None, "suggestion": "drop" if same else "relabel",
                "confidence": 0.99 if same else 0.6, "of_row": r0, "similarity": 1.0,
                "why": (f"identical features to row {r0} with the same label{'; one copy is in the holdout and one in training, which inflates the holdout score' if cross else ''}"
                        if same else f"identical features to row {r0}, labelled {y_all[r0]} there and {y_all[r]} here"),
                **_text_of(df.iloc[r], text_cols, other_cols),
            })
        else:
            first[k] = r

    # near-duplicates with conflicting labels (not exact copies)
    nn = NearestNeighbors(n_neighbors=6, metric="cosine").fit(E)
    dist, ind = nn.kneighbors(E)
    n_near_conflict = 0
    pos = {int(r): i for i, r in enumerate(rows_a)}
    seen_pairs = set()
    for r in range(len(df)):
        for d, j in zip(dist[r, 1:], ind[r, 1:]):
            sim = 1 - d
            if sim < 0.97 or key.iat[r] == key.iat[j] or y_all[r] == y_all[j]:
                continue
            pair = (min(r, j), max(r, j))
            if pair in seen_pairs:
                continue
            seen_pairs.add(pair)
            n_near_conflict += 1
            # the row the model disagrees with more is the suspect
            pr, pj = pos.get(r), pos.get(int(j))
            sr = P[pr, yi[pr]] if pr is not None else 0.5
            sj = P[pj, yi[pj]] if pj is not None else 0.5
            sus, oth = (r, int(j)) if sr <= sj else (int(j), r)
            if sus in in_label:
                continue
            items.append({
                "row": sus, "kind": "conflict", "split": "holdout" if sus in hold else "train", "given": y_all[sus],
                "suggested": y_all[oth], "suggestion": "relabel", "confidence": round(float(max(sr, sj)), 4),
                "of_row": oth, "similarity": round(float(sim), 4),
                "why": f"near-duplicate of row {oth} (cosine {sim:.3f}) labelled {y_all[oth]}; the model gives the given label {min(sr, sj):.1%}",
                **_text_of(df.iloc[sus], text_cols, other_cols),
            })

    # outliers (cleanlab OutOfDistribution on the embeddings)
    ood = OutOfDistribution().fit_score(features=E)
    cut = np.quantile(ood, 0.003)
    n_out = 0
    for r in np.argsort(ood):
        if ood[r] > cut:
            break
        n_out += 1
        r = int(r)
        if r in in_label:
            continue
        items.append({
            "row": r, "kind": "outlier", "split": "holdout" if r in hold else "train", "given": y_all[r], "suggested": None,
            "suggestion": "skip", "confidence": round(float(1 - ood[r]), 4), "ood_score": round(float(ood[r]), 4),
            "why": f"out-of-distribution score {ood[r]:.3f} (mean k-NN distance), in the lowest 0.3 % (≤ {cut:.3f}) of the {len(df):,} rows",
            **_text_of(df.iloc[r], text_cols, other_cols),
        })

    for n, it in enumerate(items):
        it["id"] = f"i{n:04d}"
        it["answer"] = None
    return {
        "items": items, "seconds": round(time.time() - t0, 2),
        "stats": {"rows_scored": int(len(rows_a)), "label_issues": n_issues, "label_items": len(in_label),
                  "duplicates": n_dup, "duplicate_conflicts": n_dup_conflict, "near_conflicts": n_near_conflict,
                  "outliers": n_out, "ood_cut": round(float(cut), 4),
                  "method": "cleanlab.filter.find_label_issues(prune_by_noise_rate), ranked by normalized_margin; "
                            "cleanlab.outlier.OutOfDistribution on TF-IDF→SVD / one-hot embeddings; cosine k-NN for near-duplicates"},
    }


def measure_latency(predictor_path: str, csv_path: str, label: str, drop: list[str], rows: list[int], n: int = 40) -> dict[str, Any]:
    """Online latency: one row per predict call (the served model), n calls after 3 warm-ups; ms per row."""
    from ..engine.pool import load_predictor

    p = load_predictor(predictor_path)
    df = frames.read_csv_path(csv_path).iloc[rows[: n + 3]].drop(columns=[label, *drop], errors="ignore")
    ts = []
    for i in range(len(df)):
        x = df.iloc[[i]]
        t = time.perf_counter()
        p.predict(x)
        if i >= 3:
            ts.append((time.perf_counter() - t) * 1000)
    a = np.asarray(ts)
    return {"n": int(len(a)), "p50_ms": float(np.percentile(a, 50)), "p95_ms": float(np.percentile(a, 95)),
            "mean_ms": float(a.mean()), "model": p.model_best, "method": "predictor.predict on one row, n calls after 3 warm-ups"}
