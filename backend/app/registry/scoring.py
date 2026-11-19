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
from ..splits import holdout_mask
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
    is_h = holdout_mask(splits, len(df))
    return df.loc[~is_h & df[cfg["label"]].notna(), [c for c in features if c in df.columns]]


def score_batch(db_path: str, batch_id: str, predictor_path: str, cfg: dict[str, Any], csv_path: str, out_dir: str,
                kinds: dict[str, str], version: dict[str, Any], rconf: dict[str, Any] | None = None,
                shadow: dict[str, Any] | None = None, recourse_conf: dict[str, Any] | None = None) -> dict[str, Any]:
    """`recourse_conf` (G1): the model's saved recourse actions; the first RECOURSE_BATCH_K adverse rows get
    counterfactuals (a `recourse` column with the nearest valid option). `shadow` (F18): {version_id, path, model_name, version}: a Staging challenger that scores the same rows; its
    p̂ goes to shadow.csv and the comparison to result.shadow. It never changes the batch's own output."""
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
            if recourse_conf is not None:
                reg.update_batch(batch_id, stage=f"recourse for the first {RECOURSE_BATCH_K} adverse rows", progress=0.84)
                try:
                    reasons_summary["recourse"] = _batch_recourse(predictor_path, cfg, rconf, kinds, recourse_conf, X, out)
                except Exception as e:  # noqa: BLE001 — the batch stands; the summary says why recourse is missing
                    reasons_summary["recourse"] = {"error": f"{type(e).__name__}: {e}"}
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
                from .monitoring import score_quantiles

                summary["score_quantiles"] = score_quantiles(cur_pred[f"p_{pos}"].to_numpy())
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
        if shadow and p.problem_type == "binary":  # F18: the challenger scores the same rows in the shadow
            reg.update_batch(batch_id, stage=f"shadow scoring with {shadow.get('label') or shadow['version_id']}", progress=0.93)
            try:
                result["shadow"] = _shadow(shadow, df, train, out_dir_p, cur_pred[f"p_{pos}"].to_numpy(),
                                           float(p.decision_threshold), pos)
            except Exception as e:  # noqa: BLE001 — the batch itself stands; the comparison says why it is missing
                result["shadow"] = {"version_id": shadow["version_id"], "error": f"{type(e).__name__}: {e}"}
        return _finish_batch(reg, batch_id, out_dir_p, version, rep, result, n, "")
    except Exception as e:  # noqa: BLE001
        return _fail_batch(reg, batch_id, out_dir_p, e)


def _finish_batch(reg: RegistryStore, batch_id: str, out_dir_p: Path, version: dict[str, Any], rep: dict[str, Any],
                  result: dict[str, Any], n: int, suffix: str) -> dict[str, Any]:
    """Store a scored batch's result and drift report and write its audit entry."""
    (out_dir_p / "drift.json").write_text(json.dumps(jsonable(rep)))
    reg.update_batch(batch_id, status="succeeded", stage="done", progress=1.0, result=jsonable(result), finished_at=now())
    v = reg.get_version(version["id"])
    brec = reg.get_batch(batch_id)
    reg.audit(actor=brec["created_by"] or "unknown", action="batch_scored", version=v,
              comment=f"{n:,} rows from {brec['filename']}{suffix}",
              details={"batch_id": batch_id, "n_rows": n, "max_psi": rep.get("max_psi"),
                       "prediction_psi": rep["prediction"]["psi"], "bands": rep["counts"]})
    return {"ok": True}


def _fail_batch(reg: RegistryStore, batch_id: str, out_dir_p: Path, e: Exception) -> dict[str, Any]:
    (out_dir_p / "error.txt").write_text(traceback.format_exc())
    reg.update_batch(batch_id, status="failed", stage="failed", error=f"{type(e).__name__}: {e}", finished_at=now())
    return {"ok": False, "error": str(e)}


def _shadow(shadow: dict[str, Any], df: pd.DataFrame, train: pd.DataFrame, out_dir: Path, p_champ: np.ndarray,
            t_champ: float, pos: str) -> dict[str, Any]:
    from .monitoring import shadow_compare

    t0 = time.time()
    q = load_predictor(shadow["path"])
    if q.problem_type != "binary" or str(q.positive_class) != pos:
        raise ValueError(f"The challenger is not a binary model of the same positive class ({pos}).")
    feats = list(q.original_features)
    Xq = _coerce(df.reindex(columns=feats), train.reindex(columns=[c for c in feats if c in train.columns]))
    pq = q.predict_proba(Xq)[q.positive_class].to_numpy()
    t_q = float(q.decision_threshold)
    pd.DataFrame({"row": np.arange(len(df)), "p_champion": p_champ, "p_challenger": pq}).to_csv(out_dir / "shadow.csv", index=False)
    cmp = shadow_compare(p_champ, pq, t_champ, t_q)
    return {"version_id": shadow["version_id"], "label": shadow.get("label"), "model": q.model_best,
            "missing_columns": [c for c in feats if c not in df.columns], "seconds": time.time() - t0, **cmp}


def attach_labels(db_path: str, batch_id: str, batch_dir: str, labels_path: str, id_column: str, label_column: str,
                  label_date: str | None, info: dict[str, Any]) -> dict[str, Any]:
    """F18: join a later label file to a scored batch on `id_column` and measure performance on the joined rows (and the
    paired champion / challenger comparison when the batch was shadow-scored). `info`: problem_type, positive_class,
    threshold, cost, data_date, actor."""
    from . import monitoring as M

    reg = RegistryStore(Database(Path(db_path)))
    bdir = Path(batch_dir)
    scored = pd.read_csv(bdir / "scored.csv")
    lab = pd.read_csv(labels_path)
    for c, what in ((id_column, "id"), (label_column, "label")):
        if c not in lab.columns:
            raise ValueError(f"The label file has no {what} column '{c}' (it has {', '.join(map(str, lab.columns))}).")
    if id_column not in scored.columns:
        raise ValueError(f"The batch has no column '{id_column}' to join on.")
    dates = None
    if "label_date" in lab.columns:
        dates = pd.to_datetime(lab["label_date"], errors="coerce")
    lab = lab.dropna(subset=[label_column])
    dup = int(lab[id_column].duplicated().sum())
    lab = lab.drop_duplicates(subset=[id_column], keep="last")
    sc = scored.reset_index().rename(columns={"index": "_row"})
    sc[id_column] = sc[id_column].astype(str)
    lab = lab.assign(**{id_column: lab[id_column].astype(str)})
    j = sc.merge(lab[[id_column, label_column] + (["label_date"] if "label_date" in lab.columns else [])]
                 .rename(columns={label_column: "_y"}), on=id_column, how="inner")
    if not len(j):
        raise ValueError(f"No id in the label file matches the batch's {id_column}.")
    ldate = label_date
    if not ldate and dates is not None and dates.notna().any():
        ldate = str(dates.max().date())
    ldate = ldate or now()[:10]
    pt = info["problem_type"]
    out: dict[str, Any] = {"n_batch": int(len(scored)), "n_labels": int(len(lab)), "n_joined": int(len(j)),
                           "coverage": float(len(j) / len(scored)), "unmatched": int(len(lab) - len(j)), "duplicates": dup,
                           "id_column": id_column, "label_column": label_column, "label_date": ldate,
                           "data_date": info.get("data_date"), "lag_days": M.days_between(info.get("data_date"), ldate),
                           "uploaded_at": now(), "uploaded_by": info.get("actor") or ""}
    if pt == "binary":
        pos = str(info["positive_class"])
        y = (j["_y"].astype(str) == pos).astype(int).to_numpy()
        if not set(j["_y"].astype(str)) <= set(map(str, info.get("classes") or [])) | {pos}:
            out["unknown_labels"] = sorted(set(j["_y"].astype(str)) - set(map(str, info.get("classes") or [])))[:10]
        p = j[f"p_{pos}"].to_numpy(dtype=float)
        out["performance"] = M.perf_binary(y, p, float(info["threshold"]), info.get("cost"))
        sp = bdir / "shadow.csv"
        if sp.exists():
            sh = pd.read_csv(sp).set_index("row")
            rows = j["_row"].to_numpy()
            out["paired"] = M.paired_with_labels(y, sh.loc[rows, "p_champion"].to_numpy(), sh.loc[rows, "p_challenger"].to_numpy())
            out["paired"]["challenger_performance"] = M.perf_binary(y, sh.loc[rows, "p_challenger"].to_numpy(),
                                                                    float(info.get("challenger_threshold") or 0.5))
    else:
        proba = None
        if pt == "multiclass":
            pc = [c for c in j.columns if c.startswith("p_")]
            proba = j[pc].rename(columns=lambda c: c[2:]) if pc else None
        out["performance"] = M.perf_other(pt, j["_y"], j["prediction"], proba)
    pd.DataFrame({"row": j["_row"], id_column: j[id_column], "y": j["_y"]}).to_csv(bdir / "labels.csv", index=False)
    reg.merge_batch_result(batch_id, labels=out)
    return jsonable(out)


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
    return jsonable({**rs.info(), "n_adverse": int(len(idx)), "adverse_share": float(adverse.mean()),
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
        if kind == "expected_loss":  # G3: PD · LGD · EAD per loan, PSI of the inputs and of EL against the run's holdout
            from ..el import core as elc

            t0 = time.time()
            out, summ = elc.score_batch(version_path, df)
            score_s = time.time() - t0
            out.to_csv(out_path, index=False)
            meta = elc.load(version_path)["meta"]
            feats = [c for c in meta["inputs"] if c in df.columns and c in train.columns]
            kinds = {c: ("number" if meta["ranges"][c].get("numeric") else "category") for c in feats}
            ref_f = Path(cfg["run_dir"]) / "el_holdout.csv"
            ref_pred = pd.DataFrame({"y_pred": pd.read_csv(ref_f)["el"].to_numpy(dtype=float)}) if ref_f.exists() \
                else pd.DataFrame({"y_pred": out["expected_loss"].to_numpy(dtype=float)})
            cur_pred = pd.DataFrame({"y_pred": out["expected_loss"].to_numpy(dtype=float)})
            reg.update_batch(batch_id, stage="drift (PSI per feature)", progress=0.8)
            rep = drift.drift_report(train, df, kinds, problem_type="regression", ref_pred=ref_pred, cur_pred=cur_pred, positive=None)
            rep["prediction"]["output"] = "expected loss EL = PD · LGD · EAD per loan"
            rep["prediction_reference"] = "EL of the run's holdout loans"
            rep["reference"] = "the loans the three models were trained on"
            summary = {"n_rows": int(len(df)), "seconds": time.time() - t_start, "score_seconds": score_s,
                       "us_per_row": score_s / len(df) * 1e6, "model": "PD · LGD · EAD", "problem_type": "expected_loss",
                       "missing_columns": [c for c in meta["inputs"] if c not in df.columns],
                       "extra_columns": [c for c in df.columns if c not in meta["inputs"]],
                       "mean_prediction": float(out["expected_loss"].mean()), "std_prediction": float(out["expected_loss"].std()),
                       "prediction_symbol": "EL", "total_el": summ["total_el"], "ead_total": summ["ead_total"],
                       "mean_pd": summ["mean_pd"], "el_segments": summ["segments"]}
        elif kind == "anomaly":
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
        return _finish_batch(reg, batch_id, out_dir_p, version, rep, result, int(len(df)), f" ({kind})")
    except Exception as e:  # noqa: BLE001
        return _fail_batch(reg, batch_id, out_dir_p, e)


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


# --------------------------------------------------------------------------- counterfactuals / recourse (G1)

RECOURSE_BATCH_K = 20
_RECOURSE: dict[tuple, Any] = {}


def recourse_actions(cfg: dict[str, Any], features: list[str], saved: dict[str, Any] | None) -> tuple[dict[str, str], list[dict[str, Any]]]:
    """Feature → action: the user's saved choice, else G0's detection (roles + name rules) on the training table."""
    from ..intake import actionability

    df = pd.read_csv(cfg["csv_path"], nrows=20_000)
    det = actionability(df, features)  # the label stays in, so detection sees the real target
    mine = (saved or {}).get("actions") or {}
    acts, rows = {}, []
    for f in features:
        d = det.get(f, {"action": "any", "why": "the applicant can change it"})
        a = mine.get(f, d["action"])
        acts[f] = a
        rows.append({"feature": f, "action": a, "detected": d["action"], "why": "set by you" if f in mine and mine[f] != d["action"] else d["why"],
                     "source": "you" if f in mine and mine[f] != d["action"] else "detected"})
    return acts, rows


def recourse_engine(predictor_path: str, cfg: dict[str, Any], rconf: dict[str, Any], kinds: dict[str, str],
                    saved: dict[str, Any] | None):
    from ..engine import recourse as rcs
    from ..engine.pool import _signature

    rs = reasoner(predictor_path, cfg, rconf, kinds)
    acts, rows = recourse_actions(cfg, rs.features, saved)
    key = (str(predictor_path), _signature(predictor_path), json.dumps(rconf, sort_keys=True, default=str), json.dumps(acts, sort_keys=True))
    if key not in _RECOURSE:
        for k in [k for k in _RECOURSE if k[0] == str(predictor_path)]:
            _RECOURSE.pop(k, None)
        _RECOURSE[key] = rcs.Recourse(rs, _train_frame(cfg, rs.features), acts)
    return _RECOURSE[key], acts, rows


def recourse_info(predictor_path: str, cfg: dict[str, Any], rconf: dict[str, Any], kinds: dict[str, str],
                  saved: dict[str, Any] | None) -> dict[str, Any]:
    rc_, acts, rows = recourse_engine(predictor_path, cfg, rconf, kinds, saved)
    inf = rc_.info()
    for r in rows:
        r["effective"] = inf["allowed"][r["feature"]]
        if r["feature"] in inf["why_monotone"]:
            r["monotone"] = inf["why_monotone"][r["feature"]]
        r["kind"] = "number" if r["feature"] in inf["ranges"] else "category"
        r["range"] = inf["ranges"].get(r["feature"])
    return jsonable({**inf, "features": rows})


def recourse_rows(predictor_path: str, cfg: dict[str, Any], rconf: dict[str, Any], kinds: dict[str, str],
                  saved: dict[str, Any] | None, rows: list[dict[str, Any]]) -> dict[str, Any]:
    rc_, _, _ = recourse_engine(predictor_path, cfg, rconf, kinds, saved)
    X = pd.DataFrame([{c: r.get(c, np.nan) for c in rc_.features} for r in rows], columns=rc_.features)
    res = rc_.search(X)
    return jsonable({"results": res, "seconds": rc_.last_seconds, "threshold": rc_.rs.threshold,
                     "adverse_class": rc_.rs.adverse, "positive_class": rc_.rs.positive, "s": rc_.rs.s})


def recourse_check(predictor_path: str, cfg: dict[str, Any], rconf: dict[str, Any], kinds: dict[str, str],
                   saved: dict[str, Any] | None, n_adverse: int = 120, out_path: str | None = None) -> dict[str, Any]:
    """The acceptance check: the first `n_adverse` adverse holdout rows (in holdout order), their counterfactuals
    re-scored by the served model at t. Stored beside the version; the model card shows it."""
    from ..engine import recourse as rcs
    from ..engine import serving
    from ..splits import load_splits

    rc_, acts, _ = recourse_engine(predictor_path, cfg, rconf, kinds, saved)
    h = serving.holdout_frame(cfg["csv_path"], load_splits(Path(cfg["splits_path"])), cfg["label"], cfg["drop_columns"])
    X = h.reindex(columns=rc_.features)
    adverse, _ = rc_.rs.decide(X)
    idx = np.flatnonzero(adverse)[:n_adverse]
    out = rcs.check(rc_, X.iloc[idx].reset_index(drop=True), acts)
    out.update({"holdout_rows": int(len(X)), "holdout_adverse": int(adverse.sum()), "threshold": rc_.rs.threshold,
                "adverse_class": rc_.rs.adverse, "computed_at": now()})
    out = jsonable(out)
    if out_path:
        Path(out_path).write_text(json.dumps(out))
    return out


def _batch_recourse(predictor_path: str, cfg: dict[str, Any], rconf: dict[str, Any], kinds: dict[str, str],
                    saved: dict[str, Any], X: pd.DataFrame, out: pd.DataFrame) -> dict[str, Any]:
    """G1: counterfactuals for the first RECOURSE_BATCH_K adverse rows, as a `recourse` column (the nearest valid
    option, in words) and a summary."""
    rc_, _, _ = recourse_engine(predictor_path, cfg, rconf, kinds, saved)
    adv = np.asarray(out["adverse"], dtype=bool)
    idx = np.flatnonzero(adv)[:RECOURSE_BATCH_K]
    res = rc_.search(X.reset_index(drop=True).iloc[idx].reset_index(drop=True)) if len(idx) else []
    col: list[Any] = [None] * len(out)
    rows = []
    for i, r in zip(idx, res):
        valid = [o for o in r["options"] if o["valid"]]
        col[i] = valid[0]["text"] if valid else "no valid change found"
        rows.append({"row": int(i), "p": r["p"], "options": r["options"]})
    out["recourse"] = col
    n_ok = sum(any(o["valid"] for o in r["options"]) for r in res)
    return jsonable({"k": RECOURSE_BATCH_K, "rows_searched": int(len(idx)), "with_valid": int(n_ok),
                     "seconds": rc_.last_seconds if len(idx) else 0.0, "rows": rows})
