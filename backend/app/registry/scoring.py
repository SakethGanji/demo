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
           explain_row: bool = True, nsamples: int = explain.SHAP_NSAMPLES) -> dict[str, Any]:
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
    else:
        out["prediction"] = float(p.predict(x).iloc[0])
        out["probabilities"] = None
    out["predict_ms"] = (time.perf_counter() - t0) * 1e3
    if explain_row:
        target_c, classes = explain.resolve_target(p, x, target)
        sh = explain.shap_explain(p, train, bg, x, target_c, classes, nsamples)
        sh["reasons"] = [{**c, "direction": "raises" if c["phi"] > 0 else "lowers"} for c in sh["contributions"][:5]]
        out["shap"] = sh
    return jsonable(out)


def _train_frame(cfg: dict[str, Any], features: list[str]) -> pd.DataFrame:
    df = pd.read_csv(cfg["csv_path"])
    splits = json.loads(Path(cfg["splits_path"]).read_text())
    is_h = np.zeros(len(df), dtype=bool)
    is_h[np.asarray(splits["holdout"], dtype=int)] = True
    return df.loc[~is_h & df[cfg["label"]].notna(), [c for c in features if c in df.columns]]


def score_batch(db_path: str, batch_id: str, predictor_path: str, cfg: dict[str, Any], csv_path: str, out_dir: str,
                kinds: dict[str, str], version: dict[str, Any]) -> dict[str, Any]:
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
        preds, probas = [], []
        clf = p.problem_type in ("binary", "multiclass")
        t0 = time.time()
        for i in range(0, n, chunk):
            part = X.iloc[i:i + chunk]
            if clf:
                pr = p.predict_proba(part)
                probas.append(pr)
                preds.append(p.predict_from_proba(pr))
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
        # A histogram of the output for the UI (batch vs reference)
        if p.problem_type == "binary":
            edges = np.linspace(0, 1, 21)
            a = np.histogram(cur_pred[f"p_{pos}"], bins=edges)[0]
            e = np.histogram(ref_pred[f"p_{pos}"], bins=edges)[0]
            summary["histogram"] = {"edges": edges.tolist(), "actual": a.tolist(), "expected": e.tolist()}
        elif p.problem_type == "regression":
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
