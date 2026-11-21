"""D4 · Changes after deployment: typed experiments on a registered version, evaluated on its NEWEST LABELLED BATCH
against the version as it serves today. Nothing changes the live model: a result that is better beyond noise becomes a
request, i.e. a new registered version (approval pending) that goes through the usual four-eyes review and promotion.

Kinds (binary versions):
  threshold      the cost-optimal threshold on recent data. Out of sample: chosen on one half of the batch (stratified),
                 applied to the other, and back (cross-fitted). Δ = cost per 1,000 (current t − new t).
  recalibrate    Platt scaling on logit p̂, cross-fitted the same way. Δ log loss (the verdict) and the cost at the
                 calibrated Bayes threshold. The requested version serves the calibrated probabilities (the Platt map is
                 stored with it, ensemble_models.calibration) and decides at the Bayes threshold on that scale.
  retrain_window the version's config retrained on the labelled batches BEFORE the newest (the window; plus the
                 version's training data when the window has < 300 rows), scored on the newest batch.
  drop_feature   the version's config retrained without one (drifted) feature on its own training data, scored on the
                 newest batch.

Metric: expected cost per 1,000 decisions under the run's costs, or, when the run has none, under the ratio its
threshold implies (t = c_FP / (c_FP + c_FN) ⇒ c_FN / c_FP = (1 − t) / t). Retrained models are compared at their own
cross-fitted cost-optimal thresholds, so only the model differs; their AUC is compared with DeLong's paired test.
Paired bootstrap (B = 1,000) over the batch's labelled rows. Better beyond noise ⟺ the CI is above 0.
"""

from __future__ import annotations

import json
import secrets
from pathlib import Path
from typing import Any

import numpy as np

KINDS = {
    "threshold": "Move the threshold to the cost-optimal one on the newest labelled batch (cross-fitted)",
    "recalibrate": "Recalibrate the probabilities (Platt scaling, cross-fitted) and decide at the calibrated Bayes threshold",
    "retrain_window": "Retrain the version's config on the recent labelled window (the batches before the newest)",
    "drop_feature": "Retrain without a drifted feature",
}
MIN_ROWS = 100
B = 1000


def _logit(p: np.ndarray) -> np.ndarray:
    p = np.clip(p, 1e-6, 1 - 1e-6)
    return np.log(p / (1 - p))


def _sig(z: np.ndarray) -> np.ndarray:
    return 1 / (1 + np.exp(-z))


def _halves(y: np.ndarray, seed: int = 0) -> np.ndarray:
    rng = np.random.default_rng(seed)
    h = np.zeros(len(y), bool)
    for v in (0, 1):
        idx = np.flatnonzero(y == v)
        h[rng.permutation(idx)[: len(idx) // 2]] = True
    return h


def _boot(a: np.ndarray, b: np.ndarray, seed: int = 0) -> list[float]:
    """CI of mean(a − b) over resampled rows."""
    rng = np.random.default_rng(seed)
    n = len(a)
    ds = [float((a[i] - b[i]).mean()) for i in (rng.integers(0, n, n) for _ in range(B))]
    return [float(np.quantile(ds, 0.025)), float(np.quantile(ds, 0.975))]


def _row_cost(y: np.ndarray, d: np.ndarray, cost: dict[str, float]) -> np.ndarray:
    return 1000 * (cost["c_fp"] * (d & (y == 0)) + cost["c_fn"] * (~d & (y == 1)) - cost.get("g_tp", 0) * (d & (y == 1)))


def _op(y: np.ndarray, d: np.ndarray, cost: dict[str, float], t: float | None) -> dict[str, Any]:
    return {"t": t, "decline_rate": float(d.mean()), "catch_rate": float(d[y == 1].mean()) if y.sum() else None,
            "cost_per_1000": float(_row_cost(y, d, cost).mean())}


def cost_of(run: dict[str, Any], t: float) -> dict[str, Any]:
    c = (run.get("config") or {}).get("cost") or {}
    if c.get("c_fn") and c.get("c_fp"):
        return {"c_fp": float(c["c_fp"]), "c_fn": float(c["c_fn"]), "g_tp": float(c.get("g_tp") or 0), "source": "the run's business cost"}
    t = min(max(float(t), 1e-3), 1 - 1e-3)
    return {"c_fp": 1.0, "c_fn": float((1 - t) / t), "g_tp": 0.0,
            "source": f"implied by the version's threshold t = {t:.3f}: c_FN / c_FP = (1 − t) / t = {(1 - t) / t:.3g} (the run has no business cost)"}


def threshold_move(y: np.ndarray, p: np.ndarray, t0: float, cost: dict[str, float]) -> dict[str, Any]:
    from ..vela.experiments import crossfit_decisions

    d_new, t_full, halves = crossfit_decisions(y, p, cost["c_fp"], cost["c_fn"], cost.get("g_tp", 0))
    d_cur = p > t0
    a, b = _row_cost(y, d_cur, cost), _row_cost(y, d_new, cost)
    return {"metric": "cost / 1,000", "current": float(a.mean()), "proposed": float(b.mean()), "delta": float(a.mean() - b.mean()),
            "ci": _boot(a, b), "rows": int(len(y)), "threshold": {"current": t0, "proposed": t_full, "halves": halves},
            "operating": {"current": _op(y, d_cur, cost, t0), "proposed": _op(y, d_new, cost, t_full)},
            "proposal": {"decision_threshold": t_full},
            "sign": "Δ = cost now − cost with the move (positive: the move saves)"}


def _platt(z: np.ndarray, y: np.ndarray) -> tuple[float, float]:
    from sklearn.linear_model import LogisticRegression

    m = LogisticRegression(C=1e6, max_iter=1000).fit(z.reshape(-1, 1), y)
    return float(m.coef_[0, 0]), float(m.intercept_[0])


def recalibrate(y: np.ndarray, p: np.ndarray, t0: float, cost: dict[str, float]) -> dict[str, Any]:
    z = _logit(p)
    h = _halves(y)
    q = np.zeros(len(y))
    for fit, app in ((h, ~h), (~h, h)):
        a_, b_ = _platt(z[fit], y[fit])
        q[app] = _sig(a_ * z[app] + b_)
    a_full, b_full = _platt(z, y)
    ll = lambda pp: -(y * np.log(np.clip(pp, 1e-12, 1)) + (1 - y) * np.log(np.clip(1 - pp, 1e-12, 1)))  # noqa: E731
    la, lb = ll(np.clip(p, 1e-6, 1 - 1e-6)), ll(q)
    tb = cost["c_fp"] / (cost["c_fp"] + cost["c_fn"])  # Bayes threshold on calibrated p
    d_cur, d_new = p > t0, q > tb
    ca, cb = _row_cost(y, d_cur, cost), _row_cost(y, d_new, cost)
    t_raw = float(_sig((float(_logit(np.array([tb]))[0]) - b_full) / a_full)) if a_full > 0 else None
    return {"metric": "log loss", "current": float(la.mean()), "proposed": float(lb.mean()), "delta": float(la.mean() - lb.mean()),
            "ci": _boot(la, lb), "rows": int(len(y)),
            "calibration": {"observed_rate": float(y.mean()), "mean_p_current": float(p.mean()), "mean_p_calibrated": float(q.mean()),
                            "platt": {"a": a_full, "b": b_full}, "bayes_threshold": tb},
            "cost": {"current": float(ca.mean()), "proposed": float(cb.mean()), "delta": float(ca.mean() - cb.mean()), "ci": _boot(ca, cb, 1)},
            "operating": {"current": _op(y, d_cur, cost, t0), "proposed": _op(y, d_new, cost, t_raw)},
            "proposal": {"decision_threshold": tb, "platt": {"a": a_full, "b": b_full}, "raw_threshold_equivalent": t_raw}
            if a_full > 0 and 0 < tb < 1 else None,
            "sign": "Δ = log loss now − log loss calibrated (positive: better calibrated)",
            "serving": "The requested version serves the calibrated probabilities p′ = σ(a · logit p + b) (a, b fitted on the whole "
                       "batch) and decides at the calibrated Bayes threshold t* = c_FP / (c_FP + c_FN); the same decisions as "
                       "p > σ((logit t* − b) / a) on the raw score. The log loss above is the cross-fitted estimate."}


def compare_models(y: np.ndarray, p_cur: np.ndarray, p_new: np.ndarray, cost: dict[str, float]) -> dict[str, Any]:
    """Two models on the same labelled rows: cost at each one's cross-fitted cost-optimal threshold, and paired AUC."""
    from ..vela.experiments import crossfit_decisions
    from .monitoring import paired_delong

    da, ta, _ = crossfit_decisions(y, p_cur, cost["c_fp"], cost["c_fn"], cost.get("g_tp", 0))
    db, tb, _ = crossfit_decisions(y, p_new, cost["c_fp"], cost["c_fn"], cost.get("g_tp", 0))
    ca, cb = _row_cost(y, da, cost), _row_cost(y, db, cost)
    out = {"metric": "cost / 1,000", "current": float(ca.mean()), "proposed": float(cb.mean()), "delta": float(ca.mean() - cb.mean()),
           "ci": _boot(ca, cb), "rows": int(len(y)), "threshold": {"current_crossfit": ta, "proposed_crossfit": tb},
           "operating": {"current": _op(y, da, cost, ta), "proposed": _op(y, db, cost, tb)},
           "sign": "Δ = cost now − cost with the new model, each at its cross-fitted cost-optimal threshold (positive: saves)"}
    if 0 < y.sum() < len(y):
        d = paired_delong(y, p_cur, p_new)
        out["auc"] = {"current": d["auc_champion"], "proposed": d["auc_challenger"], "delta": d["delta"], "ci": d["ci"], "p_value": d.get("p_value")}
    return out


def score_with_run(predictor_path: str, input_csv: str, rows: list[int], positive: str,
                   cfg: dict[str, Any] | None = None) -> list[float]:
    """(Heavy pool.) p̂ of the positive class from a run's predictor on the given rows of a batch's input."""
    from autogluon.tabular import TabularPredictor

    from .scoring import read_batch

    X = read_batch(input_csv, cfg).iloc[rows]  # the user's upload, typed as the run's table
    p = TabularPredictor.load(predictor_path, verbosity=0, require_py_version_match=False)
    pr = p.predict_proba(X)
    col = next((c for c in pr.columns if str(c) == str(positive)), pr.columns[-1])
    return [float(v) for v in pr[col].to_numpy()]


def new_id() -> str:
    return "chg_" + secrets.token_hex(4)


def store_path(reg_dir: Path, vid: str) -> Path:
    d = reg_dir / "changes"
    d.mkdir(parents=True, exist_ok=True)
    return d / f"{vid}.json"


def load_all(reg_dir: Path, vid: str) -> list[dict[str, Any]]:
    p = store_path(reg_dir, vid)
    return json.loads(p.read_text()) if p.exists() else []


def save_all(reg_dir: Path, vid: str, items: list[dict[str, Any]]) -> None:
    p = store_path(reg_dir, vid)
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(items, default=lambda v: v.item() if hasattr(v, "item") else str(v)))
    tmp.replace(p)


def compose_platt(cur: dict[str, Any] | None, new: dict[str, Any]) -> dict[str, float]:
    """The map to serve on the run's raw score when the version already serves σ(a₁ · logit p + b₁) and the new
    calibration (a₂, b₂) was fitted on those served probabilities: a = a₂a₁, b = a₂b₁ + b₂."""
    a2, b2 = float(new["a"]), float(new["b"])
    if not cur:
        return {"a": a2, "b": b2}
    a1, b1 = float(cur["a"]), float(cur["b"])
    return {"a": a2 * a1, "b": a2 * b1 + b2}


def verdict(res: dict[str, Any] | None) -> str:
    if not res or res.get("delta") is None:
        return "no result"
    lo, hi = res["ci"]
    return "better" if lo > 0 else "worse" if hi < 0 else "within noise"
