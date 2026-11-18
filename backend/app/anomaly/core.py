"""Unsupervised anomaly detection: scikit-learn IsolationForest (and optionally LocalOutlierFactor) on a table's
features, in the run's worker process.

Preprocessing (a scikit-learn Pipeline saved with the model):
- numbers: median imputation (+ a missing flag when a column has gaps), then for LOF a RobustScaler;
- categories: one-hot of the 20 most frequent values (rarer values share an "other" column), missing as its own value;
- ID columns are dropped; text columns are dropped (their length is not a transaction feature).

Isolation score (Liu, Ting & Zhou 2008): s(x, n) = 2^(−E[h(x)] / c(n)), with h(x) the path length of x in one
isolation tree, E over the trees, n the sub-sample size per tree and c(n) = 2H(n−1) − 2(n−1)/n the average path
length of an unsuccessful BST search (H the harmonic number, H(i) ≈ ln i + 0.5772). s → 1: isolated in few splits
(anomalous); s ≪ 0.5: normal. scikit-learn's `score_samples` is −s.

Threshold: the (1 − contamination) quantile of s on the fitted rows; the contamination slider moves it.
Reasons per flagged row: TreeSHAP on the forest's mean path length E[h(x)] (shap.TreeExplainer supports
IsolationForest), summed back from one-hot columns to the original feature, and the robust deviation of each number
z = (x − median) / (1.4826 · MAD) or the share of rows with the same category.
"""

from __future__ import annotations

import json
import math
import time
from pathlib import Path
from typing import Any, Callable

import numpy as np
import pandas as pd

from . import group, velocity

Emit = Callable[..., int]
EULER = 0.5772156649


class AnomalyError(ValueError):
    pass


def c_n(n: int) -> float:
    if n <= 1:
        return 1.0
    if n == 2:
        return 1.0
    return 2.0 * (math.log(n - 1) + EULER) - 2.0 * (n - 1) / n


def feature_columns(df: pd.DataFrame, profile: dict[str, Any] | None, features: list[str] | None,
                    drop: list[str] | None) -> tuple[list[str], list[str]]:
    types = {c["name"]: c["type"] for c in (profile or {}).get("columns", [])}
    cols = features or [c for c in df.columns if types.get(c) not in ("id", "text")]
    cols = [c for c in cols if c not in (drop or []) and c in df.columns]
    num = [c for c in cols if pd.api.types.is_numeric_dtype(df[c]) and types.get(c, "number") == "number"]
    cat = [c for c in cols if c not in num]
    if not cols:
        raise AnomalyError("no usable feature columns")
    return num, cat


def build_pipeline(num: list[str], cat: list[str], scale: bool):
    from sklearn.compose import ColumnTransformer
    from sklearn.impute import SimpleImputer
    from sklearn.pipeline import Pipeline
    from sklearn.preprocessing import OneHotEncoder, RobustScaler

    num_steps = [("impute", SimpleImputer(strategy="median", add_indicator=True))]
    if scale:
        num_steps.append(("scale", RobustScaler()))
    parts = []
    if num:
        parts.append(("num", Pipeline(num_steps), num))
    if cat:
        parts.append(("cat", Pipeline([("impute", SimpleImputer(strategy="constant", fill_value="(missing)")),
                                       ("onehot", OneHotEncoder(handle_unknown="infrequent_if_exist", max_categories=21,
                                                                sparse_output=False))]), cat))
    return ColumnTransformer(parts, verbose_feature_names_out=True)


def _prep_frame(df: pd.DataFrame, num: list[str], cat: list[str]) -> pd.DataFrame:
    X = df[num + cat].copy()
    for c in num:
        X[c] = pd.to_numeric(X[c], errors="coerce")
    for c in cat:
        X[c] = X[c].map(lambda v: v if pd.isna(v) else str(v)).astype(object)
    return X


def origin_map(names: list[str], num: list[str], cat: list[str]) -> list[str]:
    """Transformed column → original feature (num__amount → amount; cat__channel_atm → channel; missingindicator)."""
    out = []
    for n in names:
        base = n.split("__", 1)[1] if "__" in n else n
        if base.startswith("missingindicator_"):
            base = base[len("missingindicator_"):]
        hit = next((c for c in num if base == c), None)
        if hit is None:
            hit = next((c for c in sorted(cat, key=len, reverse=True) if base == c or base.startswith(c + "_")), base)
        out.append(hit)
    return out


def robust_stats(X: pd.DataFrame, num: list[str], cat: list[str]) -> dict[str, Any]:
    st: dict[str, Any] = {"num": {}, "cat": {}}
    for c in num:
        v = X[c].dropna().to_numpy(dtype=float)
        med = float(np.median(v)) if len(v) else 0.0
        mad = float(np.median(np.abs(v - med))) * 1.4826 if len(v) else 0.0
        if mad == 0:
            mad = float(np.std(v)) or 1.0
        st["num"][c] = {"median": med, "mad": mad, "p01": float(np.percentile(v, 1)) if len(v) else None,
                        "p99": float(np.percentile(v, 99)) if len(v) else None}
    for c in cat:
        vc = X[c].fillna("(missing)").astype(str).value_counts(normalize=True)
        st["cat"][c] = {str(k): float(v) for k, v in vc.items()}
    return st


def deviations(row: pd.Series, stats: dict[str, Any]) -> dict[str, dict[str, Any]]:
    out = {}
    for c, s in stats["num"].items():
        v = row.get(c)
        if v is None or pd.isna(v):
            out[c] = {"value": None, "z": None}
        else:
            out[c] = {"value": float(v), "z": float((float(v) - s["median"]) / s["mad"]), "median": s["median"]}
    for c, freq in stats["cat"].items():
        v = row.get(c)
        key = "(missing)" if v is None or pd.isna(v) else str(v)
        out[c] = {"value": None if key == "(missing)" else key, "share": float(freq.get(key, 0.0))}
    return out


class Model:
    """What is saved (joblib): the preprocessing, the forest, optional LOF, the threshold and the stats."""

    def __init__(self, **kw: Any) -> None:
        self.__dict__.update(kw)

    def augment(self, df: pd.DataFrame) -> pd.DataFrame:
        """Add the group-relative columns (against the training entities) when the model was fit with them."""
        spec = getattr(self, "group", None)
        if not spec or not spec.get("entity_column") or spec["entity_column"] not in df.columns:
            return df
        if not all(c in df.columns for c in group.names(spec)):  # else: the training frame, already augmented
            df = group.add(df, spec, self.group_stats)
        vspec = getattr(self, "velocity", None)
        if vspec and vspec.get("time_column") in df.columns:
            if not all(c in df.columns for c in velocity.names(vspec)):
                df = velocity.add(df, vspec, self.velocity_history)
        return df

    def transform(self, df: pd.DataFrame) -> np.ndarray:
        return self.pre.transform(_prep_frame(self.augment(df), self.num, self.cat))

    def score(self, df: pd.DataFrame) -> dict[str, np.ndarray]:
        df = self.augment(df)
        Z = self.pre.transform(_prep_frame(df, self.num, self.cat))
        s = -self.iforest.score_samples(Z)
        out = {"s": s, "Eh": -c_n(self.psi) * np.log2(np.maximum(s, 1e-12))}
        if getattr(self, "lof", None) is not None:
            Zs = self.pre_lof.transform(_prep_frame(df, self.num, self.cat))
            out["lof"] = -self.lof.score_samples(Zs)
        return out


def explain_rows(model: Model, df: pd.DataFrame, k_top: int = 6) -> list[dict[str, Any]]:
    """TreeSHAP on the forest's path length per row, grouped by original feature. φ < 0 shortens the path (more
    anomalous); 'push' = −φ / Σ|φ| is each feature's share of the push towards anomalous."""
    import shap

    df = model.augment(df)
    Z = model.pre.transform(_prep_frame(df, model.num, model.cat))
    try:
        ex = shap.TreeExplainer(model.iforest)
        phi = np.asarray(ex.shap_values(Z))
        base = float(np.ravel(ex.expected_value)[0])
        method = "TreeSHAP on E[h(x)] (shap.TreeExplainer)"
    except Exception as e:  # noqa: BLE001
        phi, base, method = None, None, f"robust deviation only (TreeSHAP failed: {e})"
    orig = model.origin
    X = _prep_frame(df, model.num, model.cat)
    out = []
    for i in range(len(df)):
        dev = deviations(X.iloc[i], model.stats)
        feats: dict[str, float] = {}
        if phi is not None:
            for j, o in enumerate(orig):
                feats[o] = feats.get(o, 0.0) + float(phi[i, j])
        else:
            for c, d in dev.items():
                feats[c] = -abs(d.get("z") or 0.0) if "z" in d else -(1 - (d.get("share") or 0.0))
        tot = sum(abs(v) for v in feats.values()) or 1.0
        reasons = []
        for c, v in sorted(feats.items(), key=lambda kv: kv[1])[:k_top]:
            d = dev.get(c, {})
            reasons.append({"feature": c, "phi": v, "push": -v / tot, "value": d.get("value"), "z": d.get("z"),
                            "median": d.get("median"), "share": d.get("share")})
        out.append({"phi0": base, "phi_sum": float(sum(feats.values())), "reasons": reasons, "method": method})
    return out


def _text(r: dict[str, Any]) -> str:
    v = r.get("value")
    if r.get("z") is not None:
        return f"{r['feature']} = {v:,.4g} (z = {r['z']:+.1f} vs median {r['median']:,.4g})"
    if r.get("share") is not None:
        return f"{r['feature']} = {v} (seen in {r['share']:.1%} of rows)"
    return f"{r['feature']} = {v}"


def precision_at_k(order: np.ndarray, truth: set[int], ks: list[int]) -> list[dict[str, Any]]:
    out = []
    for k in ks:
        if k <= 0:
            continue
        top = order[:k]
        hits = int(sum(1 for r in top if int(r) in truth))
        out.append({"k": int(k), "hits": hits, "precision": hits / k, "recall": hits / max(len(truth), 1)})
    return out


def truth_check(rows: np.ndarray, s: np.ndarray, truth: dict[str, Any], n_flagged: int) -> dict[str, Any]:
    from sklearn.metrics import average_precision_score, roc_auc_score

    t = set(int(r) for r in truth["rows"])
    y = np.array([1 if int(r) in t else 0 for r in rows])
    order = rows[np.argsort(-s, kind="stable")]
    kinds = dict(zip((int(r) for r in truth["rows"]), truth.get("kinds") or []))
    top = set(int(r) for r in order[:n_flagged])
    by_kind: dict[str, dict[str, int]] = {}
    for r, kd in kinds.items():
        d = by_kind.setdefault(kd, {"planted": 0, "found": 0})
        d["planted"] += 1
        d["found"] += int(r in top)
    ks = sorted(set([10, 25, 50, 100, len(t), n_flagged]))
    return {"planted": len(t), "precision_at_k": precision_at_k(order, t, ks),
            "roc_auc": float(roc_auc_score(y, s)) if 0 < y.sum() < len(y) else None,
            "average_precision": float(average_precision_score(y, s)) if y.sum() else None,
            "by_kind": by_kind, "flagged": n_flagged, "base_rate": float(y.mean()),
            "note": "sample only: the planted rows (anomaly_truth.json) are never given to the model"}


def run(cfg: dict[str, Any], run_dir: Path, emit: Emit, merge: Callable[..., Any]) -> dict[str, Any]:
    import joblib
    from sklearn.ensemble import IsolationForest
    from sklearn.neighbors import LocalOutlierFactor

    df = pd.read_csv(cfg["csv_path"])
    profile = cfg.get("profile")
    p = cfg["params"]
    # Group-relative features (amount ÷ the entity's own mean, z within the entity, time since its previous row)
    gspec = vspec = None
    aux: list[str] = []
    if p.get("group_features") and p.get("entity_column") in df.columns:
        gspec = {"entity_column": p["entity_column"],
                 "time_column": p.get("time_column") if p.get("time_column") in df.columns else None,
                 "relative_columns": [c for c in (p.get("relative_columns") or []) if c in df.columns]}
        vspec = None
        if p.get("velocity_features") and gspec["time_column"] and p.get("amount_column") in df.columns:
            vspec = {"entity_column": gspec["entity_column"], "time_column": gspec["time_column"],
                     "amount_column": p["amount_column"],
                     "category_column": p.get("category_column") if p.get("category_column") in df.columns else None}
            # velocity's log_hours_since_prev is the same Δt: one column per signal (see velocity.py)
            gspec["time_column"] = None
        df = group.add(df, gspec)
        added = group.names(gspec)
        aux: list[str] = []
        if vspec:
            df = velocity.add(df, vspec)
            vnames = velocity.names(vspec)
            in_forest = [c for c in (p.get("velocity_in_forest") or velocity.default_in_forest(vspec)) if c in vnames]
            aux = [c for c in vnames if c not in in_forest]
            added = added + in_forest
        feats = cfg.get("features")
        feats = [c for c in (feats or []) if c not in (gspec["entity_column"], gspec["time_column"])] + added if feats else None
        tcol = vspec["time_column"] if vspec else gspec["time_column"]
        feats = [c for c in feats if c != tcol] if feats else None
        drop = list(cfg.get("drop_columns") or []) + [gspec["entity_column"]] + ([tcol] if tcol else []) + aux
        num, cat = feature_columns(df, (profile or {}) and {"columns": (profile or {}).get("columns", []) + [{"name": c, "type": "number"} for c in added]}, feats, drop)
        emit("log", message=f"group-relative features on {gspec['entity_column']}: {', '.join(added)}")
        if vspec:
            emit("log", message=f"velocity features (past only) on {vspec['entity_column']} by {vspec['time_column']}: "
                                f"{', '.join(velocity.names(vspec))}; in the forest: {', '.join(in_forest)}")
    else:
        gspec = vspec = None
        aux = []
        num, cat = feature_columns(df, profile, cfg.get("features"), cfg.get("drop_columns"))
    X = _prep_frame(df, num, cat)
    info = {"rows": int(len(df)), "features": num + cat, "numeric": num, "categorical": cat,
            "dropped_columns": [c for c in df.columns if c not in num + cat]}
    emit("data_ready", **info)
    if gspec:
        info["group"] = {**gspec, "added": group.names(gspec),
                         "entities": int(df[gspec["entity_column"]].nunique()),
                         "rows_without_baseline": int(df[group.names(gspec)[0]].isna().sum()) if gspec["relative_columns"] else 0}
    if vspec:
        vn = velocity.names(vspec)
        info["velocity"] = {**vspec, "added": vn, "in_forest": [c for c in vn if c not in aux], "not_in_forest": aux,
                            "windows_hours": {w: h for w, h in velocity.WINDOWS},
                            "rows_without_history": int(df["log_hours_since_prev"].isna().sum()),
                            "summary": {c: {"mean": float(np.nanmean(df[c])), "p99": float(np.nanpercentile(df[c], 99)),
                                            "max": float(np.nanmax(df[c]))} for c in vn}}
    merge(data=info, features=num + cat)
    contamination = float(p.get("contamination", 0.015))
    pre = build_pipeline(num, cat, scale=False)
    Z = pre.fit_transform(X)
    names = list(pre.get_feature_names_out())
    ms = p.get("max_samples")
    ifk = {"n_estimators": int(p.get("n_estimators", 300)), "max_samples": min(1024, len(df)) if ms is None else ms,
           "max_features": float(p.get("max_features", 1.0)), "bootstrap": bool(p.get("bootstrap", False)),
           "contamination": "auto", "random_state": int(p.get("random_state", 0)), "n_jobs": -1,
           **(p.get("iforest_kwargs") or {})}
    resolved = {"preprocessing": {"numeric": "SimpleImputer(median, add_indicator) ", "categorical":
                                  "SimpleImputer('(missing)') → OneHotEncoder(max_categories=21)",
                                  "columns_out": len(names)},
                "iforest_kwargs": ifk, "contamination": contamination,
                "lof_kwargs": {"n_neighbors": int(p.get("lof_neighbors", 20)), "novelty": True} if p.get("lof") else None,
                "call": "IsolationForest(**iforest_kwargs).fit(pre.fit_transform(X))"}
    merge(resolved=resolved)
    emit("config_resolved", **resolved)
    emit("fit_started", problem_type="anomaly", eval_metric="isolation score s(x,n)", n_rows=len(df), n_columns=len(names))

    emit("model_started", model="IsolationForest", family="iforest", order=1)
    t0 = time.time()
    forest = IsolationForest(**ifk).fit(Z)
    fit_if = time.time() - t0
    psi = int(forest.max_samples_)
    s = -forest.score_samples(Z)
    thr = float(np.quantile(s, 1 - contamination))
    depths = [int(e.get_depth()) for e in forest.estimators_[:50]]
    emit("model_finished", model="IsolationForest", family="iforest", fit_time=fit_if, order=1,
         val_score=None, n_trees=len(forest.estimators_), psi=psi, c_n=c_n(psi), mean_depth=float(np.mean(depths)))

    lof = pre_lof = None
    lof_s = None
    if p.get("lof"):
        emit("model_started", model="LocalOutlierFactor", family="lof", order=2)
        t0 = time.time()
        pre_lof = build_pipeline(num, cat, scale=True)
        Zs = pre_lof.fit_transform(X)
        lof = LocalOutlierFactor(n_neighbors=int(p.get("lof_neighbors", 20)), novelty=True, n_jobs=-1).fit(Zs)
        lof_s = -lof.negative_outlier_factor_
        emit("model_finished", model="LocalOutlierFactor", family="lof", fit_time=time.time() - t0, order=2)

    stats = robust_stats(X, num, cat)
    model = Model(pre=pre, pre_lof=pre_lof, iforest=forest, lof=lof, num=num, cat=cat, names=names,
                  origin=origin_map(names, num, cat), psi=psi, threshold=thr, contamination=contamination,
                  stats=stats, train_scores=np.sort(s), group=gspec,
                  group_stats=group.fit_stats(df, gspec) if gspec else None, velocity=vspec,
                  velocity_history=velocity.history(df, vspec) if vspec else None)
    mdir = Path(cfg["predictor_path"])
    mdir.mkdir(parents=True, exist_ok=True)
    joblib.dump(model, mdir / "model.joblib")
    (mdir / "model.json").write_text(json.dumps({"kind": "anomaly", "numeric": num, "categorical": cat,
                                                 "threshold": thr, "contamination": contamination, "psi": psi,
                                                 "iforest_kwargs": ifk, "group": gspec, "velocity": vspec},
                                                indent=1, default=str))
    # every derived column, for the review table and the supervised (AutoGluon) data
    derived = [c for c in df.columns if c in set((group.names(gspec) if gspec else []) + (velocity.names(vspec) if vspec else []))]
    if derived:
        df[derived].assign(row=np.arange(len(df)))[["row", *derived]].to_csv(run_dir / "derived.csv", index=False)

    Eh = -c_n(psi) * np.log2(np.maximum(s, 1e-12))
    scores = pd.DataFrame({"row": np.arange(len(df)), "s": s, "Eh": Eh})
    if lof_s is not None:
        scores["lof"] = lof_s
    scores["flagged"] = (s >= thr).astype(int)
    scores.to_csv(run_dir / "scores.csv", index=False)
    n_flag = int(scores["flagged"].sum())

    # reasons for the most anomalous rows
    emit("explaining", what="reasons")
    top_n = int(min(len(df), max(200, n_flag * 2)))
    top_rows = np.argsort(-s, kind="stable")[:top_n]
    expl = explain_rows(model, df.iloc[top_rows])
    show_cols = list(dict.fromkeys([*(cfg.get("id_columns") or []), *([vspec["time_column"]] if vspec else []), *num, *cat, *aux]))
    top = []
    for rank, (r, e) in enumerate(zip(top_rows, expl), start=1):
        top.append({"rank": rank, "row": int(r), "s": float(s[r]), "Eh": float(Eh[r]),
                    "lof": float(lof_s[r]) if lof_s is not None else None, "flagged": bool(s[r] >= thr),
                    "values": {c: (None if pd.isna(df.iloc[r][c]) else (df.iloc[r][c].item() if hasattr(df.iloc[r][c], "item") else df.iloc[r][c]))
                               for c in show_cols if c in df.columns},
                    "phi0": e["phi0"], "phi_sum": e["phi_sum"], "reasons": e["reasons"],
                    "why": "; ".join(_text(x) for x in e["reasons"][:3])})
    (run_dir / "top.json").write_text(json.dumps(top, default=str))

    hist_counts, edges = np.histogram(s, bins=60)
    dist = {"edges": [float(x) for x in edges], "counts": [int(x) for x in hist_counts],
            "quantiles": {str(q): float(np.quantile(s, q)) for q in (0.5, 0.9, 0.95, 0.99, 0.995, 0.999)},
            "min": float(s.min()), "max": float(s.max()), "mean": float(s.mean())}
    result: dict[str, Any] = {
        "problem_type": "anomaly", "best_model": "IsolationForest",
        "eval_metric": {"name": "isolation score", "greater_is_better": True,
                        "formula": "s(x,n) = 2^(−E[h(x)]/c(n))"},
        "anomaly": {"threshold": thr, "contamination": contamination, "n_flagged": n_flag, "psi": psi, "c_n": c_n(psi),
                    "n_trees": len(forest.estimators_), "distribution": dist, "fit_seconds": fit_if,
                    "columns_out": len(names), "mean_depth": float(np.mean(depths)),
                    "lof": None if lof_s is None else {
                        "n_neighbors": int(p.get("lof_neighbors", 20)),
                        "spearman_vs_iforest": float(pd.Series(s).rank().corr(pd.Series(lof_s).rank())),
                        "overlap_top": int(len(set(np.argsort(-s)[:n_flag]) & set(np.argsort(-lof_s)[:n_flag]))) if n_flag else 0}},
        "leaderboard": [{"model": "IsolationForest", "family": "iforest", "rank": 1, "fit_time": fit_if, "stack_level": 1}]
        + ([{"model": "LocalOutlierFactor", "family": "lof", "rank": 2, "stack_level": 1}] if lof_s is not None else []),
        "fit_seconds": fit_if,
    }
    truth = cfg.get("truth")
    if truth:
        result["anomaly"]["truth"] = truth_check(np.arange(len(df)), s, truth, n_flag)
        if lof_s is not None:
            result["anomaly"]["truth_lof"] = truth_check(np.arange(len(df)), lof_s, truth, n_flag)
    merge(**result)
    emit("evaluation", n_flagged=n_flag, threshold=thr,
         precision_at_k=(result["anomaly"].get("truth") or {}).get("precision_at_k"))
    return {"best_model": "IsolationForest", "n_flagged": n_flag}


def rescore_threshold(scores_csv: str, contamination: float) -> dict[str, Any]:
    s = pd.read_csv(scores_csv)["s"].to_numpy()
    thr = float(np.quantile(s, 1 - contamination))
    return {"threshold": thr, "n_flagged": int((s >= thr).sum())}


def score_frame(model_dir: str, df: pd.DataFrame, explain_top: int = 50) -> tuple[pd.DataFrame, list[dict[str, Any]], dict[str, Any]]:
    """(In a worker.) Score new rows with a saved model: s, E[h], flag at the saved threshold, the percentile of s among
    the fitted rows, and reasons for the most anomalous rows."""
    import joblib

    model: Model = joblib.load(Path(model_dir) / "model.joblib")
    df = model.augment(df)
    missing = [c for c in model.num + model.cat if c not in df.columns]
    if missing:
        raise AnomalyError(f"the file lacks the feature column(s) {', '.join(missing)}")
    sc = model.score(df)
    out = df.copy()
    out["anomaly_score"] = sc["s"]
    out["expected_path_length"] = sc["Eh"]
    out["percentile"] = np.searchsorted(model.train_scores, sc["s"], side="right") / len(model.train_scores)
    out["flagged"] = (sc["s"] >= model.threshold).astype(int)
    if "lof" in sc:
        out["lof_score"] = sc["lof"]
    order = np.argsort(-sc["s"], kind="stable")[:explain_top]
    expl = explain_rows(model, df.iloc[order]) if len(order) else []
    top = [{"row": int(r), "s": float(sc["s"][r]), "percentile": float(out["percentile"].iloc[r]),
            "flagged": bool(sc["s"][r] >= model.threshold), "reasons": e["reasons"],
            "why": "; ".join(_text(x) for x in e["reasons"][:3])} for r, e in zip(order, expl)]
    summary = {"rows": int(len(df)), "n_flagged": int(out["flagged"].sum()), "threshold": model.threshold,
               "flagged_share": float(out["flagged"].mean()), "expected_share": model.contamination,
               "mean_score": float(np.mean(sc["s"]))}
    return out, top, summary


def score_batch(model_dir: str, csv_path: str, out_path: str) -> dict[str, Any]:
    df = pd.read_csv(csv_path)
    out, top, summary = score_frame(model_dir, df)
    out.to_csv(out_path, index=False)
    h, edges = np.histogram(out["anomaly_score"], bins=40)
    return {"summary": summary, "top": top[:25], "histogram": {"edges": [float(x) for x in edges], "counts": [int(x) for x in h]}}


def score_one(model_dir: str, row: dict[str, Any]) -> dict[str, Any]:
    df = pd.DataFrame([row])
    out, top, summary = score_frame(model_dir, df, explain_top=1)
    return {"anomaly_score": float(out["anomaly_score"].iloc[0]), "percentile": float(out["percentile"].iloc[0]),
            "flagged": bool(out["flagged"].iloc[0]), "threshold": summary["threshold"], "reasons": top[0]["reasons"],
            "why": top[0]["why"]}
