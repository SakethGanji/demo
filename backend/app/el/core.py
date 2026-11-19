"""G3 · Expected loss: three AutoGluon models chained, EL = PD · LGD · EAD per loan, and stress scenarios.

    PD   P(default)                       TabularPredictor(problem_type='binary',     eval_metric='log_loss'), every loan
    LGD  loss given default, a share 0–1  TabularPredictor(problem_type='regression', eval_metric='rmse'),     defaulters
    EAD  exposure at default, money       TabularPredictor(problem_type='regression', eval_metric='rmse'),     defaulters

    EL_i = PD̂_i · clip(LGD̂_i, 0, 1) · max(EAD̂_i, 0)        realised L_i = y_i · LGD_i · EAD_i

LGD and EAD are known only for loans that defaulted, so they learn from those rows and predict for every loan: "if this
loan defaulted, what share would be lost, of how much". Log loss and RMSE are proper for the mean, so the products are
unbiased when each model is; the holdout check is ΣEL against Σ realised loss on loans none of the models saw.

All three fits use F12's `prepare_fit`/`fit_predictor` on the F4/F6 split of the PD target (one holdout for all three).

A stress scenario is a list of shocks to inputs, e.g. income −10 % or utilisation +15 pp. The holdout loans are shocked,
re-scored by all three models, and EL is compared by segment. Shocked values are kept within the column's natural range
(≥ 0 for non-negative columns, ≤ 1 for shares) and the number of clipped values is reported.
"""

from __future__ import annotations

import json
import re
import time
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

B_BOOT = 400
SEGMENT_MAX = 20
OPS = ("pct", "pp", "add", "set")
_CACHE: dict[str, tuple[float, dict[str, Any]]] = {}


# --------------------------------------------------------------------------------------------------------- numbers

def el_frame(p: np.ndarray, lgd: np.ndarray, ead: np.ndarray) -> pd.DataFrame:
    lgd = np.clip(np.asarray(lgd, float), 0, 1)
    ead = np.maximum(np.asarray(ead, float), 0)
    p = np.asarray(p, float)
    return pd.DataFrame({"pd": p, "lgd": lgd, "ead": ead, "el": p * lgd * ead})


def portfolio(el: np.ndarray, realised: np.ndarray, y: np.ndarray, ead_hat: np.ndarray, pd_hat: np.ndarray,
              seed: int = 0) -> dict[str, Any]:
    """ΣEL vs Σ realised loss, with a bootstrap 95 % interval of the ratio (loans resampled)."""
    el, realised, y = np.asarray(el, float), np.asarray(realised, float), np.asarray(y, int)
    tot_el, tot_l = float(el.sum()), float(realised.sum())
    rng = np.random.default_rng(seed)
    n = len(el)
    idx = rng.integers(0, n, size=(B_BOOT, n))
    ratios = el[idx].sum(1) / np.maximum(realised[idx].sum(1), 1e-9)
    return {
        "n": int(n), "defaults": int(y.sum()), "default_rate": float(y.mean()) if n else None,
        "mean_pd": float(np.mean(pd_hat)) if n else None,
        "el": tot_el, "realised": tot_l, "ratio": tot_el / tot_l if tot_l > 0 else None,
        "error_pct": (tot_el / tot_l - 1) * 100 if tot_l > 0 else None,
        "ratio_ci": [float(np.percentile(ratios, 2.5)), float(np.percentile(ratios, 97.5))],
        "ead_total": float(np.sum(ead_hat)), "el_rate": tot_el / float(np.sum(ead_hat)) if np.sum(ead_hat) > 0 else None,
        "within_10pct": bool(tot_l > 0 and abs(tot_el / tot_l - 1) <= 0.10),
    }


def segments(frame: pd.DataFrame, cols: list[str], realised: np.ndarray | None = None) -> dict[str, list[dict[str, Any]]]:
    """Per segment column: n, ΣEL, its share, EL rate (ΣEL / ΣEAD̂), mean PD̂ and, on the holdout, Σ realised loss."""
    out: dict[str, list[dict[str, Any]]] = {}
    tot = float(frame["el"].sum()) or 1.0
    f = frame.copy()
    if realised is not None:
        f["realised"] = realised
    for c in cols:
        if c not in f.columns:
            continue
        key = f[c].astype(str)
        if key.nunique() > SEGMENT_MAX:
            continue
        rows = []
        for v, g in f.groupby(key, sort=True):
            r = {"value": v, "n": int(len(g)), "el": float(g["el"].sum()), "share": float(g["el"].sum()) / tot,
                 "mean_pd": float(g["pd"].mean()), "mean_lgd": float(g["lgd"].mean()), "ead": float(g["ead"].sum()),
                 "el_rate": float(g["el"].sum() / g["ead"].sum()) if g["ead"].sum() > 0 else None}
            if "realised" in g:
                r["realised"] = float(g["realised"].sum())
                r["ratio"] = r["el"] / r["realised"] if r["realised"] > 0 else None
            if "y" in g:
                r["defaults"] = int(g["y"].sum())
            rows.append(r)
        out[c] = sorted(rows, key=lambda r: -r["el"])
    return out


def shock_frame(X: pd.DataFrame, shocks: list[dict[str, Any]], ranges: dict[str, dict[str, Any]]) -> tuple[pd.DataFrame, list[dict[str, Any]]]:
    """Apply the shocks to a copy of X. → (shocked frame, per-shock {column, op, value, words, changed, clipped})."""
    X = X.copy()
    notes = []
    for s in shocks:
        col, op, v = s["column"], s["op"], s["value"]
        if col not in X.columns:
            raise ValueError(f"'{col}' is not an input of these models.")
        if op not in OPS:
            raise ValueError(f"op must be one of {', '.join(OPS)}.")
        x = X[col]
        rng = ranges.get(col) or {}
        if op == "set":
            new = pd.Series(v, index=X.index)
            if rng.get("numeric"):
                new = pd.to_numeric(new, errors="raise")
        else:
            if not rng.get("numeric"):
                raise ValueError(f"'{col}' is a category; use op 'set' with one of its values.")
            v = float(v)
            x = pd.to_numeric(x, errors="coerce")
            new = x * (1 + v / 100) if op == "pct" else x + (v / 100 if op == "pp" else v)
        clipped = 0
        if rng.get("numeric"):
            lo = 0.0 if rng.get("min", -1) >= 0 else None
            hi = max(1.0, float(rng["max"])) if rng.get("share") else None
            before = new.copy()
            new = new.clip(lower=lo, upper=hi)
            clipped = int((before != new).sum())
        changed = int((new.astype(str) != X[col].astype(str)).sum())
        X[col] = new
        notes.append({"column": col, "op": op, "value": v, "words": shock_words(col, op, v), "changed": changed,
                      "clipped": clipped})
    return X, notes


def shock_words(col: str, op: str, v: Any) -> str:
    if op == "set":
        return f"{col} = {v}"
    v = float(v)
    sign = "+" if v >= 0 else "−"
    return {"pct": f"{col} {sign}{abs(v):g}%", "pp": f"{col} {sign}{abs(v):g}pp", "add": f"{col} {sign}{abs(v):g}"}[op]


def default_scenarios(features: list[str], ranges: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    """Zero-config stress scenarios from the column names, each with the direction EL should move."""
    def find(rx: str) -> str | None:
        return next((c for c in features if re.search(rx, c, re.I) and (ranges.get(c) or {}).get("numeric")), None)

    out = []
    inc = find(r"income|salary|revenue")
    util = find(r"util")
    coll = find(r"collateral|house_price|property_value|security_value")
    score = find(r"credit_score|fico|bureau_score")
    if inc:
        out.append({"name": f"{inc} −10%", "shocks": [{"column": inc, "op": "pct", "value": -10}], "expect": "up",
                    "why": "lower income raises the chance of default"})
    if util:
        op = "pp" if (ranges[util].get("share")) else "pct"
        out.append({"name": f"{util} +15{'pp' if op == 'pp' else '%'}", "shocks": [{"column": util, "op": op, "value": 15}],
                    "expect": "up", "why": "higher utilisation raises the chance of default"})
    if coll:
        out.append({"name": f"{coll} −20%", "shocks": [{"column": coll, "op": "pct", "value": -20}], "expect": "up",
                    "why": "less collateral raises the loss given default"})
    if score:
        out.append({"name": f"{score} −20", "shocks": [{"column": score, "op": "add", "value": -20}], "expect": "up",
                    "why": "a lower bureau score raises the chance of default"})
    if len(out) >= 2:
        out.append({"name": "Mild downturn (all of the above)", "shocks": [s for o in out for s in o["shocks"]], "expect": "up",
                    "why": "every shock above at once"})
    return out


# ---------------------------------------------------------------------------------------------------------- models

def load(path: str) -> dict[str, Any]:
    """The three predictors and meta.json of an EL model folder (cached per folder and its mtime)."""
    from autogluon.tabular import TabularPredictor

    p = Path(path)
    m = (p / "meta.json").stat().st_mtime
    hit = _CACHE.get(str(p))
    if hit and hit[0] == m:
        return hit[1]
    meta = json.loads((p / "meta.json").read_text())
    out = {"meta": meta, **{k: TabularPredictor.load(str(p / k), require_py_version_match=False) for k in ("pd", "lgd", "ead")}}
    _CACHE[str(p)] = (m, out)
    return out


def score(models: dict[str, Any], X: pd.DataFrame) -> pd.DataFrame:
    """PD̂, LGD̂, EAD̂ and EL for each row of X (the raw columns; each model takes the ones it was trained on)."""
    meta = models["meta"]
    p = models["pd"].predict_proba(X[meta["features"]["pd"]], as_multiclass=False)
    lgd = models["lgd"].predict(X[meta["features"]["lgd"]])
    ead = models["ead"].predict(X[meta["features"]["ead"]])
    return el_frame(np.asarray(p), np.asarray(lgd), np.asarray(ead))


def stress(models: dict[str, Any], X: pd.DataFrame, scenario: dict[str, Any], seg_cols: list[str],
           base: pd.DataFrame | None = None) -> dict[str, Any]:
    """Shock X, re-score, and compare EL with the unshocked scores, overall and by segment."""
    meta = models["meta"]
    t0 = time.time()
    base = base if base is not None else score(models, X)
    Xs, notes = shock_frame(X, scenario["shocks"], meta["ranges"])
    st = score(models, Xs)
    b, s = float(base["el"].sum()), float(st["el"].sum())
    seg = {}
    for c in seg_cols:
        if c not in X.columns:
            continue
        key = X[c].astype(str).to_numpy()
        rows = []
        for v in sorted(set(key)):
            m = key == v
            bv, sv = float(base["el"][m].sum()), float(st["el"][m].sum())
            rows.append({"value": v, "n": int(m.sum()), "base": bv, "stressed": sv, "delta": sv - bv,
                         "delta_pct": (sv / bv - 1) * 100 if bv > 0 else None})
        seg[c] = sorted(rows, key=lambda r: -abs(r["delta"]))
    delta = s - b
    expect = scenario.get("expect")
    direction = "up" if delta > 0 else "down" if delta < 0 else "flat"
    return {
        "name": scenario.get("name") or ", ".join(n["words"] for n in notes), "shocks": notes, "expect": expect,
        "why": scenario.get("why"), "direction": direction,
        "as_expected": None if not expect else direction == expect,
        "base": b, "stressed": s, "delta": delta, "delta_pct": (s / b - 1) * 100 if b > 0 else None,
        "components": {k: {"base": float(base[k].mean()), "stressed": float(st[k].mean())} for k in ("pd", "lgd", "ead")},
        "segments": seg, "n": int(len(X)), "seconds": time.time() - t0,
    }


# ------------------------------------------------------------------------------------------------------------- run

def _ranges(df: pd.DataFrame, cols: list[str]) -> dict[str, dict[str, Any]]:
    out = {}
    for c in cols:
        s = df[c]
        if pd.api.types.is_numeric_dtype(s) and not pd.api.types.is_bool_dtype(s):
            lo, hi = float(s.min()), float(s.max())
            out[c] = {"numeric": True, "min": lo, "max": hi, "median": float(s.median()), "share": bool(lo >= 0 and hi <= 1)}
        else:
            out[c] = {"numeric": False, "values": [str(v) for v in s.dropna().astype(str).value_counts().index[:30]]}
    return out


def _metrics_pd(y: np.ndarray, p: np.ndarray) -> dict[str, Any]:
    from sklearn.metrics import brier_score_loss, log_loss, roc_auc_score

    return {"auc": float(roc_auc_score(y, p)), "log_loss": float(log_loss(y, np.clip(p, 1e-6, 1 - 1e-6))),
            "brier": float(brier_score_loss(y, p)), "mean_pd": float(p.mean()), "default_rate": float(y.mean())}


def _metrics_reg(y: np.ndarray, yhat: np.ndarray) -> dict[str, Any]:
    if len(y) == 0:
        return {"n": 0}
    err = yhat - y
    ss = float(((y - y.mean()) ** 2).sum())
    return {"n": int(len(y)), "mae": float(np.abs(err).mean()), "rmse": float(np.sqrt((err ** 2).mean())),
            "r2": 1 - float((err ** 2).sum()) / ss if ss > 0 else None, "mean_pred": float(yhat.mean()), "mean_true": float(y.mean()),
            "sum_pred": float(yhat.sum()), "sum_true": float(y.sum())}


def run(cfg: dict[str, Any], run_dir: Path, emit, merge) -> dict[str, Any]:
    from ..engine.worker import GROUP_FOLD_COLUMN, fit_predictor, load_frames, prepare_fit
    from ..engine.progress import ProgressCallback

    y_col, lgd_col, ead_col = cfg["label"], cfg["lgd_column"], cfg["ead_column"]
    keep = {lgd_col, ead_col}
    base = {**cfg, "drop_columns": [c for c in cfg["drop_columns"] if c not in keep]}
    train, holdout, info, tuning = load_frames(base)
    emit("data_ready", **info)
    merge(data=info)
    pos = cfg.get("positive_class")
    y_train = train[y_col]
    classes = sorted(y_train.dropna().unique().tolist(), key=str)
    if pos is None:
        pos = 1 if 1 in classes else classes[-1]
    is_def = lambda f: (f[y_col] == pos).to_numpy()  # noqa: E731

    inputs = [c for c in train.columns if c not in (y_col, lgd_col, ead_col, GROUP_FOLD_COLUMN)]
    el_dir = Path(cfg["predictor_path"])
    el_dir.mkdir(parents=True, exist_ok=True)
    specs = [
        ("pd", y_col, "binary", "log_loss", None, [lgd_col, ead_col]),
        ("lgd", lgd_col, "regression", "root_mean_squared_error", True, [y_col, ead_col]),
        ("ead", ead_col, "regression", "root_mean_squared_error", True, [y_col, lgd_col]),
    ]
    models, fits, features = {}, {}, {}
    for key, label, pt, metric, only_def, drop in specs:
        emit("log", level="info", logger="ensemble", message=f"expected loss: fitting {key.upper()} ({label}, {pt}, {metric})")
        emit("el_model_started", model=key, label=label)
        tr = train[is_def(train)] if only_def else train
        ho = holdout[is_def(holdout)] if only_def else holdout
        tu = None if tuning is None else (tuning[is_def(tuning)] if only_def else tuning)
        if tu is not None and len(tu) < 20:
            tu = None
        tr, ho = tr.drop(columns=drop), ho.drop(columns=drop)
        tu = tu.drop(columns=drop) if tu is not None else None
        tr = tr[tr[label].notna()]
        sub = {**cfg, "label": label, "problem_type": pt, "eval_metric": metric, "cost": None, "quantile": None,
               "monotone": None, "scorecard": None, "column_types": {}, "text_columns": [], "pseudolabel": None,
               "distill": None, "predictor_path": str(el_dir / key), "predictor_kwargs": {}}
        prep = prepare_fit(sub, tr, ho, tu, info)
        t0 = time.time()
        predictor, secs = fit_predictor(sub, prep, tr, sub["predictor_path"], callbacks=[ProgressCallback(emit)], emit=emit)
        models[key] = predictor
        features[key] = [c for c in tr.columns if c not in (label, GROUP_FOLD_COLUMN)]
        fits[key] = {"label": label, "problem_type": pt, "eval_metric": metric, "train_rows": int(len(tr)),
                     "best_model": predictor.model_best, "fit_seconds": secs, "n_models": len(predictor.model_names()),
                     "resolved": prep["resolved"]["fit_kwargs"] if isinstance(prep["resolved"].get("fit_kwargs"), dict) else {},
                     "seconds": time.time() - t0}
        emit("el_model_finished", model=key, best_model=predictor.model_best, seconds=secs, train_rows=int(len(tr)))

    ranges = _ranges(pd.concat([train, holdout]), inputs)
    seg_cols = [c for c in cfg.get("segments") or [] if c in holdout.columns]
    meta = {"label": y_col, "positive_class": pos if not isinstance(pos, np.generic) else pos.item(),
            "lgd_column": lgd_col, "ead_column": ead_col, "features": features, "inputs": inputs, "ranges": ranges,
            "segments": seg_cols}
    (el_dir / "meta.json").write_text(json.dumps(meta, indent=1, default=str))
    m = {"meta": meta, **models}

    # Holdout: EL against realised loss.
    sc = score(m, holdout)
    y = is_def(holdout).astype(int)
    realised = y * holdout[lgd_col].fillna(0).to_numpy() * holdout[ead_col].fillna(0).to_numpy()
    port = portfolio(sc["el"].to_numpy(), realised, y, sc["ead"].to_numpy(), sc["pd"].to_numpy())
    d = y == 1
    comp = {"pd": {**_metrics_pd(y, sc["pd"].to_numpy()), **fits["pd"]},
            "lgd": {**_metrics_reg(holdout[lgd_col].to_numpy()[d], sc["lgd"].to_numpy()[d]), **fits["lgd"]},
            "ead": {**_metrics_reg(holdout[ead_col].to_numpy()[d], sc["ead"].to_numpy()[d]), **fits["ead"]}}
    fr = sc.assign(y=y, **{c: holdout[c].to_numpy() for c in seg_cols})
    segs = segments(fr, seg_cols, realised)
    out_rows = sc.assign(row=holdout.index.to_numpy(), y=y, realised=realised)
    out_rows.to_csv(run_dir / "el_holdout.csv", index=False)

    emit("log", level="info", logger="ensemble", message="expected loss: stress scenarios")
    scen = [stress(m, holdout, s, seg_cols, base=sc) for s in default_scenarios(inputs, ranges)]
    el = {"portfolio": port, "components": comp, "segments": segs, "scenarios": scen, "segment_columns": seg_cols,
          "columns": {"pd": y_col, "lgd": lgd_col, "ead": ead_col, "positive_class": meta["positive_class"]},
          "inputs": inputs, "ranges": ranges, "detected": cfg.get("detected") or {}, "holdout_rows": int(len(holdout))}
    merge(expected_loss=el, problem_type="expected_loss", best_model=f"PD {fits['pd']['best_model']} · LGD "
          f"{fits['lgd']['best_model']} · EAD {fits['ead']['best_model']}", features=inputs,
          holdout={"metrics": {"el_ratio": port["ratio"], "auc": comp["pd"]["auc"]}},
          eval_metric={"name": "el_ratio", "greater_is_better": True, "optimum": 1.0})
    return {"el_ratio": port["ratio"], "el": port["el"], "realised": port["realised"]}


# --------------------------------------------------------------------------------------------- serving (Registry)

def _frame(models: dict[str, Any], rows: pd.DataFrame | list[dict[str, Any]]) -> pd.DataFrame:
    meta = models["meta"]
    df = pd.DataFrame(rows) if isinstance(rows, list) else rows
    if df.empty:
        raise ValueError("Give at least one row.")
    if not any(c in df.columns for c in meta["inputs"]):
        raise ValueError(f"None of the model's inputs ({', '.join(meta['inputs'][:5])}, …) are in the rows.")
    X = df.reindex(columns=meta["inputs"])
    for c, r in meta["ranges"].items():
        if r.get("numeric") and c in X.columns:
            X[c] = pd.to_numeric(X[c], errors="coerce")
    return X


def score_rows(path: str, rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Rows → PD̂, LGD̂, EAD̂ and EL per row (the online path of an expected-loss version)."""
    t0 = time.perf_counter()
    m = load(path)
    sc = score(m, _frame(m, rows))
    return {"problem_type": "expected_loss", "n_rows": len(sc), "results": sc.round(6).to_dict(orient="records"),
            "total_el": float(sc["el"].sum()), "predict_ms": (time.perf_counter() - t0) * 1e3}


def score_batch(path: str, df: pd.DataFrame) -> tuple[pd.DataFrame, dict[str, Any]]:
    """A file of loans → the file with pd, lgd, ead, expected_loss columns added, and a portfolio summary by segment."""
    m = load(path)
    sc = score(m, _frame(m, df))
    out = df.copy()
    out["pd"], out["lgd"], out["ead"], out["expected_loss"] = sc["pd"].to_numpy(), sc["lgd"].to_numpy(), sc["ead"].to_numpy(), sc["el"].to_numpy()
    seg_cols = [c for c in m["meta"].get("segments") or [] if c in df.columns]
    fr = sc.assign(**{c: df[c].to_numpy() for c in seg_cols})
    return out, {"total_el": float(sc["el"].sum()), "ead_total": float(sc["ead"].sum()), "mean_pd": float(sc["pd"].mean()),
                 "segments": segments(fr, seg_cols)}
