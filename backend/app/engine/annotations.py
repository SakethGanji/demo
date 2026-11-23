"""F14: ground-truth reliability. Several annotators per row: agreement (Cohen's κ per pair, Fleiss' κ), the quality
of each annotator, and a consensus label per row with a quality score from cleanlab's CROWDLAB
(`cleanlab.multiannotator.get_label_quality_multiannotator`), which weighs each annotator's labels and a model's
out-of-fold class probabilities by how much each agrees with the others.

Per run: annotations live in `runs/<id>/annotations/`:
- `annotations.csv`: long format (row, annotator, label, source, at); `row` is the data.csv row of the run's dataset.
- `adjudications.json`: {row: {label, by, at}}: a final label a person set in the disagreement queue.
- `probs.npz` + `probs.json`: the class probabilities p̂(x) of every training row, out of fold. The run's own OOF
  predictions when it was bagged; otherwise a quick bagged fit of the run's call (F12's prepare_fit / fit_predictor,
  `num_bag_folds = 5`, one GBM plus the run's text models) in its own process: `python -m app.engine.annotations <run_dir>`.

Only training rows are annotated, scored and relabelled: the run's holdout is never relabelled.
"""

from __future__ import annotations

import json
import logging
import os
import shutil
import sys
import threading
import time
import traceback
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from .. import frames
from ..splits import holdout_mask
from .jsonsafe import read_json, write_json

log = logging.getLogger("ensemble.annotations")

SCHEMA = "annotations/1"
QUEUE_THRESHOLD = 0.5  # a row whose consensus quality score is below this joins the queue even if everyone agreed
BOOT = 1000
MIN_RATED = 30  # an annotator with fewer labels is shown but not ranked as the worst

def ann_dir(run_dir: Path | str) -> Path:
    return Path(run_dir) / "annotations"


def read_state(run_dir: Path) -> dict[str, Any] | None:
    return read_json(ann_dir(run_dir) / "state.json")


def write_state(run_dir: Path, st: dict[str, Any]) -> None:
    write_json(ann_dir(run_dir) / "state.json", st)


def read_long(run_dir: Path) -> pd.DataFrame:
    p = ann_dir(run_dir) / "annotations.csv"
    if not p.exists():
        return pd.DataFrame(columns=["row", "annotator", "label", "source", "at"])
    return frames.read_artifact(p, dtype={"annotator": str, "label": str, "source": str, "at": str})


def write_long(run_dir: Path, df: pd.DataFrame) -> None:
    p = ann_dir(run_dir) / "annotations.csv"
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_name(f"annotations.{os.getpid()}.{threading.get_ident()}.tmp")
    df[["row", "annotator", "label", "source", "at"]].to_csv(tmp, index=False)
    os.replace(tmp, p)


def read_adjudications(run_dir: Path) -> dict[str, Any]:
    return read_json(ann_dir(run_dir) / "adjudications.json") or {}


def write_adjudications(run_dir: Path, adj: dict[str, Any]) -> None:
    write_json(ann_dir(run_dir) / "adjudications.json", adj)


def read_probs(run_dir: Path) -> tuple[dict[str, Any], np.ndarray] | None:
    d = ann_dir(run_dir)
    meta = read_json(d / "probs.json")
    if not meta or not (d / "probs.npz").exists():
        return None
    with np.load(d / "probs.npz") as z:
        P = z["P"]
    return meta, P


# --------------------------------------------------------------------------- rows of the run


def split_rows(cfg: dict[str, Any]) -> tuple[np.ndarray, np.ndarray]:
    """(training rows, holdout rows) of the run's split, as data.csv positions. Training = not holdout, not the F6
    tuning slice, not excluded by the split."""
    sp = json.loads(Path(cfg["splits_path"]).read_text())
    fold = np.asarray(sp["fold"], dtype=int)
    hold = np.asarray(sp["holdout"], dtype=int)
    is_hold = holdout_mask(sp, len(fold))
    structured = bool(sp.get("params"))
    bad = is_hold | ((fold == -2) | (fold == -3) if structured else np.zeros(len(fold), dtype=bool))
    return np.flatnonzero(~bad), np.sort(hold)


# --------------------------------------------------------------------------- upload


def parse_upload(data: pd.DataFrame, up: pd.DataFrame, label: str) -> tuple[pd.DataFrame, dict[str, Any]]:
    """A long-format annotations file → (row, annotator, label). The row is a `row` column (data.csv position,
    0-based) or a column of the dataset whose values are unique (an ID such as ticket_id). The annotator column is
    `annotator`, `reviewer`, `rater` or `labeller`; the label column `label`, `annotation` or the dataset's label name."""
    cols = {c.lower().strip(): c for c in up.columns}
    ann_col = next((cols[k] for k in ("annotator", "reviewer", "rater", "labeller", "labeler", "worker") if k in cols), None)
    lab_col = next((cols[k] for k in ("label", "annotation", label.lower()) if k in cols), None)
    if ann_col is None or lab_col is None:
        raise ValueError(f"An annotations file needs an annotator column (annotator / reviewer / rater) and a label "
                         f"column (label / annotation / {label}); this one has {', '.join(map(str, up.columns))}.")
    info: dict[str, Any] = {"annotator_column": ann_col, "label_column": lab_col}
    if "row" in cols:
        rows = pd.to_numeric(up[cols["row"]], errors="coerce")
        info["row_key"] = "row"
    else:
        key = next((c for c in up.columns if c in data.columns and c != label and c not in (ann_col, lab_col)
                    and data[c].is_unique), None)
        if key is None:
            raise ValueError("Name the rows with a `row` column (the dataset's row number, from 0) or with an ID column "
                             "of the dataset whose values are unique (for example ticket_id).")
        pos = pd.Series(np.arange(len(data)), index=data[key].astype(str))
        rows = up[key].astype(str).map(pos)
        info["row_key"] = key
    out = pd.DataFrame({"row": rows, "annotator": up[ann_col].astype(str).str.strip(), "label": up[lab_col]})
    bad = out["row"].isna() | (out["row"] < 0) | (out["row"] >= len(data))
    info["unmatched_rows"] = int(bad.sum())
    out = out[~bad & out["label"].notna() & (out["annotator"] != "")].copy()
    out["row"] = out["row"].astype(int)
    out["label"] = out["label"].map(lambda v: str(int(v)) if isinstance(v, float) and v.is_integer() else str(v))
    return out, info


# --------------------------------------------------------------------------- p̂(x): out-of-fold class probabilities


def quick_fit_cfg(cfg: dict[str, Any], path: str) -> dict[str, Any]:
    """The run's call made into a quick bagged fit: the same data handling (split, tuning slice, group folds, column
    types, text models, cost metric) with one LightGBM (plus the run's TF-IDF models for text) bagged 5 ways."""
    fk = {k: v for k, v in (cfg.get("fit_kwargs") or {}).items()
          if k not in ("hyperparameters", "num_bag_folds", "num_bag_sets", "num_stack_levels", "auto_stack",
                       "dynamic_stacking", "refit_full", "set_best_to_refit_full", "hyperparameter_tune_kwargs",
                       "infer_limit", "infer_limit_batch_size", "calibrate_decision_threshold")}
    fk.update({"num_bag_folds": 5, "num_bag_sets": 1, "num_stack_levels": 0, "dynamic_stacking": False,
               "hyperparameters": {"GBM": {"num_boost_round": 200}}})
    tl = cfg.get("time_limit")
    return {**cfg, "fit_kwargs": fk, "presets": "medium_quality", "time_limit": max(60.0, float(tl)) if tl else None,
            "monotone": None, "scorecard": None, "distill": None, "pseudolabel": None, "predictor_path": path}


def compute_probs(run_dir: Path) -> dict[str, Any]:
    """p̂(x) for every training row: out of fold for labelled rows, the bagged ensemble's prediction for training rows
    without a label (no fold model saw them). Writes probs.npz / probs.json."""
    from autogluon.tabular import TabularPredictor

    from . import worker as w

    run_dir = Path(run_dir)
    cfg = json.loads((run_dir / "config.json").read_text())
    d = ann_dir(run_dir)
    t0 = time.time()
    train, holdout, info, tuning = w.load_frames(cfg)
    label = cfg["label"]
    src: dict[str, Any] = {}
    predictor = None
    try:
        p = TabularPredictor.load(cfg["predictor_path"], require_py_version_match=False, check_packages=False)
        if p._trainer.bagged_mode:
            m = p.model_best
            if m.endswith("_FULL") and m[: -len("_FULL")] in p.model_names():
                m = m[: -len("_FULL")]
            if not m.endswith("_FULL"):
                predictor, src = p, {"source": "run-oof", "model": m,
                                     "note": f"the run's own out-of-fold predictions ({m}, bagged)"}
    except Exception as e:  # noqa: BLE001
        log.warning("could not load the run's predictor for OOF: %s", e)
    if predictor is None:
        path = str(d / "quick_fit")
        shutil.rmtree(path, ignore_errors=True)
        qcfg = quick_fit_cfg(cfg, path)
        prep = w.prepare_fit(qcfg, train, holdout, tuning, info)
        predictor, secs = w.fit_predictor(qcfg, prep, train, path)
        m = predictor.model_best
        src = {"source": "quick-bagged", "model": m, "fit_seconds": round(secs, 1),
               "hyperparameters": prep["resolved"]["fit_kwargs"].get("hyperparameters"),
               "num_bag_folds": 5,
               "note": f"the run is not bagged, so a quick bagged fit of its call gave the out-of-fold p̂ ({m}, 5 folds)"}
    proba = predictor.predict_proba_oof(model=src["model"])
    classes = [str(c) for c in predictor.class_labels]
    rows_tr, _ = split_rows(cfg)
    df = frames.read(frames.resolve(cfg))
    # P11: the fit's frames are indexed by __row_id (gaps after an edit on a Parquet version); rows here are read positions
    proba.index = pd.Index(df.index).get_indexer(proba.index)
    df = df.reset_index(drop=True)
    missing = [r for r in rows_tr.tolist() if r not in set(proba.index.tolist())]
    P = pd.DataFrame(np.nan, index=rows_tr, columns=proba.columns, dtype=float)
    common = proba.index.intersection(P.index)
    P.loc[common] = proba.loc[common].to_numpy()
    if missing:  # training rows without a label (an F13 version): the bagged ensemble's prediction; no fold saw them
        drop = [c for c in cfg.get("drop_columns") or [] if c in df.columns and c != label]
        X = df.loc[missing].drop(columns=drop + [label])
        if cfg.get("column_types"):
            w.apply_column_types(cfg, X, X.copy(), None)
        P.loc[missing] = predictor.predict_proba(X, model=src["model"]).to_numpy()
    P = P.dropna()
    if src["source"] == "quick-bagged":
        shutil.rmtree(d / "quick_fit", ignore_errors=True)
    meta = {**src, "classes": classes, "rows": [int(r) for r in P.index], "n_oof": int(len(common)),
            "n_predicted": len(missing), "seconds": round(time.time() - t0, 1)}
    d.mkdir(parents=True, exist_ok=True)
    np.savez_compressed(d / "probs.npz", P=P.to_numpy(dtype=float))
    write_json(d / "probs.json", meta)
    return meta


# --------------------------------------------------------------------------- agreement


def cohen_kappa(a: np.ndarray, b: np.ndarray, K: int) -> float:
    n = len(a)
    if n == 0:
        return float("nan")
    po = float((a == b).mean())
    pe = float((np.bincount(a, minlength=K) / n) @ (np.bincount(b, minlength=K) / n))
    return float("nan") if pe >= 1 else (po - pe) / (1 - pe)


def fleiss_parts(counts: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Per row: P_i = Σⱼ nᵢⱼ(nᵢⱼ − 1) / (nᵢ(nᵢ − 1)) and nᵢ, for rows with nᵢ ≥ 2."""
    n = counts.sum(1)
    Pi = (counts * (counts - 1)).sum(1) / (n * (n - 1))
    return Pi, n


def fleiss_kappa(counts: np.ndarray) -> float:
    """Fleiss' κ with a varying number of raters per row: κ = (P̄ − P̄ₑ)/(1 − P̄ₑ), P̄ = mean of P_i, P̄ₑ = Σⱼ pⱼ²,
    pⱼ = Σᵢ nᵢⱼ / Σᵢ nᵢ."""
    Pi, n = fleiss_parts(counts)
    p = counts.sum(0) / n.sum()
    Pe = float((p ** 2).sum())
    return float((Pi.mean() - Pe) / (1 - Pe)) if Pe < 1 else float("nan")


def mcnemar_exact(b: int, c: int) -> float:
    """Two-sided exact McNemar p-value: the discordant pairs b vs c under Binomial(b + c, ½)."""
    from scipy.stats import binomtest

    return 1.0 if b + c == 0 else float(binomtest(b, b + c, 0.5).pvalue)


# --------------------------------------------------------------------------- the analysis


def analyze(run_dir: Path, cfg: dict[str, Any], given: pd.Series | None = None) -> dict[str, Any]:
    """Everything the page shows, from the stored annotations, p̂ and adjudications. Returns
    {summary (annotations/1, for F17), annotators, pairwise, rows (per annotated row), classes, probs}."""
    from cleanlab.multiannotator import get_label_quality_multiannotator

    run_dir = Path(run_dir)
    long = read_long(run_dir)
    pr = read_probs(run_dir)
    if pr is None:
        raise ValueError("The out-of-fold class probabilities are not computed yet.")
    meta, Pall = pr
    classes = meta["classes"]
    K = len(classes)
    cidx = {c: i for i, c in enumerate(classes)}
    prow = {r: i for i, r in enumerate(meta["rows"])}
    long = long[long["row"].isin(prow.keys()) & long["label"].isin(cidx.keys())]
    long = long.drop_duplicates(["row", "annotator"], keep="last")
    if long.empty:
        raise ValueError("No annotations on training rows.")
    rows = np.sort(long["row"].unique())
    names = sorted(long["annotator"].unique(), key=lambda a: (-int((long["annotator"] == a).sum()), a))
    M = len(names)
    ri = {r: i for i, r in enumerate(rows)}
    mi = {a: j for j, a in enumerate(names)}
    L = np.full((len(rows), M), np.nan)
    L[long["row"].map(ri).to_numpy(), long["annotator"].map(mi).to_numpy()] = long["label"].map(cidx).to_numpy()
    P = Pall[[prow[r] for r in rows]]
    P = np.clip(P, 1e-6, 1)
    P = P / P.sum(1, keepdims=True)
    n_ann = (~np.isnan(L)).sum(1)
    counts = np.stack([(L == k).sum(1) for k in range(K)], axis=1)

    # cleanlab CROWDLAB: consensus = arg max of the posterior that weighs the model and every annotator
    lq: dict[str, Any] = {}
    if M >= 2:
        lq = get_label_quality_multiannotator(pd.DataFrame(L, columns=names), P,
                                              consensus_method=["best_quality", "majority_vote"],
                                              quality_method="crowdlab", return_weights=True, verbose=False)
        lab_q = lq["label_quality"]
        consensus = lab_q["consensus_label"].to_numpy().astype(int)
        cq = lab_q["consensus_quality_score"].to_numpy()
        agree = lab_q["annotator_agreement"].to_numpy()
        mv_cl = lab_q["consensus_label_majority_vote"].to_numpy().astype(int)
        agree_mv = lab_q["annotator_agreement_majority_vote"].to_numpy()
        stats = lq["annotator_stats"]
        # The posterior that chose the consensus: cleanlab's best_quality takes arg max of CROWDLAB's posterior built
        # around the majority vote. Recomputed with cleanlab's own function so the page can show p̃ and its weights.
        from cleanlab.multiannotator import _get_post_pred_probs_and_weights

        post, model_weight, aw = _get_post_pred_probs_and_weights(L, mv_cl, P, n_ann, agree_mv, "crowdlab", verbose=False)
        model_weight = float(model_weight)
        aw = np.asarray(aw, dtype=float)
        pi = float(np.mean(agree_mv[n_ann != 1]))
    else:
        consensus = np.nanargmax(np.where(np.isnan(L), -1, L), axis=1).astype(int)
        cq = P[np.arange(len(rows)), consensus]
        agree = np.ones(len(rows))
        mv_cl = consensus
        stats, aw, model_weight, pi = None, None, None, None
        post = P

    # plain majority vote, ties broken at random (seeded): the baseline a team would use without a model
    rng = np.random.default_rng(0)
    mx = counts.max(1, keepdims=True)
    tied = counts == mx
    n_ties = int((tied.sum(1) > 1).sum())
    mv = np.array([rng.choice(np.flatnonzero(t)) for t in tied])

    # adjudications override the consensus
    adj = read_adjudications(run_dir)
    final = consensus.copy()
    adjudicated = np.zeros(len(rows), dtype=bool)
    for r, a in adj.items():
        r = int(r)
        if r in ri and a.get("label") in cidx:
            final[ri[r]] = cidx[a["label"]]
            adjudicated[ri[r]] = True

    # Cohen's κ per pair, Fleiss' κ, with bootstrap intervals over rows
    brng = np.random.default_rng(14)
    pairwise = []
    Li = np.where(np.isnan(L), -1, L).astype(int)
    for x in range(M):
        for y in range(x + 1, M):
            both = (Li[:, x] >= 0) & (Li[:, y] >= 0)
            a, b = Li[both, x], Li[both, y]
            n = int(both.sum())
            if n < 10:
                pairwise.append({"a": names[x], "b": names[y], "n": n, "kappa": None, "ci": None, "agreement": None})
                continue
            idx = brng.integers(0, n, size=(300, n))
            boots = [cohen_kappa(a[i], b[i], K) for i in idx]
            pairwise.append({"a": names[x], "b": names[y], "n": n, "kappa": cohen_kappa(a, b, K),
                             "ci": [float(np.nanpercentile(boots, 2.5)), float(np.nanpercentile(boots, 97.5))],
                             "agreement": float((a == b).mean())})
    multi = n_ann >= 2
    fleiss = None
    if multi.sum() >= 10:
        C2 = counts[multi]
        Pi, ni = fleiss_parts(C2)
        idx = brng.integers(0, len(C2), size=(BOOT, len(C2)))
        boots = []
        for i in idx:
            p = C2[i].sum(0) / ni[i].sum()
            Pe = (p ** 2).sum()
            boots.append((Pi[i].mean() - Pe) / (1 - Pe))
        p = C2.sum(0) / ni.sum()
        Pe = float((p ** 2).sum())
        fleiss = {"value": fleiss_kappa(C2), "ci": [float(np.percentile(boots, 2.5)), float(np.percentile(boots, 97.5))],
                  "n_rows": int(multi.sum()), "P_bar": float(Pi.mean()), "P_e": Pe,
                  "p": {classes[k]: float(p[k]) for k in range(K)}, "raters_per_row": {"min": int(ni.min()), "max": int(ni.max()),
                                                                                       "mean": float(ni.mean())}}

    # per annotator
    ann = []
    for j, a in enumerate(names):
        has = Li[:, j] >= 0
        lab = Li[has, j]
        cons = consensus[has]
        st = stats.loc[a] if stats is not None and a in stats.index else None
        conf = np.zeros((K, K), dtype=int)
        np.add.at(conf, (cons, lab), 1)
        off = conf.copy()
        np.fill_diagonal(off, 0)
        top = None
        if off.sum():
            c_, l_ = np.unravel_index(off.argmax(), off.shape)
            top = {"consensus": classes[c_], "given": classes[l_], "n": int(off[c_, l_]),
                   "share_of_class": float(off[c_, l_] / max(1, conf[c_].sum())), "of_class_rows": int(conf[c_].sum())}
        kap = [p["kappa"] for p in pairwise if a in (p["a"], p["b"]) and p["kappa"] is not None]
        x = {"name": a, "n": int(has.sum()),
             "quality": float(st["annotator_quality"]) if st is not None else None,
             "agreement_with_consensus": float(st["agreement_with_consensus"]) if st is not None else float((lab == cons).mean()),
             "worst_class": classes[int(st["worst_class"])] if st is not None and not pd.isna(st["worst_class"]) else None,
             "weight": float(aw[j]) if aw is not None else None,
             "kappa_mean": float(np.mean(kap)) if kap else None,
             "top_confusion": top,
             "confusion_vs_consensus": conf.tolist()}
        ann.append(x)
    rated = [x for x in ann if x["quality"] is not None and x["n"] >= MIN_RATED]
    worst = min(rated, key=lambda x: x["quality"]) if rated else None

    # the dataset's own label on these rows
    G = None
    if given is not None:
        g = given.reindex(rows).astype(str).to_numpy()
        G = np.array([cidx.get(v, -1) for v in g])

    disagree = (counts.max(1) < n_ann)
    in_queue = disagree | (cq < QUEUE_THRESHOLD)
    summary: dict[str, Any] = {
        "schema": SCHEMA, "n_rows": int(len(rows)), "n_annotations": int((Li >= 0).sum()), "n_annotators": M,
        "annotations_per_row": {"mean": float(n_ann.mean()), "min": int(n_ann.min()), "max": int(n_ann.max())},
        "classes": classes,
        "probs": {k: meta.get(k) for k in ("source", "model", "note", "n_oof", "n_predicted", "fit_seconds")},
        "agreement": {"fleiss_kappa": {k: fleiss[k] for k in ("value", "ci", "n_rows")} if fleiss else None,
                      "cohen_kappa_mean": float(np.mean([p["kappa"] for p in pairwise if p["kappa"] is not None]))
                      if any(p["kappa"] is not None for p in pairwise) else None,
                      "raw_agreement": float((counts.max(1)[multi] == n_ann[multi]).mean()) if multi.any() else None,
                      "interpretation": _landis(fleiss["value"]) if fleiss else None},
        "annotators": [{k: x.get(k) for k in ("name", "n", "quality", "agreement_with_consensus", "weight", "kappa_mean",
                                              "worst_class", "top_confusion")} for x in ann],
        "worst_annotator": {"name": worst["name"], "quality": worst["quality"], "by": "cleanlab annotator_quality (CROWDLAB)",
                            "min_labels": MIN_RATED}
        if worst else None,
        "model_weight": model_weight,
        "crowdlab": {"pi": pi, "model_weight": model_weight,
                     "annotator_weights": {a: float(aw[j]) for j, a in enumerate(names)} if aw is not None else None,
                     "consensus_is_argmax": bool((post.argmax(1) == consensus).mean() == 1.0),
                     "weights_from": "the majority-vote pass that cleanlab's best_quality consensus is the arg max of"},
        "consensus": {"method": "crowdlab (cleanlab.multiannotator, consensus_method='best_quality')",
                      "disagreements": int(disagree.sum()), "low_quality": int((cq < QUEUE_THRESHOLD).sum()),
                      "queue": int(in_queue.sum()), "queue_threshold": QUEUE_THRESHOLD,
                      "consensus_vs_majority_vote_differ": int((consensus != mv).sum()), "majority_vote_ties": n_ties,
                      "adjudicated": int(adjudicated.sum()),
                      "changed_vs_given": int((final != G).sum()) if G is not None else None,
                      "mean_quality": float(cq.mean())},
    }
    detail = {
        "rows": rows, "L": Li, "names": names, "P": P, "consensus": consensus, "cq": cq, "agree": agree, "mv": mv,
        "mv_cl": mv_cl, "post": post, "final": final, "adjudicated": adjudicated, "in_queue": in_queue, "n_ann": n_ann, "G": G,
    }
    return {"summary": summary, "annotators": ann, "pairwise": pairwise, "fleiss": fleiss, "classes": classes,
            "detail": detail, "probs_meta": meta}


def _landis(k: float) -> str:
    """Landis & Koch's (1977) bands for κ."""
    for lo, word in ((0.81, "almost perfect"), (0.61, "substantial"), (0.41, "moderate"), (0.21, "fair"), (0.0, "slight")):
        if k >= lo:
            return word
    return "poor"


def queue_items(an: dict[str, Any], data: pd.DataFrame, label: str, show_cols: list[str], *, offset: int = 0,
                limit: int = 50, include: str = "queue") -> tuple[list[dict[str, Any]], int]:
    """The disagreement queue: rows annotators disagree on or whose consensus quality is below the threshold, lowest
    consensus quality first, with every vote, the model's p̂, the consensus and the plain majority vote."""
    d = an["detail"]
    classes = an["classes"]
    sel = np.flatnonzero(d["in_queue"]) if include == "queue" else np.arange(len(d["rows"]))
    order = sel[np.argsort(d["cq"][sel], kind="stable")]
    adj = None
    items = []
    for i in order[offset: offset + limit]:
        r = int(d["rows"][i])
        p = d["P"][i]
        top = np.argsort(-p)[:3]
        rec = data.loc[r]
        items.append({
            "row": r, "fields": {c: (None if pd.isna(rec[c]) else str(rec[c])[:400]) for c in show_cols},
            "given": None if pd.isna(rec[label]) else str(rec[label]),
            "votes": [{"annotator": d["names"][j], "label": classes[d["L"][i, j]]} for j in range(len(d["names"])) if d["L"][i, j] >= 0],
            "p_model": [{"label": classes[k], "p": float(p[k])} for k in top],
            "posterior": [{"label": classes[k], "p": float(d["post"][i][k])} for k in np.argsort(-d["post"][i])[:3]],
            "consensus": classes[d["consensus"][i]], "consensus_quality": float(d["cq"][i]),
            "agreement": float(d["agree"][i]), "majority_vote": classes[d["mv"][i]],
            "final": classes[d["final"][i]], "adjudicated": bool(d["adjudicated"][i]),
        })
    _ = adj
    return items, int(len(sel))


def headline(st: dict[str, Any] | None) -> dict[str, Any] | None:
    if not st:
        return None
    return {"schema": SCHEMA, "status": st.get("status"), "error": st.get("error"), "summary": st.get("summary"),
            "updated_at": st.get("updated_at")}


# --------------------------------------------------------------------------- the p̂ process


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    run_dir = Path(sys.argv[1])
    if len(sys.argv) > 2:  # F-JOBS: the worker that holds the job (its death ends this process)
        from .jobs import watch_parent

        watch_parent(int(sys.argv[2]), anchor=True)
    st = read_state(run_dir) or {}
    try:
        meta = compute_probs(run_dir)
        st = read_state(run_dir) or st
        st.update(status="ready", probs=meta, error=None)
        write_state(run_dir, st)
        sys.exit(0)
    except Exception as e:  # noqa: BLE001
        tb = traceback.format_exc()
        log.error("annotations p̂ failed:\n%s", tb)
        st = read_state(run_dir) or st
        st.update(status="failed", error=f"{type(e).__name__}: {e}", traceback=tb[-3000:])
        write_state(run_dir, st)
        sys.exit(1)


if __name__ == "__main__":
    main()
