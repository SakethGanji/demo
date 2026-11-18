"""Using a registered version, in worker processes: Try It (one row, with its prediction, probabilities and the
top SHAP reasons) and batch scoring of a CSV (progress, a preview, a downloadable CSV, and PSI drift against the
training data)."""

from __future__ import annotations

import json
import time
import traceback
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from ..db import Database
from ..engine import explain
from ..engine.jsonsafe import jsonable
from ..engine.pool import load_predictor
from ..engine.store import now
from .store import RegistryStore


def _coerce(frame: pd.DataFrame, train: pd.DataFrame) -> pd.DataFrame:
    """Cast a posted row to the training frame's dtypes (numbers typed as text in a form become numbers)."""
    out = frame.copy()
    for c in out.columns:
        if c in train.columns and pd.api.types.is_numeric_dtype(train[c].dtype):
            out[c] = pd.to_numeric(out[c], errors="coerce")
    return out


def try_it(cfg: dict[str, Any], predictor_path: str, row: dict[str, Any], target: str | None = None,
           explain_row: bool = True, nsamples: int = explain.SHAP_NSAMPLES, rconf: dict[str, Any] | None = None,
           kinds: dict[str, str] | None = None) -> dict[str, Any]:
    p = load_predictor(predictor_path)
    features = list(p.original_features)
    given = {k: v for k, v in row.items() if k in features and v is not None and v != ""}
    missing = [c for c in features if c not in given]
    _, train, bg = explain._shap_background(cfg, features, explain.SHAP_BACKGROUND)
    x = _coerce(pd.DataFrame([{c: given.get(c, np.nan) for c in features}]), train)
    t0 = time.perf_counter()
    out: dict[str, Any] = {"model": p.model_best, "problem_type": p.problem_type, "missing_columns": missing,
                           "features": features}
    if p.problem_type in ("binary", "multiclass"):
        proba = p.predict_proba(x)
        pred = p.predict_from_proba(proba)
        out["classes"] = jsonable(list(proba.columns))
        out["prediction"] = jsonable(pred.iloc[0])
        out["probabilities"] = {str(c): float(v) for c, v in proba.iloc[0].items()}
        if p.problem_type == "binary":
            out["positive_class"] = jsonable(p.positive_class)
            out["threshold"] = float(p.decision_threshold)
    elif p.problem_type == "quantile":  # F9: every level, and the centre as the prediction
        from ..engine import quantile as qt

        q = qt.qframe(p, x)
        out["quantile_levels"] = [float(t) for t in q.columns]
        out["centre"] = qt.centre(list(q.columns))
        out["quantiles"] = qt.as_records(q)[0]
        out["prediction"] = float(qt.point(q).iloc[0])
        out["interval"] = qt.main_interval(list(q.columns))
        out["probabilities"] = None
    else:
        out["prediction"] = float(p.predict(x).iloc[0])
        out["probabilities"] = None
    out["predict_ms"] = (time.perf_counter() - t0) * 1e3
    if explain_row:
        target_c, classes = explain.resolve_target(p, x, target)
        sh = explain.shap_explain(p, train, bg, x, target_c, classes, nsamples)
        sh["reasons"] = [{**c, "direction": "raises" if c["phi"] > 0 else "lowers"} for c in sh["contributions"][:5]]
        out["shap"] = sh
    if p.problem_type == "binary" and rconf is not None:  # F10: the decision and its reason codes, as batch / score
        try:
            rr = reasons_rows(predictor_path, cfg, rconf, kinds or {}, [row], only_adverse=False)
            out["reasons"] = {**{k: v for k, v in rr.items() if k not in ("adverse", "p_positive", "codes")},
                              "adverse": rr["adverse"][0], "p_positive": rr["p_positive"][0], "codes": rr["codes"][0]}
        except Exception as e:  # noqa: BLE001
            out["reasons"] = {"error": f"{type(e).__name__}: {e}"}
    return jsonable(out)


def _train_frame(cfg: dict[str, Any], features: list[str]) -> pd.DataFrame:
    df = pd.read_csv(cfg["csv_path"])
    splits = json.loads(Path(cfg["splits_path"]).read_text())
    is_h = np.zeros(len(df), dtype=bool)
    is_h[np.asarray(splits["holdout"], dtype=int)] = True
    return df.loc[~is_h & df[cfg["label"]].notna(), [c for c in features if c in df.columns]]


def score_batch(db_path: str, batch_id: str, predictor_path: str, cfg: dict[str, Any], csv_path: str, out_dir: str,
                kinds: dict[str, str], version: dict[str, Any], rconf: dict[str, Any] | None = None) -> dict[str, Any]:
    reg = RegistryStore(Database(Path(db_path)))
    out_dir_p = Path(out_dir)
    try:
        reg.update_batch(batch_id, status="running", stage="loading", progress=0.02)
        t_start = time.time()
        p = load_predictor(predictor_path)
        features = list(p.original_features)
        df = pd.read_csv(csv_path)
        if len(df) == 0:
            raise ValueError("The file has no rows.")
        train = _train_frame(cfg, features)
        present = [c for c in features if c in df.columns]
        if not present:
            raise ValueError(f"None of the model's features are in the file (it needs {', '.join(features)}).")
        missing = [c for c in features if c not in df.columns]
        extra = [c for c in df.columns if c not in features]
        X = df.reindex(columns=features)
        X = _coerce(X, train)
        reg.update_batch(batch_id, n_rows=len(df), stage="scoring", progress=0.05)
        n = len(X)
        chunk = max(200, int(np.ceil(n / 20)))
        preds, probas, quants = [], [], []
        clf = p.problem_type in ("binary", "multiclass")
        quant = p.problem_type == "quantile"
        if quant:
            from ..engine import quantile as qt
        t0 = time.time()
        for i in range(0, n, chunk):
            part = X.iloc[i:i + chunk]
            if clf:
                pr = p.predict_proba(part)
                probas.append(pr)
                preds.append(p.predict_from_proba(pr))
            elif quant:  # F9: one column per level; the prediction is the centre level
                q = qt.qframe(p, part)
                quants.append(q)
                preds.append(qt.point(q))
            else:
                preds.append(p.predict(part))
            reg.update_batch(batch_id, progress=0.05 + 0.75 * min(1.0, (i + len(part)) / n),
                             stage=f"scoring {min(n, i + len(part)):,} / {n:,} rows")
        score_s = time.time() - t0
        pred = pd.concat(preds)
        out = df.copy()
        out["prediction"] = pred.to_numpy()
        cur_pred = pd.DataFrame({"y_pred": pred.to_numpy()})
        if clf:
            pr = pd.concat(probas)
            for c in pr.columns:
                out[f"p_{c}"] = pr[c].to_numpy().round(6)
                cur_pred[f"p_{c}"] = pr[c].to_numpy()
        if quant:
            qa = pd.concat(quants)
            for t in qa.columns:
                out[qt.col(t)] = qa[t].to_numpy()
        reasons_summary = None
        if p.problem_type == "binary" and rconf is not None and rconf.get("enabled", True):
            reasons_summary = _batch_reasons(reg, batch_id, predictor_path, cfg, rconf, kinds, X, out, pred)
        out_path = out_dir_p / "scored.csv"
        out.to_csv(out_path, index=False)

        reg.update_batch(batch_id, stage="drift (PSI per feature)", progress=0.85)
        from . import drift

        vdir = Path(predictor_path).parent
        ref_path = vdir / "holdout_predictions.csv"
        ref_pred = pd.read_csv(ref_path) if ref_path.exists() else pd.read_csv(Path(cfg["run_dir"]) / "holdout_predictions.csv")
        pos = str(p.positive_class) if p.problem_type == "binary" else None
        rep = drift.drift_report(train, X, {c: kinds.get(c, "number") for c in present}, problem_type=p.problem_type,
                                 ref_pred=ref_pred, cur_pred=cur_pred, positive=pos)
        for c in missing:
            rep["features"].append({"feature": c, "kind": kinds.get(c, "number"), "psi": None, "band": "missing",
                                    "note": "column not in the file; scored as missing"})
        summary: dict[str, Any] = {"n_rows": n, "seconds": time.time() - t_start, "score_seconds": score_s,
                                   "us_per_row": score_s / n * 1e6, "missing_columns": missing, "extra_columns": extra,
                                   "model": p.model_best, "problem_type": p.problem_type}
        if reasons_summary is not None:
            summary["reasons"] = reasons_summary
        if clf:
            vc = pred.astype(str).value_counts()
            summary["predicted"] = {str(k): int(v) for k, v in vc.items()}
            summary["classes"] = [str(c) for c in pr.columns]
            if pos is not None:
                summary["positive_class"] = pos
                summary["mean_p"] = float(cur_pred[f"p_{pos}"].mean())
                summary["mean_p_reference"] = float(ref_pred[f"p_{pos}"].mean()) if f"p_{pos}" in ref_pred else None
                summary["flagged_share"] = float((pred.astype(str) == pos).mean())
                summary["threshold"] = float(p.decision_threshold)
        else:
            summary["mean_prediction"] = float(pred.mean())
            summary["std_prediction"] = float(pred.std())
        if quant:
            iv = qt.main_interval(list(qa.columns))
            summary["quantile_levels"] = [float(t) for t in qa.columns]
            summary["prediction_is"] = f"q̂_{qt.centre(list(qa.columns)):g} (the centre level)"
            if iv:
                w = qa[iv["hi"]].to_numpy() - qa[iv["lo"]].to_numpy()
                summary["interval"] = {**iv, "mean_width": float(w.mean()), "median_width": float(np.median(w))}
                if "q_" + f"{iv['lo']:g}" in ref_pred.columns:
                    rw = ref_pred[qt.col(iv["hi"])] - ref_pred[qt.col(iv["lo"])]
                    summary["interval"]["mean_width_reference"] = float(rw.mean())
        # A histogram of the output for the UI (batch vs reference)
        if p.problem_type == "binary":
            edges = np.linspace(0, 1, 21)
            a = np.histogram(cur_pred[f"p_{pos}"], bins=edges)[0]
            e = np.histogram(ref_pred[f"p_{pos}"], bins=edges)[0]
            summary["histogram"] = {"edges": edges.tolist(), "actual": a.tolist(), "expected": e.tolist()}
        elif p.problem_type in ("regression", "quantile"):
            lo = float(min(ref_pred["y_pred"].min(), pred.min()))
            hi = float(max(ref_pred["y_pred"].max(), pred.max()))
            edges = np.linspace(lo, hi, 26)
            summary["histogram"] = {"edges": edges.tolist(), "actual": np.histogram(pred, bins=edges)[0].tolist(),
                                    "expected": np.histogram(ref_pred["y_pred"], bins=edges)[0].tolist()}
        preview = jsonable(out.head(50).to_dict(orient="records"))
        result = {"summary": summary, "drift": rep, "preview": preview, "columns": list(out.columns),
                  "output_file": out_path.name}
        (out_dir_p / "drift.json").write_text(json.dumps(jsonable(rep)))
        reg.update_batch(batch_id, status="succeeded", stage="done", progress=1.0, result=result, finished_at=now())
        v = reg.get_version(version["id"])
        brec = reg.get_batch(batch_id)
        reg.audit(actor=brec["created_by"] or "unknown", action="batch_scored", version=v,
                  comment=f"{n:,} rows from {brec['filename']}",
                  details={"batch_id": batch_id, "n_rows": n, "max_psi": rep.get("max_psi"),
                           "prediction_psi": rep["prediction"]["psi"], "bands": rep["counts"]})
        return {"ok": True}
    except Exception as e:  # noqa: BLE001
        (out_dir_p / "error.txt").write_text(traceback.format_exc())
        reg.update_batch(batch_id, status="failed", stage="failed", error=f"{type(e).__name__}: {e}", finished_at=now())
        return {"ok": False, "error": str(e)}


def _batch_reasons(reg: RegistryStore, batch_id: str, predictor_path: str, cfg: dict[str, Any], rconf: dict[str, Any],
                   kinds: dict[str, str], X: pd.DataFrame, out: pd.DataFrame, pred: pd.Series) -> dict[str, Any]:
    """F10: reason codes for every adverse decision, as extra columns of the scored file: `adverse`, and for k = 1…K
    `reason_k_code`, `reason_k` (the statement), `reason_k_feature`, `reason_k_phi` (φ of P(positive))."""
    reg.update_batch(batch_id, stage="reason codes: background and attribution set-up", progress=0.8)
    t0 = time.time()
    rs = reasoner(predictor_path, cfg, rconf, kinds)
    setup_s = time.time() - t0
    Xr = X.reset_index(drop=True)
    adverse = pred.astype(str).to_numpy() == rs.adverse  # the served decision at the version's threshold
    idx = np.flatnonzero(adverse)
    K = rs.top_k
    cols: dict[str, list[Any]] = {f"reason_{k}{suf}": [None] * len(Xr) for k in range(1, K + 1)
                                  for suf in ("_code", "", "_feature", "_phi")}
    n_codes = np.zeros(len(Xr), dtype=int)
    n_factors: list[int | None] = [None] * len(Xr)
    t1 = time.time()
    chunk = 100
    methods, notes, add_max, mono_tot = set(), set(), 0.0, {}
    freq: dict[str, int] = {}
    consistent = True
    for i in range(0, len(idx), chunk):
        part = idx[i:i + chunk]
        Xe = Xr.iloc[part].reset_index(drop=True)
        ex = rs.att.explain(Xe)
        methods.add(ex["method"])
        if ex["note"]:
            notes.add(ex["note"])
        add_max = max(add_max, ex["additivity_max"])
        codes = rs.codes_for(Xe, ex["phi"])
        for f, m in rs.monotone_check(Xe, ex["phi"]).items():
            agg = mono_tot.setdefault(f, {**m, "rows_above_background": 0, "rows_below_background": 0, "violations": 0})
            for k in ("rows_above_background", "rows_below_background", "violations"):
                agg[k] += m[k]
        n_pos = (rs.s * ex["phi"] > 0).sum(axis=1)
        for r_i, cs, npos in zip(part, codes, n_pos):
            n_factors[r_i] = int(npos)
            n_codes[r_i] = len(cs)
            for c in cs:
                cols[f"reason_{c['rank']}_code"][r_i] = c["code"]
                cols[f"reason_{c['rank']}"][r_i] = c["statement"]
                cols[f"reason_{c['rank']}_feature"][r_i] = c["feature"]
                cols[f"reason_{c['rank']}_phi"][r_i] = round(c["phi"], 6)
                freq[c["code"]] = freq.get(c["code"], 0) + 1
                consistent &= rs.s * c["phi"] > 0
        done = min(len(idx), i + chunk)
        el = time.time() - t1
        reg.update_batch(batch_id, progress=0.8 + 0.05 * done / max(len(idx), 1),
                         stage=f"reason codes {done:,} / {len(idx):,} adverse rows ({done / el if el else 0:.1f} rows/s)")
    secs = time.time() - t1
    out["adverse"] = adverse
    out["reason_factors"] = n_factors  # features with s·φ > 0 (codes = the top min(K, this))
    for c, v in cols.items():
        out[c] = v
    by_code = {r["code"]: r for r in rs.mapping.values()}
    top = sorted(freq.items(), key=lambda kv: -kv[1])
    return jsonable({**{k: v for k, v in rs.info().items()}, "n_adverse": int(len(idx)), "adverse_share": float(adverse.mean()),
                     "n_explained": int(len(idx)), "seconds": secs, "setup_seconds": setup_s,
                     "rows_per_s": len(idx) / secs if secs > 0 else None, "methods": sorted(methods), "notes": sorted(notes),
                     "additivity_max": add_max, "codes_per_row": {str(k): int(v) for k, v in zip(*np.unique(n_codes[idx], return_counts=True))} if len(idx) else {},
                     "signs_consistent": bool(consistent), "monotone": mono_tot,
                     "top_codes": [{"code": c, "feature": by_code.get(c, {}).get("feature"), "rows": k, "share": k / max(len(idx), 1)}
                                   for c, k in top[:10]],
                     "rows_fewer_than_k": int(sum(1 for i in idx if n_codes[i] < K)),
                     "columns": ["adverse", "reason_factors"] + [c for c in cols]})


def score_flow_batch(db_path: str, batch_id: str, kind: str, version_path: str, cfg: dict[str, Any], csv_path: str,
                     out_dir: str, version: dict[str, Any]) -> dict[str, Any]:
    """(In a worker.) Batch scoring and drift for forecast and anomaly versions, in the registry's batch format.

    Anomaly: every row scored with the version's IsolationForest (s, E[h], percentile among the fitted rows, the flag
    at s*); PSI of each feature against the rows the forest was fitted on, and of the score s against the fitted
    rows' scores. Forecast: the file is new history (same mapping); the version's TimeSeriesPredictor forecasts H
    steps per series; PSI of the target and the numeric known covariates against the training history, and of the
    forecast median against the run's own forecast."""
    from . import drift

    reg = RegistryStore(Database(Path(db_path)))
    out_dir_p = Path(out_dir)
    try:
        reg.update_batch(batch_id, status="running", stage="loading", progress=0.05)
        t_start = time.time()
        df = pd.read_csv(csv_path)
        if len(df) == 0:
            raise ValueError("The file has no rows.")
        train = pd.read_csv(cfg["csv_path"])
        out_path = out_dir_p / "scored.csv"
        reg.update_batch(batch_id, n_rows=len(df), stage="scoring", progress=0.2)
        if kind == "anomaly":
            import joblib

            from ..anomaly import core as an

            model = joblib.load(Path(version_path) / "model.joblib")
            t0 = time.time()
            out, top, summ = an.score_frame(version_path, df)
            score_s = time.time() - t0
            out.to_csv(out_path, index=False)
            feats = [c for c in model.num + model.cat if c in df.columns and c in train.columns]
            kinds = {c: ("number" if c in model.num else "category") for c in feats}
            ref_pred = pd.DataFrame({"y_pred": np.asarray(model.train_scores, dtype=float)})
            cur_pred = pd.DataFrame({"y_pred": out["anomaly_score"].to_numpy(dtype=float)})
            reg.update_batch(batch_id, stage="drift (PSI per feature)", progress=0.8)
            rep = drift.drift_report(train, df, kinds, problem_type="regression", ref_pred=ref_pred, cur_pred=cur_pred, positive=None)
            rep["prediction"]["output"] = "isolation score s(x, ψ)"
            rep["prediction"]["feature"] = "s"
            rep["prediction_reference"] = "the scores of the rows the forest was fitted on"
            rep["reference"] = "the rows the forest was fitted on (the run's dataset)"
            edges = np.linspace(min(ref_pred.y_pred.min(), cur_pred.y_pred.min()), max(ref_pred.y_pred.max(), cur_pred.y_pred.max()), 26)
            summary = {"n_rows": int(len(df)), "seconds": time.time() - t_start, "score_seconds": score_s,
                       "us_per_row": score_s / len(df) * 1e6, "model": "IsolationForest", "problem_type": "anomaly",
                       "missing_columns": [], "extra_columns": [c for c in df.columns if c not in feats],
                       "flagged_share": summ["flagged_share"], "threshold": summ["threshold"], "n_flagged": summ["n_flagged"],
                       "expected_share": summ["expected_share"], "flag_rule": f"share with s ≥ s* = {summ['threshold']:.4f}",
                       "mean_prediction": summ["mean_score"], "std_prediction": float(out["anomaly_score"].std()),
                       "prediction_symbol": "s", "top": top[:10],
                       "histogram": {"edges": edges.tolist(), "actual": np.histogram(cur_pred.y_pred, bins=edges)[0].tolist(),
                                     "expected": np.histogram(ref_pred.y_pred, bins=edges)[0].tolist()}}
        else:
            from ..forecast import core as fc

            t0 = time.time()
            res = fc.predict_new(version_path, cfg, csv_path, str(out_path))
            score_s = time.time() - t0
            out = pd.read_csv(out_path)
            mp = cfg["mapping"]
            cols = [mp["target"]] + [c for c in (mp.get("known_covariates") or []) + (mp.get("past_covariates") or [])
                                     if c in df.columns and c in train.columns]
            obs_train = train[train[mp["target"]].notna()]
            obs_new = df[df[mp["target"]].notna()] if mp["target"] in df.columns else df
            kinds = {c: ("number" if pd.api.types.is_numeric_dtype(obs_train[c]) else "category") for c in cols}
            ref_f = Path(cfg["run_dir"]) / "forecast.csv"
            ref_pred = pd.DataFrame({"y_pred": pd.read_csv(ref_f)["mean"].to_numpy(dtype=float)}) if ref_f.exists() else pd.DataFrame({"y_pred": out["mean"].to_numpy(dtype=float)})
            cur_pred = pd.DataFrame({"y_pred": out["mean"].to_numpy(dtype=float)})
            reg.update_batch(batch_id, stage="drift (PSI of the history)", progress=0.8)
            rep = drift.drift_report(obs_train, obs_new, kinds, problem_type="regression", ref_pred=ref_pred, cur_pred=cur_pred, positive=None)
            rep["prediction"]["output"] = "forecast mean ŷ over every series and step"
            rep["prediction_reference"] = "the run's own forecast past the end of its data"
            rep["reference"] = "the observed history the predictor was trained on"
            summary = {"n_rows": int(len(df)), "seconds": time.time() - t_start, "score_seconds": score_s,
                       "us_per_row": score_s / max(len(out), 1) * 1e6, "model": res["model"], "problem_type": "forecast",
                       "missing_columns": [], "extra_columns": [], "items": res["items"], "forecast_rows": res["rows"],
                       "known_covariates_source": res["known_covariates_source"],
                       "mean_prediction": float(out["mean"].mean()), "std_prediction": float(out["mean"].std()),
                       "prediction_symbol": "ŷ"}
        preview = jsonable(out.head(50).to_dict(orient="records"))
        result = {"summary": summary, "drift": rep, "preview": preview, "columns": list(out.columns),
                  "output_file": out_path.name, "kind": kind}
        (out_dir_p / "drift.json").write_text(json.dumps(jsonable(rep)))
        reg.update_batch(batch_id, status="succeeded", stage="done", progress=1.0, result=jsonable(result), finished_at=now())
        v = reg.get_version(version["id"])
        brec = reg.get_batch(batch_id)
        reg.audit(actor=brec["created_by"] or "unknown", action="batch_scored", version=v,
                  comment=f"{len(df):,} rows from {brec['filename']} ({kind})",
                  details={"batch_id": batch_id, "n_rows": int(len(df)), "max_psi": rep.get("max_psi"),
                           "prediction_psi": rep["prediction"]["psi"], "bands": rep["counts"]})
        return {"ok": True}
    except Exception as e:  # noqa: BLE001
        (out_dir_p / "error.txt").write_text(traceback.format_exc())
        reg.update_batch(batch_id, status="failed", stage="failed", error=f"{type(e).__name__}: {e}", finished_at=now())
        return {"ok": False, "error": str(e)}


# --------------------------------------------------------------------------- online scoring (F3)


def _raw_numeric(p) -> set[str]:
    try:
        tm = p._learner.feature_generator.feature_metadata_in.type_map_raw
    except Exception:  # noqa: BLE001
        return set()
    return {c for c, t in tm.items() if t in ("int", "float")}


def score_rows(predictor_path: str, rows: list[dict[str, Any]]) -> dict[str, Any]:
    """The online path: rows → predictions (and probabilities), no SHAP and no data files read. The predictor comes
    from the worker's cache, persisted and warmed when it was loaded. `predict_ms` is the predictor call alone;
    `worker_ms` adds building the frame and the response."""
    t_all = time.perf_counter()
    return score_with(load_predictor(predictor_path), rows, t_all)


def score_with(p, rows: list[dict[str, Any]], t_all: float | None = None) -> dict[str, Any]:
    t_all = t_all or time.perf_counter()
    if not rows:
        raise ValueError("Give at least one row.")
    features = list(p.original_features)
    X = pd.DataFrame([{c: r.get(c, np.nan) for c in features} for r in rows], columns=features)
    for c in _raw_numeric(p) & set(features):
        if X[c].dtype == object:
            X[c] = pd.to_numeric(X[c], errors="coerce")
    t0 = time.perf_counter()
    out: dict[str, Any] = {"model": p.model_best, "problem_type": p.problem_type, "n_rows": len(X)}
    if p.problem_type in ("binary", "multiclass"):
        proba = p.predict_proba(X)
        pred = p.predict_from_proba(proba)
        out["predict_ms"] = (time.perf_counter() - t0) * 1e3
        out["classes"] = jsonable(list(proba.columns))
        out["predictions"] = jsonable(pred.tolist())
        out["probabilities"] = [{str(c): float(v) for c, v in r.items()} for r in proba.to_dict(orient="records")]
    elif p.problem_type == "quantile":  # F9
        from ..engine import quantile as qt

        q = qt.qframe(p, X)
        out["predict_ms"] = (time.perf_counter() - t0) * 1e3
        out["quantile_levels"] = [float(t) for t in q.columns]
        out["predictions"] = jsonable(qt.point(q).tolist())
        out["quantiles"] = qt.as_records(q)
        out["probabilities"] = None
    else:
        pred = p.predict(X)
        out["predict_ms"] = (time.perf_counter() - t0) * 1e3
        out["predictions"] = jsonable(pred.tolist())
        out["probabilities"] = None
    out["worker_ms"] = (time.perf_counter() - t_all) * 1e3
    return out


def benchmark_version(predictor_path: str, cfg: dict[str, Any], n: int = 300) -> dict[str, Any]:
    """Served latency of a version, in a predict worker: the warm-up the worker did on load, then one-row calls on
    holdout rows (p50 / p95) and batches of 1 → 1,000 (ms per call, µs per row)."""
    from ..engine import serving
    from ..splits import load_splits
    from . import fastpath

    t0 = time.perf_counter()
    p = load_predictor(predictor_path)
    load_s = time.perf_counter() - t0
    h = serving.holdout_frame(cfg["csv_path"], load_splits(Path(cfg["splits_path"])), cfg["label"], cfg["drop_columns"])
    X = h.drop(columns=[cfg["label"]])
    fn = fastpath.predict_fn(p)
    from ..engine.pool import _WARM

    return jsonable({"model": p.model_best, "one_row": fastpath.one_row(fn, X, n=n, budget_s=8.0),
                     "batches": fastpath.batches(fn, X), "warm_on_load": _WARM.get(str(predictor_path)),
                     "cache_load_s": load_s, "features_in": list(p._learner.feature_generator.features_in),
                     "n_features": len(p._learner.feature_generator.features_out), "pid": __import__("os").getpid(),
                     "where": "a predict worker (the API's worker pool), predictor cached, persisted and warmed"})


# --------------------------------------------------------------------------- reason codes (F10)

_REASONERS: dict[tuple, Any] = {}


def reasoner(predictor_path: str, cfg: dict[str, Any], rconf: dict[str, Any], kinds: dict[str, str]):
    """A Reasoner for the version (cached per worker by the predictor's files and the mapping)."""
    from ..engine import reasons as rc
    from ..engine.pool import _signature

    p = load_predictor(predictor_path)
    key = (str(predictor_path), _signature(predictor_path), json.dumps(rconf, sort_keys=True, default=str))
    if key in _REASONERS:
        return _REASONERS[key]
    features = list(p.original_features)
    cons = ((cfg.get("monotone") or {}).get("constraints")) or {}
    r = rc.Reasoner(p, _train_frame(cfg, features), rconf, kinds, cons)
    for k in [k for k in _REASONERS if k[0] == str(predictor_path)]:
        _REASONERS.pop(k, None)
    _REASONERS[key] = r
    return r


def reasons_info(predictor_path: str, cfg: dict[str, Any], rconf: dict[str, Any], kinds: dict[str, str]) -> dict[str, Any]:
    rs = reasoner(predictor_path, cfg, rconf, kinds)
    return jsonable({**rs.info(), "mapping": list(rs.mapping.values()), "features": rs.features,
                     "classes": [str(c) for c in rs.p.class_labels]})


def reasons_rows(predictor_path: str, cfg: dict[str, Any], rconf: dict[str, Any], kinds: dict[str, str],
                 rows: list[dict[str, Any]], only_adverse: bool = True, reference: bool = False) -> dict[str, Any]:
    """Decisions and reason codes for posted rows (the online path with reasons=true, and Try It). With `reference`,
    also the same game estimated with 4,096 coalitions (same background) for every explained row, to audit the fast
    estimator: φ of each cited feature under the reference, and the reference's own top codes."""
    from ..engine import reasons as rc

    rs = reasoner(predictor_path, cfg, rconf, kinds)
    X = pd.DataFrame([{c: r.get(c, np.nan) for c in rs.features} for r in rows], columns=rs.features)
    t0 = time.perf_counter()
    r = rs.run(X, only_adverse=only_adverse)
    ref_out = None
    if reference and len(r["explained_index"]):
        t1 = time.perf_counter()
        ref = rc.Attributor(rs.p, rs.att.bg, background=len(rs.att.bg), coalitions=4096, seed=rs.att.seed + 3, method="kernel")
        ref.bg, ref.base_kernel = rs.att.bg, rs.att.base_kernel
        Xr = rc._coerce(X.copy(), rs.att.numeric).iloc[r["explained_index"]].reset_index(drop=True)
        phr = ref.explain(Xr)["phi"]
        rcodes = rs.codes_for(Xr, phr)
        per = [None] * len(X)
        for k, i in enumerate(r["explained_index"]):
            per[i] = {"phi": {f: float(phr[k, j]) for j, f in enumerate(rs.features)}, "codes": [c["code"] for c in rcodes[k]]}
        ref_out = {"rows": per, "coalitions": int(len(ref.masks)), "exact": ref.exact_enum, "seconds": time.perf_counter() - t1}
    return jsonable({"reference_check": ref_out, "adverse": [bool(a) for a in r["adverse"]], "p_positive": [float(v) for v in r["p_positive"]],
                     "codes": r["codes"], "method": r["method"], "note": r["note"], "reasons_ms": (time.perf_counter() - t0) * 1e3,
                     "additivity_max": r["additivity_max"], "base_value": float(r["base"][0]) if len(r["base"]) else None,
                     "monotone": r["monotone"], **{k: v for k, v in rs.info().items() if k != "attribution"},
                     "attribution": rs.info()["attribution"]})


def reasons_stability(predictor_path: str, cfg: dict[str, Any], rconf: dict[str, Any], kinds: dict[str, str],
                      rows: int = 300, noise: float = 0.05, repeats: int = 3, out_path: str | None = None) -> dict[str, Any]:
    """The stability check on holdout rows (the adverse ones among the first `rows`), stored beside the version."""
    from ..engine import reasons as rc
    from ..engine import serving
    from ..splits import load_splits

    rs = reasoner(predictor_path, cfg, rconf, kinds)
    h = serving.holdout_frame(cfg["csv_path"], load_splits(Path(cfg["splits_path"])), cfg["label"], cfg["drop_columns"])
    X = h.reindex(columns=rs.features).head(rows)
    out = rc.stability(rs, X, noise=noise, repeats=repeats)
    out.update({"holdout_rows": int(len(X)), "method": rs.att.describe(), "threshold": rs.threshold,
                "adverse_class": rs.adverse, "computed_at": now()})
    out = jsonable(out)
    if out_path:
        Path(out_path).write_text(json.dumps(out))
    return out
