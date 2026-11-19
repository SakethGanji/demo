"""Quantile regression (F9): `TabularPredictor(problem_type='quantile', quantile_levels=[τ₁, …, τ_L])`.

AutoGluon fits every family that supports quantiles (LightGBM, CatBoost, the neural nets, the quantile forests, …) to
the pinball loss and predicts one column per level τ. Its eval_metric is `pinball_loss`:

    ρ_τ(y, q) = max(τ (y − q), (τ − 1)(y − q)),      pinball = (1/L) Σ_τ (1/n) Σᵢ ρ_τ(yᵢ, q̂_τ(xᵢ))

This module reads those predictions on the holdout and reports what a range forecast promises:
- per level τ: the pinball loss and F̂(τ) = (1/n) Σᵢ 𝟙[yᵢ ≤ q̂_τ(xᵢ)], the share of rows at or below the predicted
  quantile (calibrated: F̂(τ) ≈ τ);
- per interval [q̂_lo, q̂_hi] (every pair τ, 1 − τ, and the outermost levels): its nominal coverage hi − lo, the
  holdout coverage (1/n) Σᵢ 𝟙[q̂_lo ≤ yᵢ ≤ q̂_hi] with a 95% Wilson interval, and its width;
- crossing: the share of rows whose quantiles are out of order (q̂_τ > q̂_τ' for some τ < τ').

Conformal recalibration is AutoGluon's own (`fit(calibrate=...)`, CQR: Romano et al. 2019): for each level it adds a
constant c_τ to the served model's predictions, the conformity score of the validation rows (out-of-fold rows when
bagged) at rank ⌈τ (n + 1)⌉ (τ > ½) or ⌈(1 − τ)(n + 1)⌉ (τ < ½). The worker records the holdout both raw (c = 0)
and calibrated (+c), whichever the predictor serves: the offsets are computed the way AutoGluon would when it did
not apply them.
"""

from __future__ import annotations

import math
from typing import Any

import numpy as np
import pandas as pd

from ..stats import wilson

DEFAULT_LEVELS = [0.1, 0.5, 0.9]


# --------------------------------------------------------------------------- the request


def normalise_levels(levels: Any) -> list[float]:
    """Quantile levels as sorted, distinct floats in (0, 1). Raises ValueError with the reason."""
    if levels is None:
        return list(DEFAULT_LEVELS)
    if isinstance(levels, (int, float)):
        levels = [levels]
    if not isinstance(levels, (list, tuple)) or not levels:
        raise ValueError("give quantile_levels as a list of numbers between 0 and 1, e.g. [0.1, 0.5, 0.9].")
    out = []
    for v in levels:
        try:
            t = float(v)
        except (TypeError, ValueError):
            raise ValueError(f"{v!r} is not a number; quantile levels are numbers between 0 and 1.") from None
        if not (0 < t < 1) or math.isnan(t):
            raise ValueError(f"{v!r} is outside (0, 1); a quantile level τ satisfies 0 < τ < 1.")
        out.append(round(t, 6))
    if len(set(out)) != len(out):
        raise ValueError("each quantile level once: " + ", ".join(f"{t:g}" for t in out) + ".")
    return sorted(out)


def centre(levels: list[float]) -> float:
    """The point prediction a quantile model serves where one number is needed: τ = 0.5 when it is a level, else the
    level closest to it."""
    return min(levels, key=lambda t: (abs(t - 0.5), t))


def col(t: float) -> str:
    """Column name of level τ in saved predictions and batch outputs: q_0.1, q_0.5, …"""
    return f"q_{t:g}"


def intervals(levels: list[float]) -> list[dict[str, Any]]:
    """Central intervals [τ, 1 − τ] for every τ < ½ whose partner is a level, plus [min τ, max τ] when that is not one
    of them (and there are two levels or more). Widest first."""
    lv = sorted(levels)
    out, seen = [], set()
    for t in lv:
        partner = round(1 - t, 6)
        if t < 0.5 and partner in lv:
            out.append({"lo": t, "hi": partner, "nominal": round(partner - t, 6), "symmetric": True})
            seen.add((t, partner))
    if len(lv) >= 2 and (lv[0], lv[-1]) not in seen:
        out.append({"lo": lv[0], "hi": lv[-1], "nominal": round(lv[-1] - lv[0], 6), "symmetric": False})
    out.sort(key=lambda d: -d["nominal"])
    return out


def main_interval(levels: list[float]) -> dict[str, Any] | None:
    iv = intervals(levels)
    return iv[0] if iv else None


# --------------------------------------------------------------------------- predictions


def levels_of(predictor) -> list[float]:
    return [float(t) for t in predictor.quantile_levels]


def qframe(predictor, X: pd.DataFrame, model: str | None = None) -> pd.DataFrame:
    """Predicted quantiles: one float column per level (column names are the levels as floats)."""
    q = predictor.predict(X, model=model)
    q = pd.DataFrame(q) if not isinstance(q, pd.DataFrame) else q
    q.columns = [float(c) for c in q.columns]
    return q.astype(float)


def point(q: pd.DataFrame) -> pd.Series:
    """The centre level's column (τ = 0.5, or the closest level)."""
    return q[centre(list(q.columns))]


def as_records(q: pd.DataFrame) -> list[dict[str, float]]:
    """[{"0.1": …, "0.5": …, "0.9": …}, …] for JSON."""
    return [{f"{float(t):g}": float(v) for t, v in r.items()} for r in q.to_dict(orient="records")]


def pinball(y: np.ndarray, q: np.ndarray, t: float) -> np.ndarray:
    e = y - q
    return np.maximum(t * e, (t - 1) * e)


def evaluate(y: np.ndarray, Q: np.ndarray, levels: list[float]) -> dict[str, Any]:
    """Pinball loss and calibration per level, coverage and width per interval, and crossing, on (y, Q)."""
    y = np.asarray(y, dtype=float)
    Q = np.asarray(Q, dtype=float)
    n = len(y)
    per = []
    for j, t in enumerate(levels):
        below = int((y <= Q[:, j]).sum())
        lo, hi = wilson(below, n, empty=(None, None))
        per.append({"tau": t, "pinball": float(pinball(y, Q[:, j], t).mean()) if n else None,
                    "below": below / n if n else None, "below_ci": [lo, hi], "gap": (below / n - t) if n else None})
    ivs = []
    for iv in intervals(levels):
        a, b = Q[:, levels.index(iv["lo"])], Q[:, levels.index(iv["hi"])]
        inside = int(((y >= a) & (y <= b)).sum())
        lo, hi = wilson(inside, n, empty=(None, None))
        w = b - a
        ivs.append({**iv, "coverage": inside / n if n else None, "coverage_ci": [lo, hi], "inside": inside, "n": n,
                    "below_lo": float((y < a).mean()) if n else None, "above_hi": float((y > b).mean()) if n else None,
                    "mean_width": float(w.mean()) if n else None, "median_width": float(np.median(w)) if n else None})
    order_bad = (np.diff(Q, axis=1) < -1e-9).any(axis=1) if Q.shape[1] > 1 else np.zeros(n, dtype=bool)
    return {"n": n, "pinball": float(np.mean([p["pinball"] for p in per])) if n else None, "levels": per,
            "intervals": ivs, "crossing_share": float(order_bad.mean()) if n else None,
            "crossing_rows": int(order_bad.sum())}


# --------------------------------------------------------------------------- conformal (AutoGluon's CQR)


def conformal(predictor, model: str | None = None) -> dict[str, Any]:
    """The CQR offsets c_τ of `model` (default: the best): the ones AutoGluon applied (`model.conformalize`), or,
    when it did not calibrate, the ones `trainer.calibrate_model()` would compute, on the same validation rows
    (the held-out validation split, or the out-of-fold predictions when bagged). Nothing is saved."""
    from autogluon.core.calibrate.conformity_score import compute_conformity_score

    tr = predictor._trainer
    levels = levels_of(predictor)
    name = model or predictor.model_best
    m = tr.load_model(name)
    applied = getattr(m, "conformalize", None)
    out: dict[str, Any] = {"model": name, "applied": applied is not None,
                           "method": "AutoGluon fit(calibrate=True): conformalized quantile regression (CQR), a "
                                     "constant c_τ per level added to the served model's quantiles"}
    if applied is not None:
        c = np.asarray(applied, dtype=float).reshape(-1)
        out["offsets"] = {f"{t:g}": float(v) for t, v in zip(levels, c)}
        out["source"] = "applied by AutoGluon at fit time"
        return out
    # Not applied: compute what AutoGluon would, on the data it would use.
    og = name
    for parent, full in tr.model_refit_map().items():
        if full == name:
            og = parent
            break
    try:
        if tr.has_val:
            Xv = tr.load_X_val()
            pv = np.asarray(tr.predict_proba(Xv, og), dtype=float)
            yv = tr.load_y_val().to_numpy(dtype=float)
            src = "validation split"
        else:
            pv = np.asarray(tr.get_model_oof(og), dtype=float)
            yv = tr.load_y().to_numpy(dtype=float)
            src = "out-of-fold predictions"
        ok = ~np.isnan(pv).any(axis=1)
        c = compute_conformity_score(y_val_pred=pv[ok], y_val=yv[ok], quantile_levels=levels)
    except Exception as e:  # noqa: BLE001 — the report says why, the run does not fail
        out["error"] = f"{type(e).__name__}: {e}"
        return out
    out["offsets"] = {f"{t:g}": float(v) for t, v in zip(levels, c)}
    out["n_val"] = int(ok.sum())
    out["source"] = f"computed on the {src} ({int(ok.sum())} rows) the way AutoGluon would; not applied"
    return out


def offsets_vector(conf: dict[str, Any], levels: list[float]) -> np.ndarray | None:
    off = conf.get("offsets")
    if not off:
        return None
    return np.asarray([off.get(f"{t:g}", 0.0) for t in levels], dtype=float)


# --------------------------------------------------------------------------- the report


def fan(y: np.ndarray, Q: np.ndarray, levels: list[float], rows: np.ndarray, max_points: int = 400) -> dict[str, Any]:
    """Holdout rows sorted by the centre prediction (subsampled evenly to `max_points`): the fan chart."""
    c = levels.index(centre(levels))
    order = np.argsort(Q[:, c], kind="stable")
    if len(order) > max_points:
        order = order[np.linspace(0, len(order) - 1, max_points).round().astype(int)]
    return {"rank": [int(i) for i in range(len(order))], "row": [int(r) for r in rows[order]],
            "y": [float(v) for v in y[order]],
            "q": {f"{t:g}": [float(v) for v in Q[order, j]] for j, t in enumerate(levels)},
            "n": int(len(y)), "sampled": bool(len(y) > max_points), "sorted_by": f"{centre(levels):g}"}


def report(predictor, holdout: pd.DataFrame, label: str, per_model: bool = True,
           conf: dict[str, Any] | None = None) -> dict[str, Any]:
    """Everything Results shows for a quantile run, on the holdout. `conf` overrides the conformal offsets (the
    registry passes the source run's when the deployed copy no longer has the validation rows)."""
    levels = levels_of(predictor)
    y = holdout[label].to_numpy(dtype=float)
    Q = qframe(predictor, holdout).to_numpy()
    conf = conf or conformal(predictor)
    c = offsets_vector(conf, levels)
    served = evaluate(y, Q, levels)
    raw = cal = None
    if c is not None:
        raw_Q, cal_Q = (Q - c, Q) if conf["applied"] else (Q, Q + c)
        raw = evaluate(y, raw_Q, levels)
        cal = evaluate(y, cal_Q, levels)
    models = []
    if per_model:
        for m in predictor.model_names(can_infer=True):
            try:
                Qm = qframe(predictor, holdout, model=m).to_numpy()
            except Exception:  # noqa: BLE001
                continue
            e = evaluate(y, Qm, levels)
            mi = e["intervals"][0] if e["intervals"] else {}
            models.append({"model": m, "pinball": e["pinball"], "levels": [{"tau": p["tau"], "pinball": p["pinball"],
                                                                           "below": p["below"]} for p in e["levels"]],
                           "coverage": mi.get("coverage"), "nominal": mi.get("nominal"), "mean_width": mi.get("mean_width"),
                           "crossing_share": e["crossing_share"], "conformalized": getattr(predictor._trainer.load_model(m), "conformalize", None) is not None})
        models.sort(key=lambda r: r["pinball"])
    return {"levels": levels, "centre": centre(levels), "main_interval": main_interval(levels),
            "model": predictor.model_best, "holdout": served, "raw": raw, "calibrated": cal, "conformal": conf,
            "served": "calibrated" if conf.get("applied") else "raw", "models": models,
            "fan": fan(y, Q, levels, holdout.index.to_numpy()),
            "formulas": {"pinball": "ρ_τ(y, q) = max(τ (y − q), (τ − 1)(y − q))",
                         "loss": "pinball = (1/L) Σ_τ (1/n) Σᵢ ρ_τ(yᵢ, q̂_τ(xᵢ))",
                         "below": "F̂(τ) = (1/n) Σᵢ 𝟙[yᵢ ≤ q̂_τ(xᵢ)]",
                         "coverage": "cov = (1/n) Σᵢ 𝟙[q̂_lo(xᵢ) ≤ yᵢ ≤ q̂_hi(xᵢ)]",
                         "conformal": "q̃_τ(x) = q̂_τ(x) + c_τ"}}


def bootstrap(y: np.ndarray, Q: np.ndarray, levels: list[float], B: int = 500, seed: int = 0) -> dict[str, Any]:
    """Point estimates and 95% percentile-bootstrap intervals of the pinball loss (overall and per level) and of the
    main interval's coverage and width: the registry's holdout metrics for a quantile version."""
    y = np.asarray(y, dtype=float)
    Q = np.asarray(Q, dtype=float)
    n = len(y)
    L = np.stack([pinball(y, Q[:, j], t) for j, t in enumerate(levels)], axis=1)  # n × L
    iv = main_interval(levels)
    fns: dict[str, tuple[str, Any]] = {
        "pinball_loss": ("pinball = (1/L) Σ_τ (1/n) Σᵢ ρ_τ(yᵢ, q̂_τ)", lambda i: float(L[i].mean())),
    }
    for j, t in enumerate(levels):
        fns[f"pinball_{t:g}"] = (f"(1/n) Σᵢ ρ_{t:g}(yᵢ, q̂_{t:g})", (lambda j: lambda i: float(L[i, j].mean()))(j))
    if iv:
        a, b = Q[:, levels.index(iv["lo"])], Q[:, levels.index(iv["hi"])]
        inside = ((y >= a) & (y <= b)).astype(float)
        w = b - a
        fns["coverage"] = (f"(1/n) Σᵢ 𝟙[q̂_{iv['lo']:g} ≤ yᵢ ≤ q̂_{iv['hi']:g}]  (nominal {iv['nominal']:g})",
                           lambda i: float(inside[i].mean()))
        fns["mean_width"] = (f"(1/n) Σᵢ (q̂_{iv['hi']:g} − q̂_{iv['lo']:g})", lambda i: float(w[i].mean()))
    c = levels.index(centre(levels))
    fns["mae_centre"] = (f"MAE of q̂_{centre(levels):g} = (1/n) Σᵢ |yᵢ − q̂_{centre(levels):g}|",
                         lambda i: float(np.abs(y[i] - Q[i, c]).mean()))
    rng = np.random.default_rng(seed)
    idx = rng.integers(0, n, size=(B, n))
    out = {}
    allrows = np.arange(n)
    for name, (formula, fn) in fns.items():
        boots = [fn(idx[b]) for b in range(B)]
        out[name] = {"value": fn(allrows), "ci_low": float(np.percentile(boots, 2.5)),
                     "ci_high": float(np.percentile(boots, 97.5)), "formula": formula, "B": B}
    return {"metrics": out, "n": n, "B": B, "seed": seed, "method": "percentile bootstrap over holdout rows, 95%"}


def holdout_columns(predictor, holdout: pd.DataFrame) -> tuple[pd.Series, dict[str, np.ndarray]]:
    """(centre prediction, {q_τ: values}) for a saved holdout_predictions.csv."""
    q = qframe(predictor, holdout)
    return point(q), {col(t): q[t].to_numpy() for t in q.columns}


def from_saved(df: pd.DataFrame) -> tuple[list[float], np.ndarray] | None:
    """(levels, Q) from the q_τ columns of a saved predictions frame, or None if there are none."""
    cols = [c for c in df.columns if c.startswith("q_")]
    if not cols:
        return None
    levels = sorted(float(c[2:]) for c in cols)
    return levels, df[[col(t) for t in levels]].to_numpy(dtype=float)
