"""Vela's findings, computed from a finished run (no model involved): each states its numbers and offers one patch,
a concrete change to the run's Gym config (the `POST /api/runs` body) that the Gym applies and retrains.

Findings (each only when its condition holds):
* overfit      validation vs holdout gap of the best model, against the holdout's bootstrap standard error
* weak_class   the class with the lowest holdout recall (classification)
* segment      the worst segment of a categorical column (holdout error or error rate by level, n ≥ 30)
* imbalance    minority share and the threshold in use; F₁ at t = 0.5 vs the best t on the holdout grid
* leakage      a feature that alone nearly predicts the label (single-feature tree on train → holdout) and holds
               most of the permutation importance, or an ID-like column used as a feature
* budget       the fit used ≥ 95% of τ, or AutoGluon skipped models / stack levels for lack of time
* columns      constant or very high-cardinality category columns kept as features
* drift        a scored batch of a version built from this run has a feature with PSI > 0.25
"""

from __future__ import annotations

import copy
import json
import re
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

ID_NAME = re.compile(r"(^id$|_id$|^id_|uuid|^key$|_key$|^row$|^index$)", re.I)


def _set_path(body: dict[str, Any], path: str, value: Any) -> None:
    cur = body
    parts = path.split(".")
    for p in parts[:-1]:
        if not isinstance(cur.get(p), dict):
            cur[p] = {}
        cur = cur[p]
    if value is None:
        cur.pop(parts[-1], None)
    else:
        cur[parts[-1]] = value


def _get_path(body: dict[str, Any], path: str) -> Any:
    cur: Any = body
    for p in path.split("."):
        if not isinstance(cur, dict) or p not in cur:
            return None
        cur = cur[p]
    return cur


def make_patch(base: dict[str, Any], pid: str, title: str, changes: list[tuple[str, Any, str]]) -> dict[str, Any]:
    """changes: (path, new value, plain label). Returns the patch with from → to per change and the patched body."""
    body = copy.deepcopy(base)
    out = []
    for path, to, label in changes:
        frm = copy.deepcopy(_get_path(body, path))
        _set_path(body, path, to)
        out.append({"path": path, "from": frm, "to": to, "label": label})
    return {"id": pid, "title": title, "changes": out, "body": body}


def _fmt(v: Any) -> str:
    return json.dumps(v, ensure_ascii=False) if not isinstance(v, str) else repr(v)


def _metric(res: dict[str, Any]):
    from autogluon.core.metrics import get_metric

    return get_metric(res["eval_metric"]["name"], res["problem_type"])


def _score(metric, y_true: np.ndarray, y_pred: np.ndarray, proba: pd.DataFrame | None, positive: Any) -> float:
    if getattr(metric, "needs_pred", True) and not (getattr(metric, "needs_proba", False) or getattr(metric, "needs_threshold", False)):
        return float(metric(y_true, y_pred))
    if proba is None:
        return float(metric(y_true, y_pred))
    if proba.shape[1] == 2 and positive is not None:
        return float(metric(y_true, proba[f"p_{positive}"].to_numpy()))
    return float(metric(y_true, proba.to_numpy()))


def holdout_frame(rec: dict[str, Any]) -> pd.DataFrame | None:
    p = Path(rec["config"]["run_dir"]) / "holdout_predictions.csv"
    if not p.exists():
        return None
    return pd.read_csv(p, dtype={"y_true": str, "y_pred": str}) if (rec.get("result") or {}).get("problem_type") in ("binary", "multiclass") \
        else pd.read_csv(p)


def bootstrap_se(rec: dict[str, Any], hp: pd.DataFrame, B: int = 200) -> float | None:
    res = rec["result"]
    if rec["config"].get("cost"):  # F7: the cost metric is not in AutoGluon's registry; bootstrap the per-row costs
        from ..engine import costs

        try:
            rows = costs.row_costs(hp, rec["config"]["cost"], positive=res.get("positive_class"),
                                   t=float((res.get("threshold") or {}).get("value", 0.5)))
        except Exception:  # noqa: BLE001
            return None
        idx = np.random.default_rng(0).integers(0, len(rows), size=(B, len(rows)))
        return float(np.std(rows[idx].mean(axis=1), ddof=1))
    try:
        metric = _metric(res)
    except Exception:  # noqa: BLE001
        return None
    pcols = [c for c in hp.columns if c.startswith("p_")]
    rng = np.random.default_rng(0)
    vals = []
    n = len(hp)
    pos = res.get("positive_class")
    for _ in range(B):
        idx = rng.integers(0, n, n)
        s = hp.iloc[idx]
        try:
            vals.append(_score(metric, s["y_true"].to_numpy(), s["y_pred"].to_numpy(), s[pcols] if pcols else None, pos))
        except Exception:  # noqa: BLE001 — a resample with one class
            continue
    if len(vals) < 20:
        return None
    # AutoGluon scores are higher-is-better: error metrics are negated in score_*; the SE is the same either way.
    return float(np.std(vals, ddof=1))


def _best_row(res: dict[str, Any]) -> dict[str, Any] | None:
    lb = res.get("leaderboard") or []
    return next((r for r in lb if r.get("model") == res.get("best_model")), lb[0] if lb else None)


def _zoo(base_hp: Any) -> dict[str, Any]:
    from autogluon.tabular.configs.hyperparameter_configs import get_hyperparameter_config

    if isinstance(base_hp, dict):
        return copy.deepcopy(base_hp)
    return copy.deepcopy(get_hyperparameter_config(base_hp if isinstance(base_hp, str) else "default"))


def _with_params(hp: dict[str, Any], fam: str, params: dict[str, Any]) -> dict[str, Any]:
    v = hp.get(fam, {})
    if isinstance(v, list):
        hp[fam] = [{**(c or {}), **params} for c in v]
    else:
        hp[fam] = {**(v or {}), **params}
    return hp


# --------------------------------------------------------------------------- the findings


def f_overfit(rec, base, hp, out):
    res = rec["result"]
    best = _best_row(res)
    if not best or best.get("score_val") is None or best.get("score_test") is None or hp is None or len(hp) < 30:
        return
    gap = float(best["score_val"]) - float(best["score_test"])
    se = bootstrap_se(rec, hp)
    sd = float(best.get("val_score_std") or 0.0)
    if se is None and sd == 0.0:  # no noise estimate at all: a gap/noise ratio would be meaningless
        return
    se = se or 0.0
    noise = max(se, sd, 1e-9)
    z = gap / noise
    if gap <= 0 or z < 2.0:
        return
    k = (res.get("resolved") or {}).get("fit_kwargs", {}).get("num_bag_folds")
    bagged = bool((res.get("oof") or {}).get("available"))
    fam = best.get("family")
    if not bagged:
        patch = make_patch(base, "overfit", "Validate out-of-fold", [
            ("fit_kwargs.num_bag_folds", 5, "bag 5 folds so models are chosen on out-of-fold predictions, not one small split")])
    else:
        hpz = _zoo((base.get("fit_kwargs") or {}).get("hyperparameters") or ((res.get("resolved") or {}).get("fit_kwargs") or {}).get("hyperparameters"))
        reg = {"GBM": {"min_data_in_leaf": 40, "lambda_l2": 1.0}, "CAT": {"l2_leaf_reg": 10.0}, "XGB": {"reg_lambda": 10.0, "min_child_weight": 4.0},
               "RF": {"min_samples_leaf": 5}, "XT": {"min_samples_leaf": 5}, "NN_TORCH": {"weight_decay": 1e-4, "dropout_prob": 0.3}}
        target = next((f for f in [fam] + list(reg) if f in reg and f in hpz), None)
        if target:
            patch = make_patch(base, "overfit", f"Regularise {target}", [
                ("fit_kwargs.hyperparameters", _with_params(hpz, target, reg[target]), f"stronger regularisation on {target}: "
                 + ", ".join(f"{k}={v}" for k, v in reg[target].items()))])
        else:
            patch = make_patch(base, "overfit", "Repeat the bagging", [("fit_kwargs.num_bag_sets", 2, "a second set of k folds averages out split noise")])
    out.append({
        "id": "overfit", "kind": "overfit", "severity": "warning" if z < 4 else "critical",
        "title": "Validation is optimistic",
        "numbers": {"score_val": best["score_val"], "score_test": best["score_test"], "gap": gap, "se": se, "fold_sd": sd, "z": z,
                    "model": best["model"], "k": k},
        "text": (f"{best['model']} scores {best['score_val']:.4f} on validation but {best['score_test']:.4f} on the holdout: a gap "
                 f"of {gap:.4f}, {z:.1f}× the noise (holdout bootstrap SE {se:.4f}{f', fold σ {sd:.4f}' if sd else ''})."),
        "formula": "z = (s_val − s_holdout) / max(SE_boot, σ_folds)",
        "patch": patch,
    })


def f_weak_class(rec, base, hp, out):
    res = rec["result"]
    h = res.get("holdout") or {}
    cm, classes = h.get("confusion_matrix"), h.get("classes")
    if res.get("problem_type") not in ("binary", "multiclass") or not cm or not classes:
        return
    cm = np.asarray(cm, dtype=float)
    support = cm.sum(axis=1)
    recall = np.divide(np.diag(cm), support, out=np.zeros(len(classes)), where=support > 0)
    i = int(np.argmin(recall))
    macro = float(recall[support > 0].mean())
    if macro - recall[i] < 0.05:
        return
    confused = int(np.argmax(np.where(np.arange(len(classes)) == i, -1, cm[i])))
    pk = base.get("predictor_kwargs") or {}
    if res["problem_type"] == "binary" and not base.get("calibrate_threshold") and (res.get("threshold") or {}).get("source") in (None, "default"):
        patch = make_patch(base, "weak_class", "Calibrate for balanced accuracy", [
            ("calibrate_threshold", "balanced_accuracy", "choose t on validation to maximise ½(TPR + TNR)")])
    elif pk.get("sample_weight") != "balance_weight":
        patch = make_patch(base, "weak_class", "Weight classes by frequency", [
            ("predictor_kwargs.sample_weight", "balance_weight", "wᵢ ∝ n / (K · n_{yᵢ}): each class carries equal total weight")])
    else:
        patch = make_patch(base, "weak_class", "More bagging", [("fit_kwargs.num_bag_sets", 2, "average a second set of folds")])
    out.append({
        "id": "weak_class", "kind": "weak_class", "severity": "warning",
        "title": f"Class '{classes[i]}' is the weakest",
        "numbers": {"class": classes[i], "recall": float(recall[i]), "macro_recall": macro, "support": int(support[i]),
                    "confused_with": classes[confused], "confused_n": int(cm[i][confused])},
        "text": (f"Holdout recall on '{classes[i]}' is {recall[i]:.3f} against a macro mean of {macro:.3f} "
                 f"(n = {int(support[i])}); {int(cm[i][confused])} of them were predicted '{classes[confused]}'."),
        "formula": "r_c = TP_c / (TP_c + FN_c)",
        "patch": patch,
    })


def f_segment(rec, base, hp, data, out):
    res = rec["result"]
    if hp is None or data is None:
        return
    label = rec["label"]
    feats = set(res.get("features") or [])
    cats = [c for c in data.columns if c in feats and c != label and (data[c].dtype == object) and 2 <= data[c].nunique() <= 30]
    if not cats:
        return
    rows = hp["row"].to_numpy()
    reg = res["problem_type"] in ("regression", "quantile")
    if reg:
        err = np.abs(pd.to_numeric(hp["y_true"], errors="coerce") - pd.to_numeric(hp["y_pred"], errors="coerce")).to_numpy()
        what = "MAE"
    else:
        err = (hp["y_true"].astype(str).to_numpy() != hp["y_pred"].astype(str).to_numpy()).astype(float)
        what = "error rate"
    overall = float(np.nanmean(err))
    if overall <= 0:
        return
    worst = None
    for c in cats:
        lv = data[c].iloc[rows].astype(str).to_numpy()
        for v in pd.unique(lv):
            m = lv == v
            if m.sum() < 30:
                continue
            e = float(np.nanmean(err[m]))
            ratio = e / overall
            if worst is None or ratio > worst[3]:
                worst = (c, v, int(m.sum()), ratio, e)
    if not worst or worst[3] < 1.3:
        return
    c, v, n, ratio, e = worst
    presets = base.get("presets")
    if presets in (None, "medium_quality"):
        patch = make_patch(base, "segment", "Stack for interactions", [("presets", "high_quality", "bagged + stacked models capture segment interactions")])
    else:
        patch = make_patch(base, "segment", "Longer budget", [("time_limit", float(min(3600, (base.get("time_limit") or 120) * 2)), "twice the time budget τ")])
    out.append({
        "id": "segment", "kind": "segment", "severity": "info" if ratio < 1.6 else "warning",
        "title": f"Worst segment: {c} = {v}",
        "numbers": {"column": c, "value": v, "n": n, "segment_error": e, "overall_error": overall, "ratio": ratio, "measure": what},
        "text": f"On the holdout, {c} = {v} (n = {n}) has a {what} of {e:.4g}, {ratio:.2f}× the overall {overall:.4g}.",
        "formula": f"ratio = {what}(segment) / {what}(all)",
        "patch": patch,
    })


def f_imbalance(rec, base, hp, data, out):
    res = rec["result"]
    pt = res.get("problem_type")
    if pt not in ("binary", "multiclass") or data is None:
        return
    y = data[rec["label"]].dropna().astype(str)
    vc = y.value_counts()
    share = float(vc.min() / vc.sum())
    pk = base.get("predictor_kwargs") or {}
    if pt == "binary":
        if share >= 0.2:
            return
        thr = res.get("threshold") or {}
        t = float(thr.get("value", 0.5))
        pos = res.get("positive_class")
        f1_05 = f1_best = t_best = None
        if hp is not None and f"p_{pos}" in hp.columns:
            yt = (hp["y_true"].astype(str) == str(pos)).to_numpy()
            p = hp[f"p_{pos}"].to_numpy()
            grid = np.round(np.arange(0.005, 1.0, 0.005), 3)

            def f1(tt):
                pred = p > tt
                tp = (pred & yt).sum()
                fp = (pred & ~yt).sum()
                fn = (~pred & yt).sum()
                return 2 * tp / max(1, 2 * tp + fp + fn)
            f1s = np.array([f1(g) for g in grid])
            f1_05, t_best, f1_best = float(f1(0.5)), float(grid[int(np.argmax(f1s))]), float(f1s.max())
        calibrated = thr.get("source") not in (None, "default")
        if not calibrated:
            patch = make_patch(base, "imbalance", "Calibrate the threshold for F₁", [("calibrate_threshold", "f1", "pick t on validation data to maximise F₁")])
            text = (f"The minority class '{vc.idxmin()}' is {share:.1%} of rows and the run predicts at t = {t:.3f}. "
                    + (f"On the holdout F₁ is {f1_05:.3f} at t = 0.5 but {f1_best:.3f} at t = {t_best:.3f} (an upper bound: t chosen on these rows)." if f1_best is not None else ""))
        elif pk.get("sample_weight") != "balance_weight":
            patch = make_patch(base, "imbalance", "Weight the minority up", [("predictor_kwargs.sample_weight", "balance_weight", "equal total weight per class while fitting")])
            text = (f"The minority class '{vc.idxmin()}' is {share:.1%} of rows; t = {t:.3f} is already calibrated "
                    f"({thr.get('metric')}). Weighting the classes while fitting is the other lever.")
        else:
            return
        out.append({"id": "imbalance", "kind": "imbalance", "severity": "warning" if not calibrated else "info",
                    "title": "Imbalanced label", "numbers": {"minority": vc.idxmin(), "share": share, "t": t, "f1_at_05": f1_05,
                                                            "f1_best": f1_best, "t_best": t_best, "calibrated": calibrated},
                    "text": text, "formula": "F₁(t) = 2TP / (2TP + FP + FN),  ŷ = 1[p̂ > t]", "patch": patch})
    else:
        ratio = float(vc.max() / vc.min())
        if ratio < 5 or pk.get("sample_weight") == "balance_weight":
            return
        out.append({"id": "imbalance", "kind": "imbalance", "severity": "info", "title": "Imbalanced classes",
                    "numbers": {"ratio": ratio, "largest": vc.idxmax(), "smallest": vc.idxmin(), "share": share},
                    "text": f"'{vc.idxmax()}' has {ratio:.1f}× the rows of '{vc.idxmin()}' ({share:.1%} of rows).",
                    "formula": "ratio = n_max / n_min",
                    "patch": make_patch(base, "imbalance", "Weight classes by frequency", [
                        ("predictor_kwargs.sample_weight", "balance_weight", "wᵢ ∝ n / (K · n_{yᵢ})")])})


def _single_feature_score(data: pd.DataFrame, rows_train: np.ndarray, rows_hold: np.ndarray, col: str, label: str, pt: str) -> float | None:
    from sklearn.metrics import r2_score, roc_auc_score
    from sklearn.tree import DecisionTreeClassifier, DecisionTreeRegressor

    x = data[col]
    if x.dtype == object:
        codes, _ = pd.factorize(x.astype(str))
        X = codes.reshape(-1, 1).astype(float)
    else:
        X = pd.to_numeric(x, errors="coerce").fillna(-9e9).to_numpy().reshape(-1, 1)
    y = data[label]
    ok_tr = rows_train[y.iloc[rows_train].notna().to_numpy()]
    ok_ho = rows_hold[y.iloc[rows_hold].notna().to_numpy()]
    if len(ok_tr) < 20 or len(ok_ho) < 10:
        return None
    if pt in ("regression", "quantile"):
        m = DecisionTreeRegressor(max_depth=6, random_state=0).fit(X[ok_tr], y.iloc[ok_tr].astype(float))
        return float(r2_score(y.iloc[ok_ho].astype(float), m.predict(X[ok_ho])))
    ys = y.astype(str)
    m = DecisionTreeClassifier(max_depth=6, random_state=0).fit(X[ok_tr], ys.iloc[ok_tr])
    if pt == "binary" and ys.iloc[ok_ho].nunique() == 2:
        pr = m.predict_proba(X[ok_ho])
        pos_i = list(m.classes_).index(sorted(ys.iloc[ok_ho].unique())[1]) if len(m.classes_) == 2 else 0
        auc = roc_auc_score((ys.iloc[ok_ho] == m.classes_[pos_i]).astype(int), pr[:, pos_i])
        return float(max(auc, 1 - auc))
    return float((m.predict(X[ok_ho]) == ys.iloc[ok_ho].to_numpy()).mean())


def f_leakage(rec, base, data, out):
    res = rec["result"]
    if data is None:
        return
    label = rec["label"]
    feats = [f for f in res.get("features") or [] if f in data.columns]
    fi = res.get("feature_importance") or []
    imp = {r["feature"]: float(r.get("importance") or 0) for r in fi if r.get("feature")}
    pos_total = sum(v for v in imp.values() if v > 0) or 0.0
    splits = json.loads(Path(rec["config"]["splits_path"]).read_text())
    is_h = np.zeros(len(data), dtype=bool)
    is_h[np.asarray(splits["holdout"], dtype=int)] = True
    rows_tr, rows_ho = np.flatnonzero(~is_h), np.flatnonzero(is_h)
    pt = res.get("problem_type")
    suspects = []
    ranked = sorted(imp.items(), key=lambda kv: -kv[1])[:3] if imp else [(f, 0.0) for f in feats[:0]]
    for f, v in ranked:
        share = v / pos_total if pos_total > 0 else 0.0
        if share < 0.5 or f not in data.columns:
            continue
        s = _single_feature_score(data, rows_tr, rows_ho, f, label, pt)
        floor = 0.97 if pt != "multiclass" else 0.95
        if s is not None and s >= floor:
            suspects.append({"feature": f, "reason": "near-perfect alone", "importance": v, "share": share, "single_score": s,
                             "single_measure": "R²" if pt in ("regression", "quantile") else ("AUC" if pt == "binary" else "accuracy")})
    for f in feats:
        if any(s["feature"] == f for s in suspects):
            continue
        col = data[f]
        nn = col.dropna()
        uniq = nn.nunique() / max(1, len(nn))
        looks_id = bool(ID_NAME.search(f)) and uniq > 0.9
        if not looks_id and uniq > 0.98 and len(nn) > 50:
            if col.dtype == object and nn.astype(str).str.len().mean() < 40 and nn.astype(str).str.count(r"\s").mean() < 1:
                looks_id = True
            elif col.dtype != object and pd.api.types.is_integer_dtype(col) and (nn.sort_values().diff().dropna() == 1).mean() > 0.95:
                looks_id = True
        if looks_id:
            suspects.append({"feature": f, "reason": "identifier", "importance": imp.get(f), "share": (imp.get(f, 0) / pos_total) if pos_total else None,
                             "unique_share": float(uniq)})
    if not suspects:
        return
    top = suspects[0]
    drop = list(dict.fromkeys(list(base.get("drop_columns") or []) + [s["feature"] for s in suspects]))
    if top["reason"] == "identifier":
        text = (f"'{top['feature']}' looks like an identifier ({top['unique_share']:.1%} distinct values) and is used as a feature; "
                "IDs memorise rows and do not generalise to new ones.")
    else:
        text = (f"'{top['feature']}' alone predicts the label with {top['single_measure']} {top['single_score']:.3f} on the holdout "
                f"(a depth-6 tree on that one column) and carries {top['share']:.0%} of the permutation importance. "
                "A column this predictive is usually recorded after the outcome.")
    out.append({"id": "leakage", "kind": "leakage", "severity": "critical", "title": f"Leakage suspect: {top['feature']}",
                "numbers": {"suspects": suspects}, "text": text,
                "formula": "s_j = I_j / Σ_k max(I_k, 0),  I = permutation importance",
                "patch": make_patch(base, "leakage", f"Drop {', '.join(s['feature'] for s in suspects)}", [
                    ("drop_columns", drop, "leave the suspect column(s) out of training")])})


def f_budget(rec, base, events, out):
    res = rec["result"]
    cfg = rec["config"]
    tau = ((res.get("resolved") or {}).get("fit_kwargs") or {}).get("time_limit") or cfg.get("time_limit")
    if not tau:
        return
    fit = float(res.get("fit_seconds") or 0)
    skipped = [e for e in events if e.get("type") == "model_failed" and e.get("skipped")]
    time_logs = [e for e in events if e.get("type") == "log" and re.search(r"(not enough time|time limit|Time limit exceeded|skipping)", e.get("message", ""), re.I)]
    used = fit / float(tau)
    lb = res.get("leaderboard") or []
    max_level = max((int(r.get("stack_level") or 1) for r in lb), default=1)
    planned = (res.get("resolved") or {}).get("fit_kwargs", {}).get("num_stack_levels")
    short_stack = planned is not None and max_level - 1 < int(planned)
    if used < 0.95 and not skipped and not short_stack:
        return
    new_tau = float(min(4 * 3600, round(tau * 2)))
    out.append({"id": "budget", "kind": "budget", "severity": "warning",
                "title": "Time budget ran out",
                "numbers": {"fit_seconds": fit, "tau": tau, "used": used, "skipped": len(skipped), "time_logs": len(time_logs),
                            "stack_levels_fitted": max_level - 1, "stack_levels_planned": planned},
                "text": (f"The fit took {fit:.0f} s of τ = {tau:.0f} s ({used:.0%})"
                         + (f"; AutoGluon skipped {len(skipped)} model(s) for lack of time" if skipped else "")
                         + (f"; {max_level - 1} of {planned} stack level(s) were fitted" if short_stack else "") + "."),
                "formula": "used = t_fit / τ",
                "patch": make_patch(base, "budget", "Double the budget", [("time_limit", new_tau, f"τ = {tau:.0f} s → {new_tau:.0f} s")])})


def f_columns(rec, base, ds, out):
    res = rec["result"]
    if not ds:
        return
    feats = set(res.get("features") or [])
    rows = ds["profile"].get("rows") or 0
    const, high = [], []
    for c in ds["profile"]["columns"]:
        if c["name"] not in feats:
            continue
        if c.get("unique", 0) <= 1:
            const.append(c["name"])
        elif c["type"] == "category" and (c["unique"] > 1000 or (rows and c["unique"] / rows > 0.5)):
            high.append((c["name"], c["unique"]))
    if not const and not high:
        return
    drop = list(dict.fromkeys(list(base.get("drop_columns") or []) + const + [h[0] for h in high]))
    parts = []
    if const:
        parts.append(f"{len(const)} constant column(s) ({', '.join(const[:4])}) carry no information")
    if high:
        parts.append("; ".join(f"'{n}' has {u:,} distinct categories ({u / rows:.0%} of rows)" for n, u in high[:3]))
    out.append({"id": "columns", "kind": "columns", "severity": "info", "title": "Columns to drop",
                "numbers": {"constant": const, "high_cardinality": [{"column": n, "unique": u} for n, u in high], "rows": rows},
                "text": "; ".join(parts) + ".", "formula": "cardinality share = |unique(x_j)| / n",
                "patch": make_patch(base, "columns", "Drop them", [("drop_columns", drop, "leave them out of training")])})


def f_drift(rec, base, batches, out):
    worst = None
    for b in batches:
        for f in ((b.get("drift") or {}).get("features") or []):
            if f.get("psi") is not None and (worst is None or f["psi"] > worst[1]["psi"]):
                worst = (b, f)
    if not worst or worst[1]["psi"] <= 0.25:
        return
    b, f = worst
    shifted = sorted({x["feature"] for bb in batches for x in ((bb.get("drift") or {}).get("features") or []) if (x.get("psi") or 0) > 0.25})
    out.append({"id": "drift", "kind": "drift", "severity": "warning", "title": f"Drift on {f['feature']}",
                "numbers": {"feature": f["feature"], "psi": f["psi"], "batch": b.get("id"), "version": b.get("version"),
                            "n_rows": b.get("n_rows"), "shifted": shifted,
                            "output_psi": ((b.get("drift") or {}).get("prediction") or {}).get("psi")},
                "text": (f"Batch {b.get('id')} scored with {b.get('version')} has PSI = {f['psi']:.3f} on '{f['feature']}' (> 0.25, shift); "
                         f"{len(shifted)} feature(s) shifted in all."),
                "formula": "PSI = Σᵢ (aᵢ − eᵢ) · ln(aᵢ / eᵢ)",
                "patch": make_patch(base, "drift", f"Retrain without {f['feature']}", [
                    ("drop_columns", list(dict.fromkeys(list(base.get("drop_columns") or []) + [f["feature"]])),
                     "a model that does not lean on the shifted column")])})


ORDER = {"leakage": 0, "budget": 1, "overfit": 2, "imbalance": 3, "weak_class": 4, "segment": 5, "drift": 6, "columns": 7}


def compute(rec: dict[str, Any], base: dict[str, Any], events: list[dict[str, Any]], ds: dict[str, Any] | None,
            batches: list[dict[str, Any]]) -> dict[str, Any]:
    """All findings for a succeeded run, most severe first. Runs in a worker (bootstrap, single-feature trees)."""
    out: list[dict[str, Any]] = []
    hp = holdout_frame(rec)
    try:
        data = pd.read_csv(rec["config"]["csv_path"])
    except Exception:  # noqa: BLE001
        data = None
    steps = [lambda: f_leakage(rec, base, data, out), lambda: f_budget(rec, base, events, out),
             lambda: f_overfit(rec, base, hp, out), lambda: f_imbalance(rec, base, hp, data, out),
             lambda: f_weak_class(rec, base, hp, out), lambda: f_segment(rec, base, hp, data, out),
             lambda: f_drift(rec, base, batches, out), lambda: f_columns(rec, base, ds, out)]
    errors = []
    for s in steps:
        try:
            s()
        except Exception as e:  # noqa: BLE001 — one finding failing never hides the others
            errors.append(f"{type(e).__name__}: {e}")
    out.sort(key=lambda f: ORDER.get(f["kind"], 9))
    for f in out:
        f["computed_text"] = f["text"]
    return {"findings": out, "errors": errors}
