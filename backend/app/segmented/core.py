"""G5 · Segmented models: one AutoGluon model per segment against one global model, and a routed version.

    global      f_G(x)      TabularPredictor on every training row, the segment column s included
    segment k   f_k(x)      the same config (problem type, metric, presets, time limit) on the rows with s = k, s left out
    routed      f_R(x) = f_k(x) if a model for s(x) = k is routed, else f_G(x)          (unknown segment → f_G)

A segment gets its own model when it has enough training rows (≥ MIN_ROWS, and ≥ MIN_CLASS rows of each class for a
yes/no target). With a tuning slice (an out-of-time or group split has one), that slice is held back from every fit and
used only to route: a segment is routed to its own model when that model beat f_G on the segment's tuning rows (log
loss / squared error). The choice is out of sample for both models and never looks at the holdout. Without a tuning
slice, every trained segment model is routed.

The comparison is on the same holdout rows for both models, per segment and overall, with paired intervals:
    binary      ΔAUC = AUC(f_R) − AUC(f_G), DeLong paired 95 % CI;  Δ log loss with a paired bootstrap CI
    regression  ΔRMSE and ΔMSE, with a paired bootstrap CI of ΔMSE (mean of e_R² − e_G² over resampled rows)

A yes/no decision uses one threshold t for every model, so a change in a decision is a change in p̂, not in t. The
default is F7's business cost c_FN = 10·c_FP, whose Bayes threshold for a calibrated p̂ is t = c_FP / (c_FP + c_FN).
"""

from __future__ import annotations

import json
import re
import time
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from .. import frames

SEGMENT_MAX = 12
MIN_ROWS = 300
MIN_CLASS = 30
GLOBAL = "__global__"
REASONS_BATCH_K = 200
_CACHE: dict[str, tuple[float, dict[str, Any]]] = {}
_REASONERS: dict[tuple, Any] = {}


def _dir_name(i: int, value: str) -> str:
    return f"seg_{i}_{re.sub(r'[^A-Za-z0-9]+', '_', value)[:24]}"


# --------------------------------------------------------------------------------------------------------- numbers

def compare(y: np.ndarray, a: np.ndarray, b: np.ndarray, problem_type: str) -> dict[str, Any]:
    """f_G (a) against f_R or f_k (b) on the same rows: each model's metrics and the paired differences."""
    from ..registry.monitoring import _logloss_rows, paired_bootstrap, paired_delong

    y = np.asarray(y)
    out: dict[str, Any] = {"n": int(len(y))}
    if problem_type == "binary":
        y = y.astype(int)
        out["positives"] = int(y.sum())
        out["rate"] = float(y.mean()) if len(y) else None
        for k, p in (("global", a), ("routed", b)):
            out[k] = {"log_loss": float(_logloss_rows(y, p).mean()), "brier": float(((p - y) ** 2).mean()), "mean_p": float(p.mean())}
        if 0 < y.sum() < len(y):
            d = paired_delong(y, a, b)
            out["global"]["auc"], out["routed"]["auc"] = d["auc_champion"], d["auc_challenger"]
            out["delta_auc"] = {k: d[k] for k in ("delta", "ci", "se", "p_value")}
        else:
            out["delta_auc"] = None
        ll = paired_bootstrap(_logloss_rows(y, a), _logloss_rows(y, b), B=1000)
        out["delta_log_loss"] = {k: ll[k] for k in ("delta", "ci", "se")}
        d = out["delta_auc"]
        # Better ⟺ the paired CI excludes 0 on the side that favours f_R (AUC up; log loss down, when AUC can't be computed)
        if d:
            out["verdict"] = "routed better" if d["ci"][0] > 0 else "global better" if d["ci"][1] < 0 else "no clear difference"
        else:
            out["verdict"] = "routed better" if ll["ci"][1] < 0 else "global better" if ll["ci"][0] > 0 else "no clear difference"
    else:
        y = y.astype(float)
        for k, p in (("global", a), ("routed", b)):
            e = p - y
            out[k] = {"rmse": float(np.sqrt((e ** 2).mean())), "mae": float(np.abs(e).mean()), "mean_pred": float(p.mean())}
        out["mean_true"] = float(y.mean())
        se = paired_bootstrap((a - y) ** 2, (b - y) ** 2, B=1000)
        out["delta_mse"] = {k: se[k] for k in ("delta", "ci", "se")}
        out["delta_rmse"] = out["routed"]["rmse"] - out["global"]["rmse"]
        out["verdict"] = "routed better" if se["ci"][1] < 0 else "global better" if se["ci"][0] > 0 else "no clear difference"
    return out


def _loss(y: np.ndarray, p: np.ndarray, problem_type: str) -> float:
    from ..registry.monitoring import _logloss_rows

    return float(_logloss_rows(np.asarray(y).astype(int), p).mean()) if problem_type == "binary" else float(((p - y) ** 2).mean())


# ---------------------------------------------------------------------------------------------------------- models

def load(path: str) -> dict[str, Any]:
    """meta.json and every predictor of a segmented model folder (cached per folder and meta's mtime)."""
    from autogluon.tabular import TabularPredictor

    p = Path(path)
    m = (p / "meta.json").stat().st_mtime
    hit = _CACHE.get(str(p))
    if hit and hit[0] == m:
        return hit[1]
    meta = json.loads((p / "meta.json").read_text())
    preds = {GLOBAL: TabularPredictor.load(str(p / "global"), require_py_version_match=False)}
    for v in meta["routes"]:  # only the routed segment models serve (a registered version holds only those)
        d = meta["models"][v]
        preds[v] = TabularPredictor.load(str(p / d), require_py_version_match=False)
    out = {"meta": meta, "predictors": preds, "path": str(p)}
    _CACHE[str(p)] = (m, out)
    return out


def route(meta: dict[str, Any], X: pd.DataFrame) -> np.ndarray:
    """The model key of each row: its segment's value when that segment is routed, else GLOBAL."""
    s = meta["segment_column"]
    vals = X[s].astype(str).to_numpy() if s in X.columns else np.array(["nan"] * len(X))
    routed = set(meta["routes"])
    return np.array([v if v in routed else GLOBAL for v in vals], dtype=object)


def _out(p, X: pd.DataFrame, problem_type: str) -> np.ndarray:
    if problem_type == "binary":
        return np.asarray(p.predict_proba(X, as_multiclass=False), dtype=float)
    return np.asarray(p.predict(X), dtype=float)


def predict(models: dict[str, Any], X: pd.DataFrame, keys: np.ndarray | None = None) -> np.ndarray:
    """f_R(x) for every row (p̂ of the positive class, or ŷ)."""
    meta = models["meta"]
    keys = route(meta, X) if keys is None else keys
    out = np.empty(len(X), dtype=float)
    for k in set(keys):
        m = keys == k
        out[m] = _out(models["predictors"][k], X.loc[m], meta["problem_type"])
    return out


def _frame(meta: dict[str, Any], rows: pd.DataFrame | list[dict[str, Any]]) -> pd.DataFrame:
    df = pd.DataFrame(rows) if isinstance(rows, list) else rows
    if df.empty:
        raise ValueError("Give at least one row.")
    if not any(c in df.columns for c in meta["inputs"]):
        raise ValueError(f"None of the model's inputs ({', '.join(meta['inputs'][:5])}, …) are in the rows.")
    X = df.reindex(columns=meta["inputs"]).reset_index(drop=True)
    for c in meta["numeric"]:
        if c in X.columns:
            X[c] = pd.to_numeric(X[c], errors="coerce")
    s = meta["segment_column"]
    X[s] = X[s].where(X[s].isna(), X[s].astype(str))
    return X


# ------------------------------------------------------------------------------------------------------------- run

def run(cfg: dict[str, Any], run_dir: Path, emit, merge) -> dict[str, Any]:
    from ..engine.progress import ProgressCallback
    from ..engine.worker import GROUP_FOLD_COLUMN, fit_predictor, load_frames, prepare_fit

    label, seg = cfg["label"], cfg["segment_column"]
    train, holdout, info, tuning = load_frames(cfg)
    emit("data_ready", **info)
    merge(data=info)
    pt = cfg["problem_type"]
    metric = "log_loss" if pt == "binary" else "root_mean_squared_error"
    root = Path(cfg["predictor_path"])
    root.mkdir(parents=True, exist_ok=True)
    inputs = [c for c in train.columns if c not in (label, GROUP_FOLD_COLUMN)]
    numeric = [c for c in inputs if pd.api.types.is_numeric_dtype(train[c]) and not pd.api.types.is_bool_dtype(train[c])]
    for f in (train, holdout, tuning):
        if f is not None:
            f[seg] = f[seg].astype(str)

    def fit(key: str, tr: pd.DataFrame, ho: pd.DataFrame, tu: pd.DataFrame | None, path: Path):
        emit("seg_model_started", model=key, rows=int(len(tr)))
        emit("log", level="info", logger="ensemble", message=f"segmented: fitting {'the global model' if key == GLOBAL else f'{seg} = {key}'} "
                                                             f"on {len(tr):,} rows ({pt}, {metric})")
        sub = {**cfg, "eval_metric": metric, "cost": None, "quantile": None, "monotone": None, "scorecard": None,
               "column_types": {}, "text_columns": [], "pseudolabel": None, "distill": None,
               "predictor_path": str(path), "predictor_kwargs": {}}
        prep = prepare_fit(sub, tr, ho, tu, info)
        p, secs = fit_predictor(sub, prep, tr, str(path), callbacks=[ProgressCallback(emit)], emit=emit)
        emit("seg_model_finished", model=key, best_model=p.model_best, seconds=secs, rows=int(len(tr)))
        return p, {"rows": int(len(tr)), "best_model": p.model_best, "fit_seconds": secs, "n_models": len(p.model_names())}

    # The tuning slice is not given to any fit (AutoGluon holds out its own validation rows): it only chooses the routes.
    g, gfit = fit(GLOBAL, train, holdout, None, root / "global")
    preds = {GLOBAL: g}
    pos = g.positive_class if pt == "binary" else None
    t = None
    if pt == "binary":
        c = cfg.get("cost") or {"c_fp": 1.0, "c_fn": 10.0}
        t = float(c["c_fp"]) / (float(c["c_fp"]) + float(c["c_fn"]))
        g.set_decision_threshold(t)
        g.save()

    counts = train[seg].value_counts()
    seg_rows: dict[str, dict[str, Any]] = {}
    models: dict[str, str] = {}
    for i, (v, n) in enumerate(counts.items()):
        tr = train[train[seg] == v].drop(columns=[seg, GROUP_FOLD_COLUMN], errors="ignore")
        row: dict[str, Any] = {"value": v, "train_rows": int(n)}
        why = None
        if n < MIN_ROWS:
            why = f"{n} training rows < {MIN_ROWS}"
        elif pt == "binary":
            k = int((tr[label] == pos).sum())
            if min(k, n - k) < MIN_CLASS:
                why = f"{min(k, n - k)} rows of the rarer class < {MIN_CLASS}"
        if why:
            row.update(model=None, why=why)
            seg_rows[v] = row
            continue
        ho = holdout[holdout[seg] == v].drop(columns=[seg])
        d = _dir_name(i, v)
        p, fi = fit(v, tr, ho, None, root / d)
        if t is not None:
            p.set_decision_threshold(t)
            p.save()
        preds[v] = p
        models[v] = d
        row.update(model=d, **fi)
        seg_rows[v] = row

    # Routing: out of sample on the tuning slice when there is one, else every trained segment model.
    routes: list[str] = []
    for v, r in seg_rows.items():
        if not r.get("model"):
            r["routed"], r["route_why"] = False, f"no segment model ({r['why']}): the global model scores it"
            continue
        tu = None if tuning is None else tuning[tuning[seg] == v]
        if tu is not None and len(tu) >= 50 and (pt != "binary" or 0 < int((tu[label] == pos).sum()) < len(tu)):
            yt = (tu[label] == pos).astype(int).to_numpy() if pt == "binary" else tu[label].to_numpy(dtype=float)
            lg = _loss(yt, _out(g, tu, pt), pt)
            ls = _loss(yt, _out(preds[v], tu.drop(columns=[seg]), pt), pt)
            r["tuning"] = {"rows": int(len(tu)), "global": lg, "segment": ls, "loss": "log loss" if pt == "binary" else "MSE"}
            r["routed"] = ls < lg
            r["route_why"] = (f"its own model beat the global one on {len(tu)} tuning rows ({r['tuning']['loss']} {ls:.4f} vs {lg:.4f})"
                              if ls < lg else f"the global model was better on {len(tu)} tuning rows "
                                              f"({r['tuning']['loss']} {lg:.4f} vs {ls:.4f}): it falls back to the global model")
        else:
            r["routed"] = True
            r["route_why"] = "no tuning rows to choose on: its own model is routed"
        if r["routed"]:
            routes.append(v)

    meta = {"label": label, "problem_type": pt, "positive_class": None if pos is None else (pos.item() if isinstance(pos, np.generic) else pos),
            "threshold": t, "segment_column": seg, "inputs": inputs, "numeric": numeric, "models": models, "routes": routes,
            "train_ref": {k: cfg.get(k) for k in ("input_ref", "csv_path", "splits_path", "label", "drop_columns")},
            "features": {GLOBAL: list(g.original_features), **{v: list(preds[v].original_features) for v in models}}}
    (root / "meta.json").write_text(json.dumps(meta, indent=1, default=str))
    m = {"meta": meta, "predictors": preds, "path": str(root)}

    # Holdout: f_G, every segment's own f_k, and the routed f_R on the same rows.
    emit("log", level="info", logger="ensemble", message="segmented: comparing on the holdout (paired)")
    y = (holdout[label] == pos).astype(int).to_numpy() if pt == "binary" else holdout[label].to_numpy(dtype=float)
    hx = holdout[inputs].reset_index(drop=True)
    pg = _out(g, hx, pt)
    keys = route(meta, hx)
    pr = predict(m, hx, keys)
    sv = hx[seg].astype(str).to_numpy()
    own = pg.copy()
    for v in models:
        msk = sv == v
        if msk.any():
            own[msk] = _out(preds[v], hx.loc[msk], pt)
    per = []
    for v in sorted(seg_rows, key=lambda v: -seg_rows[v]["train_rows"]):
        msk = sv == v
        r = {**seg_rows[v], "holdout_rows": int(msk.sum())}
        if msk.sum() >= 10:
            r["own"] = compare(y[msk], pg[msk], own[msk], pt) if r.get("model") else None
            r["routed_cmp"] = compare(y[msk], pg[msk], pr[msk], pt)
        per.append(r)
    unseen = sorted(set(sv) - set(seg_rows))
    overall = compare(y, pg, pr, pt)
    overall_own = compare(y, pg, own, pt)
    pd.DataFrame({"row": holdout.index.to_numpy(), "segment": sv, "model": keys, "y": y, "global": pg, "routed": pr, "own": own}) \
        .to_csv(run_dir / "seg_holdout.csv", index=False)
    head = "delta_auc" if pt == "binary" else "delta_mse"
    wins = [r["value"] for r in per if (r.get("own") or {}).get("verdict") == "routed better"]
    losses = [r["value"] for r in per if (r.get("own") or {}).get("verdict") == "global better"]
    out = {"segment_column": seg, "problem_type": pt, "label": label, "positive_class": meta["positive_class"], "threshold": t,
           "eval_metric": metric, "headline": "AUC" if pt == "binary" else "RMSE", "segments": per, "overall": overall,
           "overall_all_segment_models": overall_own, "routes": routes, "unseen_in_training": unseen,
           "global": {**gfit, "features": len(g.original_features)}, "holdout_rows": int(len(holdout)),
           "tuning_rows": 0 if tuning is None else int(len(tuning)), "wins": wins, "losses": losses,
           "routing_rule": "validated on the tuning slice" if tuning is not None else "every trained segment model",
           "detected": cfg.get("detected") or {}, "min_rows": MIN_ROWS, "min_class": MIN_CLASS}
    ov = overall.get("routed") or {}
    merge(segmented=out, problem_type=pt, best_model=f"routed: {len(routes)} segment model{'s' if len(routes) != 1 else ''} + global",
          features=inputs, holdout={"metrics": {("auc" if pt == "binary" else "rmse"): ov.get("auc" if pt == "binary" else "rmse"),
                                                f"{head}": (overall.get(head) or {}).get("delta")}},
          eval_metric={"name": "auc" if pt == "binary" else "root_mean_squared_error", "greater_is_better": pt == "binary"})
    return {"routes": len(routes), "wins": wins}


# --------------------------------------------------------------------------------------------- serving (Registry)

def _train_rows(meta: dict[str, Any], key: str) -> pd.DataFrame:
    from ..splits import holdout_mask

    ref = meta["train_ref"]
    df = frames.read(frames.resolve(ref))  # input_ref (new models), else the csv_path the fit read
    is_h = holdout_mask(json.loads(Path(ref["splits_path"]).read_text()), len(df))
    keep = ~is_h & df[ref["label"]].notna().to_numpy()
    if key != GLOBAL:
        keep &= (df[meta["segment_column"]].astype(str) == key).to_numpy()
    return df.loc[keep]


def reasoner(path: str, key: str, rconf: dict[str, Any], kinds: dict[str, str]):
    """F10's Reasoner for one routed model, its background drawn from that model's own training rows."""
    from ..engine import reasons as rc

    models = load(path)
    k = (str(path), key, json.dumps(rconf, sort_keys=True, default=str), _CACHE[str(path)][0])
    if k not in _REASONERS:
        p = models["predictors"][key]
        # One code book for every routed model: the global model's (R01… over all inputs) with the user's edits, so a
        # code means the same thing whichever segment's model cited it.
        book = rc.merge_mapping(rconf.get("mapping"), rc.default_mapping(models["meta"]["features"][GLOBAL], kinds, None))
        _REASONERS[k] = rc.Reasoner(p, _train_rows(models["meta"], key), {**rconf, "mapping": book}, kinds)
    return _REASONERS[k]


def _model_words(meta: dict[str, Any], key: str, value: str) -> str:
    return f"{meta['segment_column']} = {key}" if key != GLOBAL else (
        f"global ({meta['segment_column']} = {value} has no routed model)")


def score_rows(path: str, rows: list[dict[str, Any]] | pd.DataFrame, rconf: dict[str, Any] | None = None,
               kinds: dict[str, str] | None = None, reasons_k: int | None = None) -> dict[str, Any]:
    """Rows → which model scored each, p̂ (or ŷ), the decision at t and, for adverse rows (the first `reasons_k`),
    F10 reason codes from that row's own model."""
    t0 = time.perf_counter()
    models = load(path)
    meta = models["meta"]
    X = _frame(meta, rows)
    keys = route(meta, X)
    out = predict(models, X, keys)
    pt = meta["problem_type"]
    sv = X[meta["segment_column"]].astype(str).to_numpy() if meta["segment_column"] in X.columns else np.array(["nan"] * len(X))
    res: list[dict[str, Any]] = []
    for i in range(len(X)):
        r: dict[str, Any] = {"segment": sv[i], "model": "segment" if keys[i] != GLOBAL else "global",
                             "routed_to": _model_words(meta, keys[i], sv[i])}
        if pt == "binary":
            r["p"] = float(out[i])
            r["adverse"] = bool(out[i] >= meta["threshold"])
            r["prediction"] = meta["positive_class"] if r["adverse"] else None
        else:
            r["prediction"] = float(out[i])
        res.append(r)
    if pt == "binary" and rconf is not None:
        limit = len(X) if reasons_k is None else reasons_k
        adv = [i for i, r in enumerate(res) if r["adverse"]][:limit]
        for k in set(keys[adv]) if adv else []:
            idx = [i for i in adv if keys[i] == k]
            rs = reasoner(path, k, rconf, kinds or {})
            got = rs.run(X.iloc[idx].reset_index(drop=True)[rs.features], only_adverse=True)
            for j, i in enumerate(idx):
                res[i]["codes"] = got["codes"][j]
    return {"problem_type": pt, "segment_column": meta["segment_column"], "threshold": meta["threshold"],
            "positive_class": meta["positive_class"], "n_rows": len(res), "results": res,
            "predict_ms": (time.perf_counter() - t0) * 1e3}


def try_row(path: str, row: dict[str, Any], rconf: dict[str, Any] | None, kinds: dict[str, str] | None) -> dict[str, Any]:
    """Try It on a routed version, shaped like a tabular version's answer (prediction, probabilities, decision and
    reason codes from the row's own model) plus the routing."""
    from ..engine.jsonsafe import jsonable

    t0 = time.perf_counter()
    models = load(path)
    meta = models["meta"]
    X = _frame(meta, [row])
    key = route(meta, X)[0]
    p = models["predictors"][key]
    feats = meta["features"][key]
    value = str(X[meta["segment_column"]].iloc[0])
    given = {k for k, v in row.items() if v is not None and v != ""}
    out: dict[str, Any] = {"model": p.model_best, "problem_type": meta["problem_type"], "features": meta["inputs"],
                           "missing_columns": [c for c in meta["inputs"] if c not in given],
                           "segment": {"column": meta["segment_column"], "value": value, "model": "segment" if key != GLOBAL else "global",
                                       "routed_to": _model_words(meta, key, value), "features": feats}}
    if meta["problem_type"] == "binary":
        proba = p.predict_proba(X[feats])
        pos = p.positive_class
        pp = float(proba[pos].iloc[0])
        t = meta["threshold"]
        neg = next(c for c in proba.columns if c != pos)
        out.update(classes=jsonable(list(proba.columns)), probabilities={str(c): float(v) for c, v in proba.iloc[0].items()},
                   positive_class=jsonable(pos), threshold=t, prediction=jsonable(pos if pp >= t else neg))
    else:
        out.update(prediction=float(p.predict(X[feats]).iloc[0]), probabilities=None)
    out["predict_ms"] = (time.perf_counter() - t0) * 1e3
    if meta["problem_type"] == "binary" and rconf is not None:
        try:
            rs = reasoner(path, key, rconf, kinds or {})
            r = rs.run(X[rs.features], only_adverse=False)
            info = rs.info()
            out["reasons"] = {**{k: v for k, v in info.items()}, "adverse": bool(r["adverse"][0]), "p_positive": float(r["p_positive"][0]),
                              "codes": r["codes"][0], "method": r["method"], "note": r["note"], "additivity_max": r["additivity_max"],
                              "base_value": float(r["base"][0]) if len(r["base"]) else None, "monotone": r["monotone"],
                              "reasons_ms": (time.perf_counter() - t0) * 1e3}
        except Exception as e:  # noqa: BLE001
            out["reasons"] = {"error": f"{type(e).__name__}: {e}"}
    return jsonable(out)


def score_batch(path: str, df: pd.DataFrame, rconf: dict[str, Any] | None, kinds: dict[str, str] | None) -> tuple[pd.DataFrame, dict[str, Any]]:
    """A file → the file with routed_model, the prediction (and p̂, decision, reason codes for the first adverse rows),
    plus counts per segment."""
    r = score_rows(path, df, rconf, kinds, reasons_k=REASONS_BATCH_K)
    meta = load(path)["meta"]
    out = df.copy()
    res = r["results"]
    out["routed_model"] = [x["routed_to"] for x in res]
    if meta["problem_type"] == "binary":
        out["p_" + str(meta["positive_class"])] = [x["p"] for x in res]
        out["decision"] = ["adverse" if x["adverse"] else "approve" for x in res]
        out["reason_codes"] = ["; ".join(f"{c['code']} {c['statement']}" for c in x.get("codes") or []) for x in res]
    else:
        out["prediction"] = [x["prediction"] for x in res]
    segs = pd.DataFrame({"segment": [x["segment"] for x in res], "model": [x["model"] for x in res],
                         "y": [x.get("p", x.get("prediction")) for x in res]})
    by = [{"value": v, "n": int(len(g)), "model": g["model"].iloc[0], "mean_prediction": float(g["y"].mean()),
           **({"adverse_share": float((g["y"] >= meta["threshold"]).mean())} if meta["problem_type"] == "binary" else {})}
          for v, g in segs.groupby("segment")]
    return out, {"segments": sorted(by, key=lambda x: -x["n"]), "global_rows": int((segs["model"] == "global").sum()),
                 "mean_prediction": float(segs["y"].mean()), "reasons_rows": sum(1 for x in res if x.get("codes"))}


# ------------------------------------------------------------------------------------------- detection (G0 roles)

def heterogeneity(df: pd.DataFrame, target: str, seg: str, max_rows: int = 20_000) -> dict[str, Any] | None:
    """A cheap look, before any fit, at whether the segments behave differently: for each numeric input, the rank
    correlation ρ with the target inside each segment. The evidence is the input whose ρ differs most between segments
    (opposite signs are the strongest sign that one model per segment could help)."""
    if target not in df.columns or seg not in df.columns:
        return None
    d = df.sample(max_rows, random_state=0) if len(df) > max_rows else df
    y = d[target]
    if not pd.api.types.is_numeric_dtype(y):
        vals = y.dropna().unique()
        if len(vals) != 2:
            return None
        y = (y == sorted(vals, key=str)[-1]).astype(float)
    groups = [v for v, n in d[seg].astype(str).value_counts().items() if n >= 100]
    if len(groups) < 2:
        return None
    best = None
    for c in d.columns:
        if c in (target, seg) or not pd.api.types.is_numeric_dtype(d[c]) or d[c].nunique() < 5:
            continue
        rho = {}
        for v in groups:
            m = (d[seg].astype(str) == v).to_numpy()
            r = pd.Series(d[c].to_numpy()[m]).corr(pd.Series(y.to_numpy()[m]), method="spearman")
            if r == r:
                rho[v] = float(r)
        if len(rho) < 2:
            continue
        hi, lo = max(rho, key=rho.get), min(rho, key=rho.get)
        spread = rho[hi] - rho[lo]
        strength = min(rho[hi], -rho[lo])  # > 0 only when the signs are opposite
        cand = {"feature": c, "spread": spread, "high": [hi, rho[hi]], "low": [lo, rho[lo]],
                "opposite": bool(rho[hi] > 0.05 and rho[lo] < -0.05), "strength": strength}
        cand["key"] = (max(strength, 0.0) if strength > 0.05 else 0.0, spread)  # opposite signs first, then the spread
        if best is None or cand["key"] > best["key"]:
            best = cand
    if not best:
        return None
    best.pop("key")
    best["differs"] = bool(best["opposite"] or best["spread"] >= 0.25)
    best["words"] = (f"'{best['feature']}' moves {target} {'in opposite directions' if best['opposite'] else 'very differently'} "
                     f"across {seg}: ρ = {best['high'][1]:+.2f} for {best['high'][0]}, {best['low'][1]:+.2f} for {best['low'][0]}")
    return best
