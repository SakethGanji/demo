"""G8 · Multi-label classification: each row carries a set of labels Y ⊆ {1, …, L} (complaint topics, say).

AutoGluon's MultilabelPredictor pattern: one binary TabularPredictor per label, y_l = [l ∈ Y], every one on the same rows,
the same inputs and the same config. The feature pipeline is shared in what it is told: the column types (a free-text
column is declared text once, from the G0 roles) go to every fit as one feature_metadata, so every label's model reads
the same columns the same way. Labels are modelled independently (AutoGluon's `consider_labels_correlation=False`): a
row's label set is then the labels whose own model clears its own threshold, and no label waits for another's prediction.

    p̂_l(x)    P(l ∈ Y | x) from label l's predictor (eval metric log loss, so p̂ stays a probability)
    t_l       label l's threshold: AutoGluon's calibrate_decision_threshold(metric="f1") on that predictor's own
              validation rows (the split's tuning slice when there is one, else AutoGluon's internal holdout); the run's
              holdout never chooses it
    Ŷ(x)     {l : p̂_l(x) ≥ t_l}

On the holdout (n rows, L labels, Y and Ŷ as n × L 0/1 matrices):
    F1_l       2·TP_l / (2·TP_l + FP_l + FN_l)                       per label, with a bootstrap 95 % CI
    macro F1   (1/L) Σ_l F1_l                                        every label counts the same
    micro F1   2·ΣTP / (2·ΣTP + ΣFP + ΣFN)                           every (row, label) decision counts the same
    Hamming    (1/(n·L)) Σ_i Σ_l [Ŷ_il ≠ Y_il]                       share of wrong (row, label) decisions
    subset     (1/n) Σ_i [Ŷ_i = Y_i]                                 exact label sets
    AP_l       average precision, the area under label l's precision–recall curve (threshold-free)
Row-bootstrap CIs (B = 300) for macro F1, micro F1 and Hamming; Hamming of the empty prediction (= label density) is the
baseline any model must beat.
"""

from __future__ import annotations

import json
import re
import time
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

DELIMS = (";", "|", ",")
MIN_POS = 20      # a label needs this many positive training rows to get its own model
B = 300
Y = "__label"
INDICATOR_SKIP = {"is", "has", "was", "will", "num", "n", "flag", "no", "not", "pct", "avg", "total", "count"}
_CACHE: dict[str, tuple[float, dict[str, Any]]] = {}


# ------------------------------------------------------------------------------------------- detection (G0 roles)

def split_labels(s: pd.Series, delimiter: str) -> list[list[str]]:
    """Each value → its list of labels (stripped, de-duplicated, order kept); blank or missing → []."""
    out = []
    for v in s.tolist():
        if v is None or (isinstance(v, float) and np.isnan(v)):
            out.append([])
            continue
        seen: list[str] = []
        for x in str(v).split(delimiter):
            x = x.strip()
            if x and x not in seen:
                seen.append(x)
        out.append(seen)
    return out


def delimiter_of(s: pd.Series) -> str | None:
    from ..intake import _multilabel

    m = _multilabel(s)
    return m[0] if m else None


def indicator_groups(df: pd.DataFrame) -> list[dict[str, Any]]:
    """Groups of ≥ 3 0/1 columns that share a prefix ('topic_fees', 'topic_fraud', …) and overlap (≥ 5 % of rows have
    two or more): one column per label. Generic flag prefixes (is_, has_, num_, …) never form a group."""
    from ..intake import _binary01

    by: dict[str, list[str]] = {}
    for c in df.columns:
        name = str(c)
        if "_" not in name:
            continue
        pre = name.split("_", 1)[0]
        if pre.lower() in INDICATOR_SKIP or len(pre) < 3:
            continue
        s = df[c]
        if s.nunique(dropna=True) == 2 and _binary01(s):
            by.setdefault(pre, []).append(name)
    out = []
    for pre, cols in by.items():
        if len(cols) < 3:
            continue
        M = indicator_matrix(df, cols)
        multi = float((M.sum(axis=1) >= 2).mean())
        if multi >= 0.05:
            out.append({"prefix": pre, "columns": cols, "multi_share": multi})
    return out


def _to01(s: pd.Series) -> np.ndarray:
    v = s.astype(str).str.strip().str.lower()
    return v.isin(["1", "1.0", "true", "yes", "y"]).to_numpy(dtype=int)


def indicator_matrix(df: pd.DataFrame, cols: list[str]) -> np.ndarray:
    return np.column_stack([_to01(df[c]) for c in cols]) if cols else np.zeros((len(df), 0), dtype=int)


def find(df: pd.DataFrame, cols: list[dict[str, Any]]) -> dict[str, Any]:
    """The label set, from the G0 roles: a delimited `multilabel` column first, else a group of 0/1 label columns."""
    out: dict[str, Any] = {"source": None, "label_column": None, "label_columns": [], "delimiter": None, "labels": [],
                           "evidence": [], "candidates": []}
    ml = [c for c in cols if c["role"] == "multilabel" or any(a["role"] == "multilabel" for a in c["also"])]
    delimited = [c for c in ml if delimiter_of(df[c["name"]])]
    groups = indicator_groups(df)
    out["candidates"] = [c["name"] for c in delimited] + [f"{g['prefix']}_*" for g in groups]
    if delimited:
        c = delimited[0]
        d = delimiter_of(df[c["name"]])
        sets = split_labels(df[c["name"]], d)
        vocab = sorted({x for xs in sets for x in xs})
        out.update(source="delimited", label_column=c["name"], delimiter=d, labels=vocab,
                   evidence=list(c["evidence"] if c["role"] == "multilabel" else
                                 next(a["evidence"] for a in c["also"] if a["role"] == "multilabel")))
    elif groups:
        g = groups[0]
        out.update(source="columns", label_column=f"{g['prefix']}_*", label_columns=g["columns"],
                   labels=[c.split("_", 1)[1] for c in g["columns"]],
                   evidence=[f"{len(g['columns'])} 0/1 columns named '{g['prefix']}_…': one per label",
                             f"{g['multi_share'] * 100:.0f}% of rows carry 2 or more"])
    return out


def label_matrix(df: pd.DataFrame, spec: dict[str, Any]) -> tuple[np.ndarray, list[str]]:
    """(n × L 0/1 matrix, label names) for the spec from `find` (delimited column or label columns)."""
    if spec["source"] == "columns":
        return indicator_matrix(df, spec["label_columns"]), list(spec["labels"])
    sets = split_labels(df[spec["label_column"]], spec["delimiter"])
    labels = list(spec["labels"])
    idx = {l: k for k, l in enumerate(labels)}
    M = np.zeros((len(df), len(labels)), dtype=int)
    for i, xs in enumerate(sets):
        for x in xs:
            if x in idx:
                M[i, idx[x]] = 1
    return M, labels


def label_source_columns(spec: dict[str, Any]) -> list[str]:
    return list(spec["label_columns"]) if spec["source"] == "columns" else [spec["label_column"]]


# --------------------------------------------------------------------------------------------------------- numbers

def _f1(y: np.ndarray, yh: np.ndarray) -> float:
    tp = float((y & yh).sum()); fp = float((~y & yh).sum()); fn = float((y & ~yh).sum())
    return 2 * tp / (2 * tp + fp + fn) if (2 * tp + fp + fn) > 0 else 1.0


def metrics(Yt: np.ndarray, Yh: np.ndarray) -> dict[str, float]:
    Yt, Yh = Yt.astype(bool), Yh.astype(bool)
    tp, fp, fn = (Yt & Yh).sum(), (~Yt & Yh).sum(), (Yt & ~Yh).sum()
    per = [_f1(Yt[:, k], Yh[:, k]) for k in range(Yt.shape[1])]
    inter, union = (Yt & Yh).sum(axis=1), (Yt | Yh).sum(axis=1)
    return {"macro_f1": float(np.mean(per)), "micro_f1": float(2 * tp / max(2 * tp + fp + fn, 1)),
            "hamming_loss": float((Yt != Yh).mean()), "subset_accuracy": float((Yt == Yh).all(axis=1).mean()),
            "jaccard": float(np.where(union > 0, inter / np.maximum(union, 1), 1.0).mean())}


def bootstrap(Yt: np.ndarray, Yh: np.ndarray, seed: int = 0) -> dict[str, Any]:
    """Row-bootstrap 95 % CIs of macro F1, micro F1, Hamming loss, and of each label's F1."""
    rng = np.random.default_rng(seed)
    n, L = Yt.shape
    Yt, Yh = Yt.astype(bool), Yh.astype(bool)
    keys = ("macro_f1", "micro_f1", "hamming_loss")
    draws = {k: [] for k in keys}
    per = np.zeros((B, L))
    for b in range(B):
        i = rng.integers(0, n, n)
        m = metrics(Yt[i], Yh[i])
        for k in keys:
            draws[k].append(m[k])
        per[b] = [_f1(Yt[i, k], Yh[i, k]) for k in range(L)]
    ci = lambda a: [float(np.percentile(a, 2.5)), float(np.percentile(a, 97.5))]  # noqa: E731
    return {**{k: ci(v) for k, v in draws.items()}, "per_label_f1": [ci(per[:, k]) for k in range(L)]}


def pr_curve(y: np.ndarray, p: np.ndarray, points: int = 40) -> dict[str, Any]:
    """Label l's precision–recall curve (thinned to ≈ `points` thresholds) and its average precision."""
    from sklearn.metrics import average_precision_score, precision_recall_curve

    if y.sum() == 0:
        return {"ap": None, "precision": [], "recall": [], "threshold": []}
    pr, rc, th = precision_recall_curve(y, p)
    keep = np.unique(np.linspace(0, len(th) - 1, min(points, len(th))).astype(int))
    return {"ap": float(average_precision_score(y, p)), "precision": [float(pr[i]) for i in keep],
            "recall": [float(rc[i]) for i in keep], "threshold": [float(th[i]) for i in keep]}


# ----------------------------------------------------------------------------------------------------------- run

def _frames(cfg: dict[str, Any]) -> tuple[pd.DataFrame, np.ndarray, np.ndarray, np.ndarray, dict[str, Any]]:
    """(data, train, tuning, holdout masks, info) from the run's split (fold −1 holdout, −3 tuning, −2 excluded)."""
    from ..splits import holdout_mask

    df = pd.read_csv(cfg["csv_path"])
    splits = json.loads(Path(cfg["splits_path"]).read_text())
    fold = np.asarray(splits["fold"], dtype=int)
    hold = holdout_mask(splits, len(df))
    structured = bool(splits.get("params"))
    tune = (fold == -3) if structured else np.zeros(len(df), dtype=bool)
    excl = (fold == -2) if structured else np.zeros(len(df), dtype=bool)
    train = ~hold & ~tune & ~excl
    info = {"rows": int(len(df)), "train_rows": int(train.sum()), "holdout_rows": int(hold.sum()),
            "tuning_rows": int(tune.sum()), "excluded_rows": int(excl.sum()), "label": cfg["label"]}
    return df, train, tune, hold, info


def run(cfg: dict[str, Any], run_dir: Path, emit, merge) -> dict[str, Any]:
    import ensemble_models  # noqa: F401 — registers TFIDF_LR / TFIDF_SVM, which read the text column

    from ..engine.progress import ProgressCallback
    from ..engine.worker import fit_predictor, prepare_fit

    spec = cfg["labels_spec"]
    df, tr_m, tu_m, ho_m, info = _frames(cfg)
    M, labels = label_matrix(df, spec)
    drop = set(cfg.get("drop_columns") or []) | set(label_source_columns(spec))
    inputs = [c for c in df.columns if c not in drop]
    X = df[inputs]
    numeric = [c for c in inputs if pd.api.types.is_numeric_dtype(X[c]) and not pd.api.types.is_bool_dtype(X[c])]
    text = [c for c in cfg.get("text_columns") or [] if c in inputs]
    info.update(features=inputs, dropped_columns=sorted(drop), labels=labels, text_columns=text)
    emit("data_ready", **info)
    merge(data=info)
    root = Path(cfg["predictor_path"])
    root.mkdir(parents=True, exist_ok=True)

    models: dict[str, str] = {}
    thresholds: dict[str, float] = {}
    fits: list[dict[str, Any]] = []
    for k, lab in enumerate(labels):
        y = M[:, k]
        npos = int(y[tr_m].sum())
        row: dict[str, Any] = {"label": lab, "train_positives": npos, "train_rows": int(tr_m.sum()),
                               "prevalence": float(y[tr_m].mean())}
        if npos < MIN_POS or npos > tr_m.sum() - MIN_POS:
            row.update(model=None, why=f"{npos} positive training rows: a label needs at least {MIN_POS} of each kind")
            fits.append(row)
            continue
        frame = X.assign(**{Y: y})
        tr, ho = frame[tr_m].copy(), frame[ho_m].copy()
        tu = frame[tu_m].copy() if tu_m.any() else None
        d = f"label_{k}_{re.sub(r'[^A-Za-z0-9]+', '_', lab)[:24]}"
        emit("ml_model_started", label=lab, rows=int(len(tr)), positives=npos)
        emit("log", level="info", logger="ensemble", message=f"multi-label: fitting '{lab}' ({k + 1}/{len(labels)}) on {len(tr):,} rows")
        sub = {**cfg, "label": Y, "problem_type": "binary", "eval_metric": "log_loss", "cost": None, "quantile": None,
               "monotone": None, "scorecard": None, "pseudolabel": None, "distill": None, "predictor_kwargs": {},
               "column_types": {c: "text" for c in text}, "text_columns": text, "predictor_path": str(root / d)}
        prep = prepare_fit(sub, tr, ho, tu, {**info, "label": Y})
        p, secs = fit_predictor(sub, prep, tr, str(root / d), callbacks=[ProgressCallback(emit)], emit=emit)
        # the label's own threshold: F1 on the predictor's validation rows (tuning slice, else AutoGluon's holdout)
        t = float(p.calibrate_decision_threshold(metric="f1", verbose=False))
        p.set_decision_threshold(t)
        p.save()
        models[lab], thresholds[lab] = d, t
        row.update(model=d, best_model=p.model_best, fit_seconds=secs, n_models=len(p.model_names()), threshold=t)
        fits.append(row)
        emit("ml_model_finished", label=lab, best_model=p.model_best, seconds=secs, threshold=t)

    if not models:
        raise ValueError(f"No label has {MIN_POS} positive training rows.")
    meta = {"labels": [l for l in labels if l in models], "all_labels": labels, "models": models, "thresholds": thresholds,
            "inputs": inputs, "numeric": numeric, "text_columns": text, "spec": spec,
            "delimiter": spec.get("delimiter") or ";"}
    (root / "meta.json").write_text(json.dumps(meta, indent=1, default=str))

    emit("log", level="info", logger="ensemble", message="multi-label: holdout F1 per label, micro/macro F1, Hamming loss")
    hx = X[ho_m].reset_index(drop=True)
    P = predict_proba(load(str(root)), hx)
    keep = [labels.index(l) for l in meta["labels"]]
    Yt = M[ho_m][:, keep].astype(bool)
    T = np.array([thresholds[l] for l in meta["labels"]])
    Yh = P >= T[None, :]
    m = metrics(Yt, Yh)
    ci = bootstrap(Yt, Yh)
    m05 = metrics(Yt, P >= 0.5)
    per = []
    for j, lab in enumerate(meta["labels"]):
        y, yh = Yt[:, j], Yh[:, j]
        tp, fp, fn = int((y & yh).sum()), int((~y & yh).sum()), int((y & ~yh).sum())
        per.append({"label": lab, "support": int(y.sum()), "prevalence": float(y.mean()), "threshold": float(T[j]),
                    "precision": tp / (tp + fp) if tp + fp else None, "recall": tp / (tp + fn) if tp + fn else None,
                    "f1": _f1(y, yh), "f1_ci": ci["per_label_f1"][j], "f1_at_05": _f1(y, P[:, j] >= 0.5),
                    "tp": tp, "fp": fp, "fn": fn, "pr": pr_curve(y.astype(int), P[:, j])})
    card_true, card_pred = Yt.sum(axis=1), Yh.sum(axis=1)
    hist = {str(k): [int((card_true == k).sum()), int((card_pred == k).sum())] for k in range(0, int(max(card_true.max(), card_pred.max())) + 1)}
    # Try It examples: holdout rows, the clearest multi-label one first
    order = np.argsort(-(card_true * 10 + (Yt == Yh).all(axis=1) * 5 + card_pred))
    ex_idx = list(dict.fromkeys([int(i) for i in order[:3]] + [int(i) for i in np.flatnonzero(card_true == 1)[:3]]))
    examples = [{"row": {c: (None if pd.isna(v) else (v.item() if hasattr(v, "item") else v)) for c, v in hx.iloc[i].items()},
                 "true": [meta["labels"][j] for j in np.flatnonzero(Yt[i])]} for i in ex_idx]
    pd.DataFrame(P, columns=[f"p_{l}" for l in meta["labels"]]).assign(n_labels=card_pred).to_csv(run_dir / "ml_holdout.csv", index=False)
    skipped = [f for f in fits if not f.get("model")]
    out = {"source": spec["source"], "label_column": spec["label_column"], "label_columns": spec.get("label_columns") or [],
           "delimiter": spec.get("delimiter"), "labels": meta["labels"], "skipped": skipped, "fits": fits,
           "holdout_rows": int(ho_m.sum()), "tuning_rows": int(tu_m.sum()), "train_rows": int(tr_m.sum()),
           **m, "ci": {k: ci[k] for k in ("macro_f1", "micro_f1", "hamming_loss")}, "at_05": m05,
           "baseline_hamming": float(Yt.mean()), "per_label": per,
           "cardinality": {"true": float(card_true.mean()), "predicted": float(card_pred.mean()), "hist": hist,
                           "multi_share_true": float((card_true >= 2).mean()), "multi_share_pred": float((card_pred >= 2).mean()),
                           "empty_pred": float((card_pred == 0).mean())},
           "examples": examples, "text_columns": text, "threshold_rule": "F1 on each predictor's validation rows"
           + (" (the split's tuning slice)" if tu_m.any() else " (AutoGluon's internal holdout)"),
           "detected": cfg.get("detected") or {}}
    merge(multilabel=out, problem_type="multilabel", best_model=f"{len(models)} label models",
          features=inputs, holdout={"metrics": {"macro_f1": m["macro_f1"], "micro_f1": m["micro_f1"], "hamming_loss": m["hamming_loss"]}},
          eval_metric={"name": "macro_f1", "greater_is_better": True})
    return {"labels": len(models), "macro_f1": m["macro_f1"]}


# --------------------------------------------------------------------------------------------- serving (Registry)

def load(path: str) -> dict[str, Any]:
    from autogluon.tabular import TabularPredictor

    import ensemble_models  # noqa: F401 — the label models may hold TFIDF_LR / TFIDF_SVM

    mt = (Path(path) / "meta.json").stat().st_mtime
    hit = _CACHE.get(path)
    if hit and hit[0] == mt:
        return hit[1]
    meta = json.loads((Path(path) / "meta.json").read_text())
    preds = {l: TabularPredictor.load(str(Path(path) / d), require_py_version_match=False) for l, d in meta["models"].items()}
    m = {"meta": meta, "predictors": preds, "path": path}
    _CACHE[path] = (mt, m)
    return m


def _frame(meta: dict[str, Any], rows: pd.DataFrame | list[dict[str, Any]]) -> pd.DataFrame:
    df = rows.copy() if isinstance(rows, pd.DataFrame) else pd.DataFrame(rows)
    if len(df) == 0:
        raise ValueError("No rows to score.")
    missing = [c for c in meta["inputs"] if c not in df.columns]
    if len(missing) == len(meta["inputs"]):
        raise ValueError(f"None of the model's inputs are in the rows: {', '.join(meta['inputs'][:6])}.")
    for c in missing:
        df[c] = np.nan
    for c in meta["numeric"]:
        df[c] = pd.to_numeric(df[c], errors="coerce")
    return df[meta["inputs"]].reset_index(drop=True)


def predict_proba(m: dict[str, Any], X: pd.DataFrame) -> np.ndarray:
    """n × L matrix of p̂_l(x), in the order of meta['labels']."""
    return np.column_stack([m["predictors"][l].predict_proba(X, as_multiclass=False).to_numpy(dtype=float)
                            for l in m["meta"]["labels"]])


def score_rows(path: str, rows: pd.DataFrame | list[dict[str, Any]]) -> dict[str, Any]:
    m = load(path)
    meta = m["meta"]
    X = _frame(meta, rows)
    t0 = time.perf_counter()
    P = predict_proba(m, X)
    ms = (time.perf_counter() - t0) * 1e3
    res = []
    for i in range(len(X)):
        sc = [{"label": l, "p": float(P[i, j]), "t": float(meta["thresholds"][l]), "on": bool(P[i, j] >= meta["thresholds"][l])}
              for j, l in enumerate(meta["labels"])]
        res.append({"labels": [s["label"] for s in sorted(sc, key=lambda s: -s["p"]) if s["on"]], "scores": sc})
    return {"results": res, "labels": meta["labels"], "thresholds": meta["thresholds"], "predict_ms": ms}


def try_row(path: str, row: dict[str, Any]) -> dict[str, Any]:
    out = score_rows(path, [row])
    r = out["results"][0]
    sc = sorted(r["scores"], key=lambda s: -s["p"])
    return {"labels": r["labels"], "scores": sc, "n_labels": len(r["labels"]), "thresholds": out["thresholds"],
            "rule": "Ŷ = {l : p̂_l(x) ≥ t_l}", "predict_ms": out["predict_ms"],
            "nearest_miss": None if r["labels"] else sc[0]}


def score_batch(path: str, df: pd.DataFrame) -> tuple[pd.DataFrame, dict[str, Any]]:
    m = load(path)
    meta = m["meta"]
    X = _frame(meta, df)
    P = predict_proba(m, X)
    T = np.array([meta["thresholds"][l] for l in meta["labels"]])
    Yh = P >= T[None, :]
    d = meta.get("delimiter") or ";"
    out = df.reset_index(drop=True).copy()
    out["labels"] = [d.join(meta["labels"][j] for j in np.flatnonzero(Yh[i])) for i in range(len(X))]
    out["n_labels"] = Yh.sum(axis=1)
    for j, l in enumerate(meta["labels"]):
        out[f"p_{l}"] = P[:, j]
    counts = [{"label": l, "n": int(Yh[:, j].sum()), "share": float(Yh[:, j].mean()), "mean_p": float(P[:, j].mean())}
              for j, l in enumerate(meta["labels"])]
    return out, {"labels": counts, "mean_labels": float(Yh.sum(axis=1).mean()), "empty": float((Yh.sum(axis=1) == 0).mean()),
                 "multi": float((Yh.sum(axis=1) >= 2).mean())}
