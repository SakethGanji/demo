"""F18 · Monitoring over time: the pure computations behind the monitoring page of a registered version.

Every scored batch carries the date of its data (`data_date`, the last day the batch covers). Ordered by that date,
the batches give time series of
- PSI per feature and of the score (registry/drift.py, unchanged), and F15's adversarial AUC when a check was run on
  the batch: covariate shift p(x);
- the score distribution: mean p̂, quantiles q₁₀ … q₉₀ and the share decided adverse at the served threshold;
- performance, **once labels arrive** (an upload keyed by an id column, later than the batch): AUC with DeLong's
  interval, log loss, Brier, observed rate with a Wilson interval against the mean p̂ (calibration in the large,
  z = (Σy − Σp̂) / √Σp̂(1 − p̂)). Each point carries its label lag: label date − data date, in days. A batch whose labels
  have not arrived has no performance point, and the page says so rather than carrying the last value forward.

Champion / challenger: a Staging version shadow-scores each batch of the Production version (same rows, its own
features and threshold). Before labels: the two score distributions (PSI of the challenger's p̂ on the champion's
deciles, KS), Spearman's ρ between them and the share of identical decisions (Cohen's κ). After labels: the paired
ΔAUC = AUC_challenger − AUC_champion on the same rows with DeLong's paired variance
Var(Δ) = Var(A₁) + Var(A₂) − 2 Cov(A₁, A₂), and the paired bootstrap of Δ log loss (B = 2,000 resamples of rows).
The stage change stays a human decision (four-eyes review, then the transition): nothing here promotes anything.
"""

from __future__ import annotations

from datetime import date
from typing import Any

import numpy as np
import pandas as pd

from ..stats import delong, delong_parts, wilson

DEFAULT_THRESHOLDS: dict[str, Any] = {
    "feature_psi": 0.25,          # alert when any feature's PSI exceeds this
    "feature_psi_watch": 0.10,    # watch band (drift.py's bands)
    "prediction_psi": 0.25,       # alert on the score's PSI
    "adversarial_auc": 0.60,      # alert when F15's batch AUC lower CI bound exceeds this
    "auc_drop": 0.03,             # alert when AUC_batch's upper CI bound < AUC_baseline − drop
    "calibration_z": 3.0,         # alert when |z| of observed vs predicted rate exceeds this
    "min_labelled": 200,          # performance needs at least this many labelled rows
}
THRESHOLD_RULES = {
    "feature_psi": "max_j PSI_j > θ_PSI (any feature)",
    "prediction_psi": "PSI(p̂) > θ_score",
    "adversarial_auc": "lower 95 % bound of AUC_adv > θ_adv",
    "auc_drop": "upper 95 % bound of AUC_batch < AUC_baseline − δ",
    "calibration_z": "|z| = |Σy − Σp̂| / √Σp̂(1 − p̂) > θ_z",
}
Z = 1.959963984540054


def _f(v: Any) -> float | None:
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return f if np.isfinite(f) else None


def days_between(a: str | None, b: str | None) -> int | None:
    try:
        return (date.fromisoformat(str(b)[:10]) - date.fromisoformat(str(a)[:10])).days
    except (TypeError, ValueError):
        return None


# --------------------------------------------------------------------------- performance on labelled rows


def paired_delong(y: np.ndarray, s1: np.ndarray, s2: np.ndarray) -> dict[str, Any]:
    """ΔAUC = AUC(s2) − AUC(s1) on the same rows; Var(Δ) from DeLong's structural components of both scores."""
    from scipy.stats import norm

    y = np.asarray(y).astype(int)
    a1, v10a, v01a = delong_parts(y, np.asarray(s1, dtype=float))
    a2, v10b, v01b = delong_parts(y, np.asarray(s2, dtype=float))
    m, n = len(v10a), len(v01a)
    s10 = np.cov(np.vstack([v10a, v10b]), ddof=1)
    s01 = np.cov(np.vstack([v01a, v01b]), ddof=1)
    cov = s10 / m + s01 / n
    var = float(cov[0, 0] + cov[1, 1] - 2 * cov[0, 1])
    se = float(np.sqrt(max(var, 0.0)))
    d = a2 - a1
    z = d / se if se > 0 else None
    p = float(2 * (1 - norm.cdf(abs(z)))) if z is not None else None
    return {"auc_champion": a1, "auc_challenger": a2, "delta": d, "se": se, "ci": [d - Z * se, d + Z * se], "z": z,
            "p_value": p, "corr": float(cov[0, 1] / np.sqrt(cov[0, 0] * cov[1, 1])) if cov[0, 0] > 0 and cov[1, 1] > 0 else None,
            "m": m, "n": n, "method": "DeLong paired: Var(Δ) = Var(A₁) + Var(A₂) − 2 Cov(A₁, A₂)"}


def _logloss_rows(y: np.ndarray, p: np.ndarray) -> np.ndarray:
    p = np.clip(np.asarray(p, dtype=float), 1e-15, 1 - 1e-15)
    return -(y * np.log(p) + (1 - y) * np.log(1 - p))


def paired_bootstrap(loss1: np.ndarray, loss2: np.ndarray, B: int = 2000, seed: int = 18) -> dict[str, Any]:
    """Δ = mean(loss2) − mean(loss1) over the same rows, with a percentile interval over B row resamples."""
    d = np.asarray(loss2, dtype=float) - np.asarray(loss1, dtype=float)
    rng = np.random.default_rng(seed)
    idx = rng.integers(0, len(d), size=(B, len(d)))
    boots = d[idx].mean(axis=1)
    return {"delta": float(d.mean()), "ci": [float(np.quantile(boots, 0.025)), float(np.quantile(boots, 0.975))],
            "se": float(boots.std(ddof=1)), "B": B, "n": int(len(d)), "method": "paired bootstrap over rows"}


def perf_binary(y: np.ndarray, p: np.ndarray, threshold: float, cost: dict[str, Any] | None = None) -> dict[str, Any]:
    """Performance of p̂ = P(y = 1 | x) on labelled rows. y is 0/1 with 1 = the positive class."""

    y = np.asarray(y).astype(int)
    p = np.asarray(p, dtype=float)
    n, k = len(y), int(y.sum())
    out: dict[str, Any] = {"n": n, "positives": k, "observed_rate": k / n if n else None, "observed_ci": wilson(k, n, Z, empty=[0.0, 1.0]),
                           "mean_p": float(p.mean()) if n else None}
    var = float((p * (1 - p)).sum())
    out["calibration_z"] = float((k - p.sum()) / np.sqrt(var)) if var > 0 else None
    out["log_loss"] = float(_logloss_rows(y, p).mean()) if n else None
    out["brier"] = float(((p - y) ** 2).mean()) if n else None
    if 0 < k < n:
        d = delong(y, p)
        out["auc"] = d["value"]
        out["auc_ci"] = d["ci"]
        out["auc_se"] = d["se"]
    else:
        out["auc"] = out["auc_ci"] = out["auc_se"] = None
    dec = p >= threshold
    tp = int((dec & (y == 1)).sum())
    fp = int((dec & (y == 0)).sum())
    fn = int((~dec & (y == 1)).sum())
    out["threshold"] = float(threshold)
    out["flagged_share"] = float(dec.mean()) if n else None
    out["recall"] = tp / (tp + fn) if tp + fn else None
    out["precision"] = tp / (tp + fp) if tp + fp else None
    out["confusion"] = {"tp": tp, "fp": fp, "fn": fn, "tn": int(n - tp - fp - fn)}
    if cost:
        c = float(cost.get("c_fp") or 0) * fp + float(cost.get("c_fn") or 0) * fn - float(cost.get("g_tp") or 0) * tp
        out["cost_per_1000"] = 1000 * c / n if n else None
    return out


def perf_other(problem_type: str, y: pd.Series, pred: pd.Series, proba: pd.DataFrame | None) -> dict[str, Any]:
    """Multiclass: accuracy (Wilson) and log loss; regression / quantile: RMSE, MAE, R² of the point prediction."""
    n = len(y)
    if problem_type == "multiclass":
        ok = (pred.astype(str).to_numpy() == y.astype(str).to_numpy())
        out = {"n": n, "accuracy": float(ok.mean()), "accuracy_ci": wilson(int(ok.sum()), n, Z, empty=[0.0, 1.0])}
        if proba is not None:
            cols = [str(c) for c in proba.columns]
            pr = proba.to_numpy()
            idx = np.array([cols.index(str(v)) if str(v) in cols else -1 for v in y])
            pt = np.where(idx >= 0, pr[np.arange(n), np.maximum(idx, 0)], 1e-15)
            out["log_loss"] = float(-np.log(np.clip(pt, 1e-15, 1)).mean())
        return out
    yt = pd.to_numeric(y, errors="coerce").to_numpy(dtype=float)
    yp = pd.to_numeric(pred, errors="coerce").to_numpy(dtype=float)
    e = yp - yt
    ss = float(((yt - yt.mean()) ** 2).sum())
    return {"n": n, "rmse": float(np.sqrt((e ** 2).mean())), "mae": float(np.abs(e).mean()),
            "r2": float(1 - (e ** 2).sum() / ss) if ss > 0 else None, "mean_y": float(yt.mean()), "mean_pred": float(yp.mean())}


def score_quantiles(p: np.ndarray) -> dict[str, float]:
    qs = np.quantile(np.asarray(p, dtype=float), [0.1, 0.25, 0.5, 0.75, 0.9])
    return {f"q{int(q * 100)}": float(v) for q, v in zip([0.1, 0.25, 0.5, 0.75, 0.9], qs)}


# --------------------------------------------------------------------------- champion / challenger (before labels)


def shadow_compare(p_champ: np.ndarray, p_chall: np.ndarray, t_champ: float, t_chall: float) -> dict[str, Any]:
    """Score distributions and agreement of the two versions on the same rows (no labels needed)."""
    from scipy.stats import ks_2samp, spearmanr

    from . import drift

    a = np.asarray(p_champ, dtype=float)
    b = np.asarray(p_chall, dtype=float)
    psi = drift.feature_psi("p̂", "number", pd.Series(a), pd.Series(b))
    ks = ks_2samp(a, b)
    da, db = a >= t_champ, b >= t_chall
    agree = float((da == db).mean())
    pa, pb = float(da.mean()), float(db.mean())
    pe = pa * pb + (1 - pa) * (1 - pb)
    kappa = (agree - pe) / (1 - pe) if pe < 1 else None
    rho = spearmanr(a, b)
    return {"n": int(len(a)), "psi": psi["psi"], "band": psi["band"], "ks": float(ks.statistic), "ks_p": float(ks.pvalue),
            "spearman": float(rho.statistic), "agreement": agree, "kappa": _f(kappa),
            "mean_p_champion": float(a.mean()), "mean_p_challenger": float(b.mean()),
            "flagged_champion": pa, "flagged_challenger": pb, "threshold_champion": float(t_champ),
            "threshold_challenger": float(t_chall),
            "disagree": {"champion_only": int((da & ~db).sum()), "challenger_only": int((~da & db).sum())},
            "quantiles_champion": score_quantiles(a), "quantiles_challenger": score_quantiles(b)}


def paired_with_labels(y: np.ndarray, p_champ: np.ndarray, p_chall: np.ndarray) -> dict[str, Any]:
    y = np.asarray(y).astype(int)
    out: dict[str, Any] = {"n": int(len(y))}
    if 0 < y.sum() < len(y):
        out["auc"] = paired_delong(y, p_champ, p_chall)
    out["log_loss"] = paired_bootstrap(_logloss_rows(y, p_champ), _logloss_rows(y, p_chall))
    return out


# --------------------------------------------------------------------------- the time series and its alerts


def alerts_for(point: dict[str, Any], baseline: dict[str, Any] | None, th: dict[str, Any]) -> list[dict[str, Any]]:
    """The alerts one batch raises. Each says which signal fired, the number and the rule."""
    out: list[dict[str, Any]] = []
    drift_feats = [f for f in point.get("features") or [] if (f.get("psi") or 0) > th["feature_psi"]]
    if drift_feats:
        top = max(drift_feats, key=lambda f: f["psi"])
        out.append({"signal": "feature_psi", "kind": "covariate", "severity": "major",
                    "features": [f["feature"] for f in sorted(drift_feats, key=lambda f: -f["psi"])],
                    "value": top["psi"], "threshold": th["feature_psi"], "rule": THRESHOLD_RULES["feature_psi"],
                    "text": f"PSI {top['psi']:.3f} on {top['feature']} > {th['feature_psi']:g}"
                            + (f" (and {len(drift_feats) - 1} more)" if len(drift_feats) > 1 else "")})
    pp = point.get("prediction_psi")
    if pp is not None and pp > th["prediction_psi"]:
        out.append({"signal": "prediction_psi", "kind": "score", "severity": "major", "value": pp,
                    "threshold": th["prediction_psi"], "rule": THRESHOLD_RULES["prediction_psi"],
                    "text": f"Score PSI {pp:.3f} > {th['prediction_psi']:g}"})
    adv = point.get("adversarial") or {}
    if adv.get("auc") is not None and (adv.get("ci") or [0])[0] > th["adversarial_auc"]:
        out.append({"signal": "adversarial_auc", "kind": "covariate", "severity": "major", "value": adv["auc"],
                    "ci": adv.get("ci"), "threshold": th["adversarial_auc"], "rule": THRESHOLD_RULES["adversarial_auc"],
                    "features": [f.get("feature") for f in adv.get("top_features") or [] if f.get("significant")][:3],
                    "text": f"Adversarial AUC {adv['auc']:.3f} [{adv['ci'][0]:.3f}, {adv['ci'][1]:.3f}] > {th['adversarial_auc']:g}"})
    perf = point.get("performance")
    if perf and perf.get("n", 0) >= th["min_labelled"]:
        b_auc = (baseline or {}).get("auc")
        if perf.get("auc") is not None and b_auc is not None and perf["auc_ci"][1] < b_auc - th["auc_drop"]:
            out.append({"signal": "auc_drop", "kind": "performance", "severity": "critical", "value": perf["auc"],
                        "ci": perf["auc_ci"], "baseline": b_auc, "threshold": th["auc_drop"], "rule": THRESHOLD_RULES["auc_drop"],
                        "lag_days": point.get("label_lag_days"),
                        "text": f"AUC {perf['auc']:.3f} [{perf['auc_ci'][0]:.3f}, {perf['auc_ci'][1]:.3f}] < baseline "
                                f"{b_auc:.3f} − {th['auc_drop']:g} (labels {point.get('label_lag_days')} days after the data)"})
        z = perf.get("calibration_z")
        if z is not None and abs(z) > th["calibration_z"]:
            out.append({"signal": "calibration_z", "kind": "performance", "severity": "critical", "value": z,
                        "threshold": th["calibration_z"], "rule": THRESHOLD_RULES["calibration_z"],
                        "lag_days": point.get("label_lag_days"),
                        "text": f"Observed rate {perf['observed_rate']:.3f} vs predicted {perf['mean_p']:.3f}: z = {z:+.1f}"})
    return out


def first_alert(points: list[dict[str, Any]], kinds: tuple[str, ...]) -> dict[str, Any] | None:
    """The first point (in data order) with an alert of one of these kinds, and when it became visible."""
    for p in points:
        a = [x for x in p.get("alerts") or [] if x["kind"] in kinds]
        if a:
            # performance is visible on the label date; drift on the day the batch is scored, which for a batch of a
            # period is its data date (the stream feeds each month at its end; `scored_at` is the wall-clock record)
            seen = p.get("labels_date") if kinds == ("performance",) else p.get("data_date") or p.get("scored_at")
            return {"batch_id": p["batch_id"], "data_date": p.get("data_date"), "period": p.get("period"), "scored_at": p.get("scored_at"),
                    "visible_on": seen, "signals": sorted({x["signal"] for x in a}), "text": a[0]["text"]}
    return None


def summarize_alerts(points: list[dict[str, Any]]) -> dict[str, Any]:
    """Which signal caught what: the first covariate, score and performance alert, and the current state. Active alerts
    are the latest batch's drift alerts and the latest *labelled* batch's performance alerts (the newest batches have no
    labels yet; their performance is unknown, not fine)."""
    latest = points[-1] if points else None
    labelled = [p for p in points if p.get("performance")]
    ll = labelled[-1] if labelled else None
    active = [dict(a, batch_id=latest["batch_id"], period=latest.get("period"))
              for a in (latest or {}).get("alerts") or [] if a["kind"] != "performance"]
    active += [dict(a, batch_id=ll["batch_id"], period=ll.get("period"), visible_on=ll.get("labels_date"))
               for a in (ll or {}).get("alerts") or [] if a["kind"] == "performance"]
    return {"first_covariate": first_alert(points, ("covariate",)), "first_score": first_alert(points, ("score",)),
            "first_performance": first_alert(points, ("performance",)), "active": active,
            "latest_labelled": {k: ll.get(k) for k in ("batch_id", "period", "data_date", "labels_date", "label_lag_days")} if ll else None,
            "unlabelled_since": next((p.get("period") for p in points if not p.get("performance") and
                                      (not ll or (p.get("data_date") or "") > (ll.get("data_date") or ""))), None),
            "n_points": len(points), "n_labelled": len(labelled)}
