"""D3 · Typed experiments: one change to a finished run's own body, trained as a child run with lineage
(`parent_run_id`, `source: doctor-experiment`, `patch.experiment`), on the same dataset and so the same holdout rows.
The result is Δ = score(child) − score(parent) on the shared holdout rows with a paired bootstrap (B = 1,000).

Kinds (D4's review loop adds its hypotheses through the same route):
  ablate   {columns: [...]}  retrain without the columns (leakage ablation)
  budget   {}                twice the time budget (≤ 4 h)
  preset   {}                the next preset (medium → good → high → best)

Score: binary AUC (a cost run: expected cost per 1,000 at each run's served threshold, lower is better), multiclass
accuracy, regression RMSE (lower is better). The sign of Δ is "better is positive" in every case.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

KINDS = ("ablate", "budget", "preset", "cost_ratio", "monotone", "apply_data_fix", "add_features", "clean_labels",
         "serve_rung", "segment")
RUN_KINDS = ("ablate", "budget", "preset", "monotone", "apply_data_fix", "add_features", "clean_labels")  # a child run
INSTANT = ("serve_rung", "cost_ratio")  # read from the run's own files, no training
NEXT_PRESET = {"medium_quality": "good_quality", "good_quality": "high_quality", "high_quality": "best_quality",
               "best_quality": "best_quality"}
DESCRIBE = {
    "ablate": "{kind: ablate, columns: [..]}: retrain without the columns (a leak, a drifted or a noisy feature)",
    "budget": "{kind: budget}: retrain with twice the time budget",
    "preset": "{kind: preset}: retrain with the next AutoGluon preset (medium → good → high → best)",
    "cost_ratio": "{kind: cost_ratio, c_fn, c_fp}: no retrain; the same model at the Bayes threshold of another cost ratio "
                  "(binary cost runs): decline / catch rates and what keeping the served threshold costs if that ratio is the true one",
    "monotone": "{kind: monotone, constraints: {column: increasing|decreasing}}: retrain with monotone constraints (binary / regression, numeric columns)",
    "apply_data_fix": "{kind: apply_data_fix, transforms: [..]}: apply Data Doctor fixes (a new dataset version, same holdout rows), then retrain",
    "add_features": "{kind: add_features, candidates?: [typed Feature Lab transforms]}: Feature Lab scores them on the training "
                    "folds; the kept ones make a new version (same holdout rows), then retrain. Without candidates its heuristics propose",
    "clean_labels": "{kind: clean_labels}: cleanlab on the training rows only (out of fold); the flagged training rows are dropped "
                    "in a new version, the holdout untouched, then the full ensemble is retrained",
    "serve_rung": "{kind: serve_rung, rung: 1|2}: no retrain; a simpler rung of the model ladder (scorecard / linear, single GBM) "
                  "against the best on the same holdout",
    "segment": "{kind: segment, column}: one model per value of a category column (2–12 values) against one global model (G5), same holdout",
}


def _cols(spec: dict[str, Any], key: str = "columns") -> list[str]:
    cols = spec.get(key)
    if not isinstance(cols, list) or not cols or not all(isinstance(c, str) for c in cols):
        raise ValueError(f"{spec.get('kind')} needs {key}: a non-empty list of column names.")
    return cols


def validate(rec: dict[str, Any], spec: dict[str, Any]) -> dict[str, Any]:
    """Check a typed experiment against the run without starting it; returns the clean spec. ValueError otherwise."""
    if not isinstance(spec, dict):
        raise ValueError("an experiment is a JSON object {kind, ...}.")
    kind = spec.get("kind")
    if kind not in KINDS:
        raise ValueError(f"kind is one of {', '.join(KINDS)} (got {kind!r}); experiments are typed, never free-form.")
    res = rec.get("result") or {}
    cfg = rec.get("config") or {}
    feats = set(res.get("features") or [])
    pt = res.get("problem_type")
    out: dict[str, Any] = {"kind": kind}
    if kind == "ablate":
        cols = _cols(spec)
        unknown = [c for c in cols if c not in feats]
        if unknown:
            raise ValueError(f"not features of this run: {', '.join(unknown)}.")
        if len(set(cols)) >= len(feats):
            raise ValueError("an ablation must leave at least one feature.")
        out["columns"] = list(dict.fromkeys(cols))
    elif kind == "cost_ratio":
        cost = cfg.get("cost") or {}
        if pt != "binary" or cost.get("kind", "binary") != "binary" or not cost:
            raise ValueError("cost_ratio is for binary runs that optimise a business cost.")
        try:
            c_fn, c_fp = float(spec.get("c_fn")), float(spec.get("c_fp", 1))
        except (TypeError, ValueError):
            raise ValueError("cost_ratio needs c_fn and c_fp (numbers > 0).") from None
        if c_fn <= 0 or c_fp <= 0:
            raise ValueError("cost_ratio needs c_fn and c_fp > 0.")
        if abs(c_fn / c_fp - float(cost["c_fn"]) / float(cost["c_fp"])) < 1e-9:
            raise ValueError(f"the run already uses {c_fn / c_fp:g}:1.")
        out.update(c_fn=c_fn, c_fp=c_fp)
    elif kind == "monotone":
        cons = spec.get("constraints")
        if pt not in ("binary", "regression") or not isinstance(cons, dict) or not cons:
            raise ValueError("monotone needs constraints {column: increasing|decreasing} on a binary or regression run.")
        bad = [c for c in cons if c not in feats]
        if bad:
            raise ValueError(f"not features of this run: {', '.join(bad)}.")
        if any(v not in ("increasing", "decreasing", 1, -1) for v in cons.values()):
            raise ValueError("each constraint is increasing or decreasing.")
        out["constraints"] = {c: ("increasing" if v in ("increasing", 1) else "decreasing") for c, v in cons.items()}
    elif kind == "apply_data_fix":
        from ..data_doctor import TRANSFORMS

        tr = spec.get("transforms")
        if not isinstance(tr, list) or not tr or not all(isinstance(t, dict) for t in tr):
            raise ValueError("apply_data_fix needs transforms: a non-empty list of Data Doctor transforms ({op, ...}).")
        for t in tr:
            if t.get("op") not in TRANSFORMS:
                raise ValueError(f"not a Data Doctor transform: {t.get('op')!r} (one of {', '.join(TRANSFORMS)}).")
            if t.get("op") == "merge_classes" and t.get("column") in (rec.get("label"), None):
                raise ValueError("merging classes of the target changes the task: the scores would not be comparable. "
                                 "It belongs in the improvement plan (redefine the label), not in an experiment.")
            if t.get("op") == "drop_rows":
                raise ValueError("dropping rows by number is not an experiment (it could drop holdout rows); use clean_labels.")
        out["transforms"] = tr
    elif kind == "add_features":
        c = spec.get("candidates")
        if c is not None:
            from .. import featurelab as fl

            if not isinstance(c, list) or not c or any(not isinstance(x, dict) or x.get("op") not in fl.OPS for x in c):
                raise ValueError(f"add_features candidates are typed Feature Lab transforms ({{op, ...}}, op one of {', '.join(fl.OPS)}).")
            out["candidates"] = c[:20]
    elif kind == "clean_labels":
        if pt not in ("binary", "multiclass"):
            raise ValueError("clean_labels is for classification runs.")
    elif kind == "serve_rung":
        try:
            rung = int(spec.get("rung"))
        except (TypeError, ValueError):
            raise ValueError("serve_rung needs rung: 1 (scorecard / linear) or 2 (single GBM).") from None
        if rung not in (1, 2):
            raise ValueError("serve_rung needs rung: 1 (scorecard / linear) or 2 (single GBM).")
        lad = res.get("ladder") or {}
        r = next((x for x in lad.get("rungs") or [] if x.get("rung") == rung), None)
        if not lad or not r or not r.get("gap") or not r.get("model"):
            raise ValueError(f"rung {rung} has no score on this run's ladder.")
        if r.get("model") == res.get("best_model"):
            raise ValueError(f"rung {rung} ({r.get('name')}) is the model the run already serves.")
        out["rung"] = rung
    elif kind == "segment":
        col = spec.get("column")
        if not isinstance(col, str) or col not in feats:
            raise ValueError("segment needs column: a feature of this run.")
        if pt not in ("binary", "regression"):
            raise ValueError("segment is for binary and regression runs (G5).")
        out["column"] = col
    if kind in ("budget", "preset") and cfg.get("import"):
        raise ValueError("an imported run has no training body.")
    if kind == "preset":
        cur = cfg.get("presets") or "medium_quality"
        cur = cur[0] if isinstance(cur, list) else cur
        if NEXT_PRESET.get(cur, "good_quality") == cur:
            raise ValueError(f"the run already uses {cur}.")
    return out


def body_for(rec: dict[str, Any], spec: dict[str, Any], dataset_id: str | None = None, prep: dict[str, Any] | None = None
             ) -> tuple[dict[str, Any], str]:
    """The child's POST /api/runs body and its name. Raises ValueError with a message for the user. `dataset_id`: the
    prepared dataset version (apply_data_fix, add_features, clean_labels) with the parent's holdout rows."""
    from ..gym.api import run_body

    spec = validate(rec, spec)
    kind = spec["kind"]
    if kind not in RUN_KINDS:
        raise ValueError(f"{kind} does not train a child run.")
    b = run_body(rec)
    if kind == "ablate":
        cols = spec["columns"]
        b["drop_columns"] = list(dict.fromkeys(list(b.get("drop_columns") or []) + cols))
        what = f"without {', '.join(cols)}"
    elif kind == "budget":
        tau = float(b.get("time_limit") or ((rec.get("result") or {}).get("fit_seconds") or 120))
        b["time_limit"] = float(min(4 * 3600, round(tau * 2)))
        what = f"budget {tau:.0f} s → {b['time_limit']:.0f} s"
    elif kind == "preset":
        cur = b.get("presets") or "medium_quality"
        cur = cur[0] if isinstance(cur, list) else cur
        nxt = NEXT_PRESET.get(cur, "good_quality")
        b["presets"] = nxt
        what = f"preset {cur} → {nxt}"
    elif kind == "monotone":
        b["monotone"] = {"constraints": spec["constraints"], "compliant_only": True}
        what = "monotone " + ", ".join(f"{c} {'↑' if v == 'increasing' else '↓'}" for c, v in spec["constraints"].items())
    else:
        if not dataset_id:
            raise ValueError(f"{kind} trains on a prepared dataset version.")
        b["dataset_id"] = dataset_id
        what = {"apply_data_fix": "Data Doctor fixes", "add_features": "Feature Lab features",
                "clean_labels": "cleaned training labels"}[kind]
        if prep and prep.get("what"):
            what += f" ({prep['what']})"
    b["parent_run_id"] = rec["id"]
    b["source"] = "doctor-experiment"
    b["patch"] = {"id": f"experiment:{kind}", "title": what, "experiment": {**spec, **({"prepared": prep} if prep else {})},
                  "changes": []}
    b["name"] = f"{rec['name']} · {what}"
    return b, what


# ------------------------------------------------------------------ preparation (a dataset version, same holdout rows)


def flag_training_labels(cfg: dict[str, Any], label: str) -> dict[str, Any]:
    """(Heavy pool.) cleanlab on the TRAINING rows only (D1's out-of-fold quick model: histogram GBM, or TF-IDF + LR
    with text): the holdout rows are neither flagged nor read. Returns the flagged row numbers of the dataset."""
    from .. import data_doctor as dd
    from ..intake import detect
    from ..splits import holdout_mask

    df = pd.read_csv(cfg["csv_path"])
    sp = json.loads(Path(cfg["splits_path"]).read_text())
    tr = np.flatnonzero(~holdout_mask(sp, len(df)) & df[label].notna().to_numpy())
    sub = df.iloc[tr].reset_index(drop=True)
    drop = [c for c in (cfg.get("drop_columns") or []) if c in sub.columns and c != label]
    sub = sub.drop(columns=drop)
    out = dd.label_issues(sub, label, detect(sub)["columns"], all_rows=True)
    if out.get("status") != "done":
        return {"rows": [], "status": out.get("status"), "reason": out.get("reason")}
    rows = sorted(int(tr[i]) for i in out["issue_rows"])
    return {"rows": rows, "status": "done", "flagged": len(rows), "of": int(len(tr)), "model": out["model"],
            "method": out["method"] + " over the training rows only; the holdout is untouched"}


# ------------------------------------------------------------------ instant experiments (no training)


def serve_rung_result(rec: dict[str, Any], rung: int) -> dict[str, Any]:
    """The rung against the model the run serves (model_best), from the D2 ladder's paired bootstrap. Exact when either
    one is the ladder's best rung (every gap is to the best on the same draws); otherwise against the best rung."""
    res = rec.get("result") or {}
    lad = res.get("ladder") or {}
    rungs = lad["rungs"]
    r = next(x for x in rungs if x.get("rung") == rung)
    best_k = lad.get("best_rung")
    best = next((x for x in rungs if x.get("rung") == best_k), None)
    served = next((x for x in rungs if x.get("model") and x.get("model") == res.get("best_model") and not x.get("same_as")), None) \
        or next((x for x in rungs if x.get("rung") == 3), None)
    if served is not None and served.get("same_as"):
        served = next((x for x in rungs if x.get("rung") == served["same_as"]), served)
    g = r["gap"]
    if served is not None and r["rung"] == best_k and served.get("gap"):
        d, ci, vs = float(served["gap"]["delta"]), [float(served["gap"]["ci"][0]), float(served["gap"]["ci"][1])], served
    elif served is not None and served.get("rung") == best_k:
        d, ci, vs = -float(g["delta"]), [-float(g["ci"][1]), -float(g["ci"][0])], served
    else:
        d, ci, vs = -float(g["delta"]), [-float(g["ci"][1]), -float(g["ci"][0])], best
    lab = lad.get("metric_label") or lad.get("metric")
    cost = lad.get("metric") == "expected_cost"
    return {"metric": "cost / 1,000" if cost else lab, "parent": vs and vs.get("score"), "child": r.get("score"),
            "delta": d, "ci": ci, "rows": lad.get("n_holdout"), "level": lad.get("level"), "B": lad.get("B"), "same_rows": True,
            "versus": f"rung {vs and vs.get('rung')} ({vs and vs.get('name')})" + (", the served model" if vs is served else ", the best rung"),
            "rung": {"rung": rung, "name": r.get("name"), "model": r.get("model"), "latency_ms_p95": r.get("latency_ms_p95"),
                     "size_bytes": r.get("size_bytes"), "within": r.get("within")},
            "best": vs and {"name": vs.get("name"), "model": vs.get("model"), "latency_ms_p95": vs.get("latency_ms_p95"),
                            "size_bytes": vs.get("size_bytes")},
            "sign": "better is positive" + (" (expected cost per 1,000: Δ is the reduction)" if cost else ""),
            "source": f"D2 model ladder: paired bootstrap on the run's holdout ({lad.get('level', 0.95):.0%} interval), every rung on the same draw"}


def best_threshold(y: np.ndarray, p: np.ndarray, c_fp: float, c_fn: float, g_tp: float = 0.0) -> float:
    """The threshold that minimises c_FP·FP + c_FN·FN − g_TP·TP on these rows (over the rows' own scores)."""
    o = np.argsort(-p)
    ps, ys = p[o], y[o]
    tp = np.cumsum(ys)  # deciding positive for the top k rows
    fp = np.cumsum(1 - ys)
    fn = ys.sum() - tp
    cost = np.concatenate([[c_fn * ys.sum()], c_fp * fp + c_fn * fn - g_tp * tp])
    k = int(np.argmin(cost))
    if k == 0:
        return float(ps[0])  # decide no one positive
    nxt = ps[k] if k < len(ps) else ps[-1] - 1e-9
    return float((ps[k - 1] + nxt) / 2)


def crossfit_decisions(y: np.ndarray, p: np.ndarray, c_fp: float, c_fn: float, g_tp: float = 0.0, seed: int = 0
                       ) -> tuple[np.ndarray, float, list[float]]:
    """Out-of-sample decisions: the cost-optimal threshold chosen on one half of the rows (stratified), applied to the
    other, and back. Returns (decisions, the threshold chosen on all rows, the two half thresholds)."""
    rng = np.random.default_rng(seed)
    half = np.zeros(len(y), bool)
    for v in (0, 1):
        idx = np.flatnonzero(y == v)
        half[rng.permutation(idx)[: len(idx) // 2]] = True
    d = np.zeros(len(y), bool)
    ts = []
    for fit, app in ((half, ~half), (~half, half)):
        t = best_threshold(y[fit], p[fit], c_fp, c_fn, g_tp)
        ts.append(t)
        d[app] = p[app] > t
    return d, best_threshold(y, p, c_fp, c_fn, g_tp), ts


def cost_ratio_result(rec: dict[str, Any], c_fn: float, c_fp: float, b: int = 1000) -> dict[str, Any]:
    """The same model at the cost-optimal threshold of ratio r' = c_fn / c_fp, chosen out of sample (cross-fitted halves
    of the holdout). Δ = what keeping the served threshold costs per 1,000 decisions if r' is the true ratio
    (cost at the served t − cost at r''s threshold, both under r'; positive = confirming the ratio is worth it)."""
    from ensemble_models.cost import binary_row_costs

    hp, _ = _scores(rec)
    res = rec.get("result") or {}
    cost = rec["config"]["cost"]
    pos = str(res.get("positive_class"))
    y = (hp["y_true"].astype(str) == pos).to_numpy().astype(int)
    p = hp[f"p_{pos}"].to_numpy(dtype=float)
    t0 = float(((res.get("threshold") or {}).get("value", 0.5)))
    g = float(cost.get("g_tp") or 0)
    d_alt, t1, halves = crossfit_decisions(y, p, c_fp, c_fn, g)
    d_srv = p > t0

    def op(d, cfp, cfn, t=None):
        rows = 1000 * binary_row_costs(y, d, cfp, cfn, g)
        return {"t": t, "decline_rate": float(d.mean()), "catch_rate": float(d[y == 1].mean()) if y.sum() else None,
                "cost_per_1000": float(rows.mean())}, rows

    served_new, ra = op(d_srv, c_fp, c_fn, t0)
    new_new, rb = op(d_alt, c_fp, c_fn, t1)
    served_old, _ = op(d_srv, float(cost["c_fp"]), float(cost["c_fn"]), t0)
    new_old, _ = op(d_alt, float(cost["c_fp"]), float(cost["c_fn"]), t1)
    rng = np.random.default_rng(0)
    n = len(y)
    ds = [float(ra[i].mean() - rb[i].mean()) for i in (rng.integers(0, n, n) for _ in range(b))]
    r0 = float(cost["c_fn"]) / float(cost["c_fp"])
    r1 = c_fn / c_fp
    return {"metric": f"cost / 1,000 at {r1:g}:1", "parent": served_new["cost_per_1000"], "child": new_new["cost_per_1000"],
            "delta": float(ra.mean() - rb.mean()), "ci": [float(np.quantile(ds, 0.025)), float(np.quantile(ds, 0.975))],
            "rows": int(n), "B": b, "same_rows": True,
            "ratio": {"served": r0, "alternative": r1}, "threshold": {"served": t0, "alternative": t1, "halves": halves,
                                                                       "bayes_alternative": c_fp / (c_fp + c_fn)},
            "operating": {"served_threshold": {"under_served_ratio": served_old, "under_alternative": served_new},
                          "alternative_threshold": {"under_served_ratio": new_old, "under_alternative": new_new}},
            "sign": f"Δ = cost at the served threshold − cost at the {r1:g}:1 threshold, both under {r1:g}:1 (positive: confirming the ratio is worth it)",
            "source": f"the run's holdout predictions, no retrain; the {r1:g}:1 threshold is chosen on one half of the holdout and "
                      "applied to the other (cross-fitted), so Δ is out of sample"}


def _scores(rec: dict[str, Any]) -> tuple[pd.DataFrame | None, str]:
    p = Path(rec["config"]["run_dir"]) / "holdout_predictions.csv"
    if not p.exists():
        return None, ""
    hp = pd.read_csv(p, dtype={"y_true": str, "y_pred": str})
    return hp, (rec.get("result") or {}).get("problem_type") or ""


def _metric(pt: str, cost: dict[str, Any] | None):
    """(label, row-level scorer on a frame → higher-is-better float, natural(score))."""
    from ..engine.scorecard import auc

    if pt == "binary" and cost and cost.get("kind") == "binary":
        return "cost / 1,000", None, (lambda s: -s)
    if pt == "binary":
        return "AUC", lambda y, p: float(auc(y, p)), (lambda s: s)
    if pt == "multiclass":
        return "accuracy", None, (lambda s: s)
    return "RMSE", None, (lambda s: -s)


def _roots(rec: dict[str, Any]) -> np.ndarray:
    """Row i of the run's dataset is row roots[i] of the root dataset (D1 / D2 versions keep `root_rows` in lineage)."""
    csv = Path(rec["config"]["csv_path"])
    lp = csv.parent / "lineage.json"
    rr = json.loads(lp.read_text()).get("root_rows") if lp.exists() else None
    if rr:
        return np.asarray(rr, dtype=int)
    with open(csv, "rb") as f:
        n = sum(1 for _ in f) - 1
    return np.arange(max(n, 0) + 1)


def delta(parent: dict[str, Any], child: dict[str, Any], b: int = 1000) -> dict[str, Any] | None:
    """Δ = score(child) − score(parent), better is positive, paired over the shared holdout rows."""
    from ensemble_models.cost import binary_row_costs

    hp_a, pt = _scores(parent)
    hp_b, _ = _scores(child)
    if hp_a is None or hp_b is None:
        return None
    if parent.get("dataset_id") != child.get("dataset_id"):  # a dataset version: pair the rows through the root rows
        hp_a = hp_a.assign(row=_roots(parent)[hp_a["row"].to_numpy()])
        hp_b = hp_b.assign(row=_roots(child)[hp_b["row"].to_numpy()])
    rows = np.intersect1d(hp_a["row"].to_numpy(), hp_b["row"].to_numpy())
    if len(rows) < 30:
        return None
    a = hp_a.set_index("row").loc[rows]
    c = hp_b.set_index("row").loc[rows]
    res = parent.get("result") or {}
    cost = parent["config"].get("cost")
    label, auc_fn, nat = _metric(pt, cost)
    pos = str(res.get("positive_class"))
    n = len(rows)
    if pt == "binary":
        y = (a["y_true"].astype(str) == pos).to_numpy().astype(int)
        if cost and cost.get("kind") == "binary":
            ta = float(((res.get("threshold") or {}).get("value", 0.5)))
            tb = float((((child.get("result") or {}).get("threshold") or {}).get("value", 0.5)))
            ra = 1000 * binary_row_costs(y, a[f"p_{pos}"].to_numpy() > ta, cost["c_fp"], cost["c_fn"], cost.get("g_tp") or 0)
            rb = 1000 * binary_row_costs(y, c[f"p_{pos}"].to_numpy() > tb, cost["c_fp"], cost["c_fn"], cost.get("g_tp") or 0)
            fa = lambda i: -float(ra[i].mean())  # noqa: E731
            fb = lambda i: -float(rb[i].mean())  # noqa: E731
        else:
            pa, pb = a[f"p_{pos}"].to_numpy(), c[f"p_{pos}"].to_numpy()
            fa = lambda i: auc_fn(y[i], pa[i])  # noqa: E731
            fb = lambda i: auc_fn(y[i], pb[i])  # noqa: E731
    elif pt == "multiclass":
        ea = (a["y_true"].astype(str) == a["y_pred"].astype(str)).to_numpy().astype(float)
        eb = (c["y_true"].astype(str) == c["y_pred"].astype(str)).to_numpy().astype(float)
        y = ea
        fa = lambda i: float(ea[i].mean())  # noqa: E731
        fb = lambda i: float(eb[i].mean())  # noqa: E731
    else:
        yt = pd.to_numeric(a["y_true"], errors="coerce").to_numpy()
        sa = (yt - pd.to_numeric(a["y_pred"], errors="coerce").to_numpy()) ** 2
        sb = (yt - pd.to_numeric(c["y_pred"], errors="coerce").to_numpy()) ** 2
        y = sa
        fa = lambda i: -float(np.sqrt(sa[i].mean()))  # noqa: E731
        fb = lambda i: -float(np.sqrt(sb[i].mean()))  # noqa: E731
    allr = np.arange(n)
    s_a, s_b = fa(allr), fb(allr)
    rng = np.random.default_rng(0)
    ds = []
    for _ in range(b):
        i = rng.integers(0, n, n)
        if pt == "binary" and not cost and y[i].min() == y[i].max():
            continue
        ds.append(fb(i) - fa(i))
    ds = np.asarray(ds)
    return {"metric": label, "parent": nat(s_a), "child": nat(s_b), "delta": float(s_b - s_a),
            "ci": [float(np.quantile(ds, 0.025)), float(np.quantile(ds, 0.975))], "rows": int(n), "B": int(len(ds)),
            "same_rows": bool(len(rows) == len(hp_a) == len(hp_b)),
            "sign": "better is positive" + (" (a cost or an error: Δ is the reduction)" if label in ("cost / 1,000", "RMSE") else "")}


def list_for(store, rec: dict[str, Any]) -> list[dict[str, Any]]:
    """The run's experiments (newest first), each with its status and, once it succeeded, Δ: child runs, plus the
    instant ones and G5 segment runs recorded in the run's result (`experiments_extra`)."""
    out = []
    fresh = store.get(rec["id"]) or rec
    for e in ((fresh.get("result") or {}).get("experiments_extra") or []):
        item = dict(e)
        if e.get("kind") == "segment" and e.get("run_id") and not e.get("result"):
            r = store.get(e["run_id"]) or {}
            item["status"] = r.get("status")
            if r.get("status") == "succeeded":
                try:
                    item["result"] = segment_result(r)
                except Exception as ex:  # noqa: BLE001
                    item["error"] = f"{type(ex).__name__}: {ex}"[:200]
            elif r.get("status") == "failed":
                item["error"] = r.get("error")
        out.append(item)
    for r in store.list():
        cfg = r.get("config") or {}
        if cfg.get("parent_run_id") != rec["id"] or cfg.get("source") != "doctor-experiment":
            continue
        ex = ((cfg.get("patch") or {}).get("experiment")) or {}
        full = store.get(r["id"]) if r.get("status") == "succeeded" else r
        item = {"run_id": r["id"], "name": r.get("name"), "status": r.get("status"), "kind": ex.get("kind"),
                "columns": ex.get("columns"), "title": (cfg.get("patch") or {}).get("title"), "created_at": r.get("created_at"),
                "spec": {k: v for k, v in ex.items() if k != "prepared"}, "prepared": ex.get("prepared"),
                "dataset_id": r.get("dataset_id")}
        if r.get("status") == "failed":
            item["error"] = (r.get("error") or "")[:300]
        if r.get("status") == "succeeded":
            try:
                item["result"] = delta(rec, full)
            except Exception as e:  # noqa: BLE001
                item["error"] = f"{type(e).__name__}: {e}"[:200]
        out.append(item)
    out.sort(key=lambda x: x.get("created_at") or "", reverse=True)
    return out


def segment_result(seg: dict[str, Any]) -> dict[str, Any]:
    """G5's own paired comparison on its holdout: the routed per-segment models against its global model."""
    res = seg.get("result") or {}
    ov = res.get("overall") or {}
    if "delta_auc" in ov:
        d = ov["delta_auc"]
        return {"metric": "AUC", "parent": (ov.get("global") or {}).get("auc"), "child": (ov.get("routed") or {}).get("auc"),
                "delta": float(d["delta"]), "ci": [float(d["ci"][0]), float(d["ci"][1])], "rows": ov.get("n"),
                "verdict_g5": ov.get("verdict"), "same_rows": True, "sign": "better is positive",
                "versus": "G5's global model (same config, same holdout); DeLong paired CI"}
    d = ov.get("delta_mse") or ov.get("delta_log_loss")
    if not d:
        raise ValueError("the segmented run has no overall comparison")
    return {"metric": "MSE" if "delta_mse" in ov else "log loss", "delta": -float(d["delta"]), "ci": [-float(d["ci"][1]), -float(d["ci"][0])],
            "rows": ov.get("n"), "verdict_g5": ov.get("verdict"), "same_rows": True,
            "sign": "better is positive (Δ is the reduction)", "versus": "G5's global model (same config, same holdout)"}


def jsonable(o: Any) -> Any:
    return json.loads(json.dumps(o, default=lambda v: v.item() if hasattr(v, "item") else str(v)))
