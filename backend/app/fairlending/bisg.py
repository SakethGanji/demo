"""BISG (Bayesian Improved Surname Geocoding) and probability-weighted fair-lending metrics.

    P(r | s, g) = P(r | s) · P(g | r) / Σ_r' P(r' | s) · P(g | r')

where s is the surname, g the zip code, r the group, P(r | s) the surname table's shares and
P(g | r) = N_g,r / N_r the share of group r's population that lives in zip g. A surname not in the table gets the
table's overall P(r); a zip not in the table leaves P(r | s) as it is.

The fairness numbers weight each applicant i by p_ir = P(r | s_i, g_i) instead of assigning one group:

    approval rate  A_r   = Σ_i p_ir · a_i / Σ_i p_ir           (a_i = 1 when approved)
    adverse impact AIR_r = A_r / A_ref                         (ref = the control group, White)
    difference     D_r   = A_r − A_ref

with 95% bootstrap intervals (applicants resampled, B = 400).

The plain weighted rate is attenuated: a group's weighted applicants include people from other groups, so its rate is
pulled toward theirs and gaps shrink. When the decision depends on the group only through the applicant's own data
(a ⟂ s, g | r) and the probabilities are calibrated, E[Σ_i p_ir a_i] = Σ_r' E[Σ_i p_ir p_ir'] · A_r', so the
corrected rates solve the K × K system

    Q · A = b,   Q_rr' = Σ_i p_ir p_ir',   b_r = Σ_i p_ir a_i

(a method-of-moments correction). ENSEMBLE reports the corrected rates as the headline and the plain ones beside them.
"""

from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd

from .tables import REFERENCE, Tables, norm_surname, norm_zip

B = 400
MIN_EFF_N = 50  # groups with fewer probability-weighted applicants are shown but not judged
MIN_SEPARABILITY = 0.5  # nor groups the proxy cannot tell apart (see separability)


def posterior(surnames: pd.Series, zips: pd.Series | None, t: Tables) -> dict[str, Any]:
    """P (n × K) group probabilities, plus how many surnames and zips the tables knew."""
    s = norm_surname(surnames.fillna(""))
    ps = t.surname.reindex(s.to_numpy())[t.groups].to_numpy()
    s_hit = ~np.isnan(ps).any(axis=1)
    ps[~s_hit] = t.prior
    post = ps.copy()
    z_hit = np.zeros(len(s), dtype=bool)
    if zips is not None:
        pg = t.p_zip_given_group.reindex(norm_zip(zips).to_numpy()).to_numpy()
        z_hit = ~np.isnan(pg).any(axis=1)
        num = ps * np.where(z_hit[:, None], np.nan_to_num(pg), 1.0)
        tot = num.sum(axis=1, keepdims=True)
        ok = tot[:, 0] > 0
        post[ok] = num[ok] / tot[ok]
    return {"P": post, "surname_matched": s_hit, "zip_matched": z_hit, "P_surname_only": ps}


def _estimates(W: np.ndarray, P: np.ndarray, a: np.ndarray) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """For each row of applicant weights W (B × n): the plain weighted approval rates (B × K), the corrected ones
    (B × K) and Σ p_ir (B × K). Corrected: Q·A = b with Q_rr' = Σ_i w_i p_ir p_ir', b_r = Σ_i w_i p_ir a_i."""
    den = W @ P
    b = W @ (P * a[:, None])
    plain = b / np.where(den > 0, den, np.nan)
    Q = np.einsum("bn,nk,nl->bkl", W, P, P)
    K = P.shape[1]
    Q = Q + 1e-9 * np.eye(K)[None]
    corr = np.clip(np.linalg.solve(Q, b[..., None])[..., 0], 0, 1)
    return plain, corr, den


def separability(P: np.ndarray) -> np.ndarray:
    """S_r = Σ_i p_ir² / Σ_i p_ir: among the applicants the proxy counts for group r, the share that the proxy
    itself expects really are in r. Near 1: the proxy tells r apart; low (e.g. Multiracial): it cannot."""
    d = P.sum(0)
    return (P ** 2).sum(0) / np.where(d > 0, d, np.nan)


def weighted_air(P: np.ndarray, a: np.ndarray, groups: list[str], ref: str = REFERENCE, seed: int = 0) -> list[dict[str, Any]]:
    """Per group: effective n = Σ p_ir, approval rate, AIR and difference vs the reference, each with a 95% CI;
    the corrected estimate (headline) and the plain weighted one (attenuated toward no gap)."""
    n = len(a)
    k = groups.index(ref) if ref in groups else int(np.argmax(P.sum(0)))
    plain, corr, den = _estimates(np.ones((1, n)), P, a)
    rng = np.random.default_rng(seed)
    W = rng.multinomial(n, np.full(n, 1 / n), size=B).astype(float)
    bp, bc, _ = _estimates(W, P, a)
    sep = separability(P)
    q = lambda x: [float(v) for v in np.nanpercentile(x, [2.5, 97.5])]  # noqa: E731
    out = []
    for j, g in enumerate(groups):
        eff = float(den[0, j])
        row: dict[str, Any] = {"group": g, "n_eff": eff, "separability": float(sep[j]), "reference": j == k,
                               "judged": bool(eff >= MIN_EFF_N and sep[j] >= MIN_SEPARABILITY)}
        for name, est, boot in (("", corr, bc), ("plain_", plain, bp)):
            r, r0 = float(est[0, j]), float(est[0, k])
            row[f"{name}approval"] = r
            row[f"{name}approval_ci"] = q(boot[:, j])
            if j == k:
                row.update({f"{name}air": 1.0, f"{name}air_ci": [1.0, 1.0], f"{name}diff": 0.0, f"{name}diff_ci": [0.0, 0.0]})
            else:
                row.update({f"{name}air": r / r0 if r0 > 0 else None, f"{name}air_ci": q(boot[:, j] / boot[:, k]),
                            f"{name}diff": r - r0, f"{name}diff_ci": q(boot[:, j] - boot[:, k])})
        air = row["air"]
        row["flag"] = None if j == k or not row["judged"] or air is None else (
            "below 0.80" if air < 0.8 else "0.80–0.90" if air < 0.9 else None)
        out.append(row)
    return out


def hard_air(truth: np.ndarray, a: np.ndarray, groups: list[str], ref: str = REFERENCE) -> list[dict[str, Any]]:
    """The same table with each applicant's true group (one-hot weights: both estimates are the plain group rates)."""
    P = (truth[:, None] == np.array(groups)[None, :]).astype(float)
    rows = weighted_air(P, a, groups, ref)
    for r in rows:
        r["n_true"] = int(round(r.pop("n_eff")))
        for kk in [x for x in r if x.startswith("plain_") or x in ("separability", "judged", "flag")]:
            r.pop(kk)
    return rows


def weighted_metricframe(P: np.ndarray, y: np.ndarray, yhat: np.ndarray, groups: list[str]) -> dict[str, Any]:
    """F4's fairness metrics (fairlearn MetricFrame) on probability-weighted groups: each applicant appears once per
    group with sample_weight p_ir, so every rate is a p-weighted rate. Count is the effective n = Σ p_ir."""
    from fairlearn.metrics import MetricFrame, false_positive_rate, selection_rate, true_positive_rate
    from sklearn.metrics import accuracy_score, precision_score

    n, K = P.shape
    yy, pp = np.tile(y, K), np.tile(yhat, K)
    gg = np.repeat(np.array(groups), n)
    w = P.T.reshape(-1)
    keep = w > 1e-9
    yy, pp, gg, w = yy[keep], pp[keep], gg[keep], w[keep]
    metrics = {
        "count": lambda yt, yp, sample_weight: float(np.sum(sample_weight)),
        "selection_rate": selection_rate,
        "base_rate": lambda yt, yp, sample_weight: float(np.average(yt, weights=sample_weight)),
        "accuracy": accuracy_score, "tpr": true_positive_rate, "fpr": false_positive_rate,
        "precision": lambda yt, yp, sample_weight: precision_score(yt, yp, sample_weight=sample_weight, zero_division=0),
    }
    sp = {m: {"sample_weight": w} for m in metrics}
    mf = MetricFrame(metrics=metrics, y_true=yy, y_pred=pp, sensitive_features=gg, sample_params=sp)
    ov = {"count": float(n), "selection_rate": float(yhat.mean()), "base_rate": float(y.mean()),
          "accuracy": float((y == yhat).mean()),
          "tpr": float(yhat[y == 1].mean()) if (y == 1).any() else None,
          "fpr": float(yhat[y == 0].mean()) if (y == 0).any() else None,
          "precision": float(y[yhat == 1].mean()) if (yhat == 1).any() else None}
    bg = mf.by_group
    return {"metrics": list(metrics), "overall": ov,
            "by_group": [{"group": str(g), **{k: (None if pd.isna(v) else float(v)) for k, v in bg.loc[g].items()}}
                         for g in groups if g in bg.index],
            "difference": {k: (None if pd.isna(v) else float(v)) for k, v in mf.difference().items() if k != "count"}}


def proxy_quality(P: np.ndarray, groups: list[str], truth: np.ndarray | None) -> dict[str, Any]:
    """How good the proxy is. Without the truth: the accuracy the proxy expects of itself, mean_i max_r p_ir (exact if
    the probabilities are calibrated). With it: argmax accuracy, Brier, per-group recall and calibration
    (mean p_ir vs the true share), and the confusion matrix of argmax vs truth."""
    out: dict[str, Any] = {"expected_accuracy": float(P.max(axis=1).mean()),
                           "estimated_shares": {g: float(P[:, j].mean()) for j, g in enumerate(groups)}}
    if truth is None:
        return out
    T = (truth[:, None] == np.array(groups)[None, :]).astype(float)
    top = np.array(groups)[P.argmax(axis=1)]
    out["accuracy"] = float((top == truth).mean())
    out["brier"] = float(((P - T) ** 2).sum(axis=1).mean())
    out["by_group"] = [{"group": g, "n_true": int(T[:, j].sum()), "true_share": float(T[:, j].mean()),
                        "estimated_share": float(P[:, j].mean()),
                        "recall": float((top[T[:, j] == 1] == g).mean()) if T[:, j].any() else None,
                        "precision": float((truth[top == g] == g).mean()) if (top == g).any() else None,
                        "mean_p_true": float(P[T[:, j] == 1, j].mean()) if T[:, j].any() else None}
                       for j, g in enumerate(groups)]
    out["confusion"] = [[int(((truth == a) & (top == b)).sum()) for b in groups] for a in groups]
    return out


def match_truth(values: pd.Series, groups: list[str], labels: dict[str, str]) -> np.ndarray | None:
    """Map a column's values onto the BISG groups (key or label, case-insensitive). None when most rows don't map."""
    alias = {g: g for g in groups} | {labels[g].lower(): g for g in groups} | {
        "asian": "api", "aapi": "api", "two or more": "multi", "2prace": "multi", "native american": "aian",
        "latino": "hispanic", "african american": "black"}
    m = values.astype(str).str.strip().str.lower().map(alias)
    return m.to_numpy(dtype=object) if m.notna().mean() >= 0.95 else None
