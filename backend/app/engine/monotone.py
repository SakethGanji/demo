"""F8: monotone constraints. The request, what AutoGluon's pipeline made of it, and the check after the fit.

* `normalise(...)`: `monotone = {constraints: {column: increasing | decreasing | 1 | -1 | none}, compliant_only}` →
  `{constraints: {column: ±1}, compliant_only, stack}` or a ValueError worded for the user. Constraints apply to
  numeric columns of binary and regression runs. A category has no order, text and dates are expanded by AutoGluon
  into features that are not the raw value, and for multiclass "increasing" names no single score (softmax couples
  the classes), so those are refused.
* The models: `ensemble_models.monotone` (GBM_MONO, CAT_MONO, XGB_MONO) map the constraints onto the columns each
  model is trained on, and constrain stack features to +1 so stack levels stay monotone.
* `ice_check(predictor, frame, constraints, model)`: individual conditional expectation on raw inputs. For n rows of
  the holdout and a grid x₁ < … < x_G of the column's quantiles, f(xᵢ with column j set to x_g) through the
  predictor's own `predict_proba` / `predict`, so the check passes through AutoGluon's feature generator exactly as a
  served row does. PD(x_g) = (1/n) Σᵢ ICEᵢ(x_g). A curve violates an increasing constraint where
  ICEᵢ(x_{g+1}) − ICEᵢ(x_g) < −ε (ε = 1e-9), and a decreasing one where it is > ε.
"""

from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd

EPS = 1e-9
GRID = 25
ROWS = 200
WORDS = {"increasing": 1, "inc": 1, "up": 1, "↑": 1, "+1": 1, "1": 1, "decreasing": -1, "dec": -1, "down": -1,
         "↓": -1, "-1": -1, "none": 0, "0": 0, "": 0}
COMPLIANT = ("GBM", "CAT", "XGB")
COMPLIANT_NAMES = {"GBM": "LightGBM", "CAT": "CatBoost", "XGB": "XGBoost"}


def _sign(v: Any, col: str) -> int:
    if v is None:
        return 0
    if isinstance(v, bool):
        raise ValueError(f"{col}: use 'increasing', 'decreasing' or 'none' (or 1, -1, 0).")
    if isinstance(v, (int, float)):
        if v not in (1, -1, 0):
            raise ValueError(f"{col}: a constraint is +1 (increasing), -1 (decreasing) or 0 (none), not {v}.")
        return int(v)
    s = WORDS.get(str(v).strip().lower())
    if s is None:
        raise ValueError(f"{col}: use 'increasing', 'decreasing' or 'none' (got {v!r}).")
    return s


def normalise(spec: dict[str, Any] | None, *, problem_type: str | None, columns: dict[str, dict[str, Any]], label: str,
              drop: list[str], column_types: dict[str, str] | None = None) -> dict[str, Any] | None:
    if not spec:
        return None
    if not isinstance(spec, dict):
        raise ValueError("monotone must be {constraints: {column: increasing | decreasing}, compliant_only}.")
    unknown = [k for k in spec if k not in ("constraints", "compliant_only", "stack")]
    if unknown:
        raise ValueError(f"monotone has no field {', '.join(map(repr, unknown))} (use constraints, compliant_only).")
    raw = spec.get("constraints") or {}
    if not isinstance(raw, dict):
        raise ValueError("monotone.constraints must be {column: increasing | decreasing}.")
    cons: dict[str, int] = {}
    types = column_types or {}
    for col, v in raw.items():
        s = _sign(v, col)
        if not s:
            continue
        if col not in columns:
            raise ValueError(f"{col}: no such column.")
        if col == label:
            raise ValueError(f"{col} is the label; constraints apply to features.")
        if col in drop:
            raise ValueError(f"{col} is dropped from training; a constraint on it would have no effect.")
        t = types.get(col, columns[col].get("type"))
        if t != "number":
            why = {"category": "a category has no order to be monotone in",
                   "text": "AutoGluon turns text into n-gram counts, not one value to be monotone in",
                   "datetime": "AutoGluon splits a date into year, month, day… features, which are not the date itself",
                   "id": "an ID column is not a quantity", "boolean": "use a 0/1 number instead"}.get(t, f"its type is {t}")
            raise ValueError(f"{col}: constraints apply to numeric columns ({why}).")
        cons[col] = s
    if not cons:
        return None
    if problem_type not in ("binary", "regression"):
        raise ValueError(f"monotone constraints apply to binary and regression labels, not {problem_type}: for "
                         "multiclass, the softmax couples the classes, so no class probability is monotone by construction.")
    co = spec.get("compliant_only", True)
    if not isinstance(co, bool):
        raise ValueError("monotone.compliant_only must be true or false.")
    return {"constraints": cons, "compliant_only": co, "stack": bool(spec.get("stack", True))}


def describe(m: dict[str, Any]) -> str:
    return ", ".join(f"{c} {'↑' if s > 0 else '↓'}" for c, s in m["constraints"].items())


# --------------------------------------------------------------------------- after the fit


def _child(trainer, name: str):
    m = trainer.load_model(name)
    if hasattr(m, "load_child") and getattr(m, "models", None):
        try:
            return m, m.load_child(m.models[0])
        except Exception:  # noqa: BLE001
            return m, None
    return m, m


def model_map(predictor) -> list[dict[str, Any]]:
    """Per model: is it constrained, the signs its library received ({internal column: sign}), its stack level, and
    for weighted ensembles the base models and weights (non-negative)."""
    from .catalogue import family_for_class

    trainer = predictor._trainer
    rows: list[dict[str, Any]] = []
    for name in predictor.model_names():
        try:
            m, ch = _child(trainer, name)
        except Exception:  # noqa: BLE001
            continue
        cls = type(ch).__name__ if ch is not None else type(m).__name__
        fam = family_for_class(cls)
        r: dict[str, Any] = {"model": name, "class": cls, "family": fam,
                             "stack_level": trainer.get_model_attribute(name, "level")}
        if hasattr(m, "_get_model_weights"):
            w = {k: float(v) for k, v in m._get_model_weights().items()}
            r["kind"] = "weighted_ensemble"
            r["weights"] = w
            r["constrained"] = None  # decided below from its base models
        else:
            applied = getattr(ch, "monotone_applied", None) if ch is not None else None
            r["kind"] = "model"
            r["constrained"] = applied is not None
            r["applied"] = applied or {}
            r["internal"] = (getattr(ch, "monotone_internal", None) or {}) if ch is not None else {}
            r["n_columns"] = len(getattr(ch, "monotone_columns", None) or []) or None
        rows.append(r)
    by = {r["model"]: r for r in rows}

    def ok(name: str, seen: frozenset = frozenset()) -> bool:
        r = by.get(name)
        if r is None or name in seen:
            return False
        if r["kind"] == "weighted_ensemble":
            return all(ok(b, seen | {name}) for b, w in r["weights"].items() if w > 0)
        if not r["constrained"]:
            return False
        stack_inputs = [c for c in r["applied"] if c in by]
        return all(ok(b, seen | {name}) for b in stack_inputs)

    for r in rows:
        r["monotone_by_construction"] = ok(r["model"])
        if r["kind"] == "weighted_ensemble":
            r["constrained"] = r["monotone_by_construction"]
    return rows


def feature_links(predictor, constraints: dict[str, int]) -> dict[str, list[str]]:
    """Raw column → the features AutoGluon's feature generator made of it (empty: dropped)."""
    try:
        links = predictor._learner.feature_generator.get_feature_links()
    except Exception:  # noqa: BLE001
        links = {}
    return {c: list(links.get(c, [])) for c in constraints}


def _grid(values: pd.Series, g: int) -> np.ndarray:
    v = pd.to_numeric(values, errors="coerce").dropna().to_numpy(dtype=float)
    if not len(v):
        return np.array([])
    q = np.unique(np.quantile(v, np.linspace(0, 1, g)))
    return q


def _score(predictor, X: pd.DataFrame, model: str | None) -> np.ndarray:
    if predictor.problem_type == "binary":
        return predictor.predict_proba(X, model=model, as_multiclass=False).to_numpy(dtype=float)
    return predictor.predict(X, model=model).to_numpy(dtype=float)


def ice_check(predictor, frame: pd.DataFrame, constraints: dict[str, int], model: str | None = None, *,
              grid: int = GRID, rows: int = ROWS, seed: int = 0, curves: int = 0) -> dict[str, Any]:
    """ICE / PD of `model` (None: the predictor's best) on raw rows of `frame` for each constrained column."""
    feats = [c for c in predictor.original_features]
    base = frame[[c for c in feats if c in frame.columns]]
    sample = base.sample(min(rows, len(base)), random_state=seed) if len(base) > rows else base
    out: dict[str, Any] = {}
    for col, s in constraints.items():
        if col not in base.columns:
            out[col] = {"sign": s, "error": f"{col} is not a feature of this predictor"}
            continue
        xs = _grid(frame[col], grid)
        if len(xs) < 2:
            out[col] = {"sign": s, "grid": xs.tolist(), "error": "fewer than two distinct values"}
            continue
        X = sample.loc[sample.index.repeat(len(xs))].copy()
        X[col] = np.tile(xs, len(sample))
        ice = _score(predictor, X, model).reshape(len(sample), len(xs))
        d = np.diff(ice, axis=1) * s
        bad = d < -EPS
        pdp = ice.mean(axis=0)
        dpd = np.diff(pdp) * s
        r = {"sign": s, "grid": xs.tolist(), "pd": pdp.tolist(), "n_curves": int(len(sample)), "n_steps": int(d.size),
             "curves_violating": int(bad.any(axis=1).sum()), "steps_violating": int(bad.sum()),
             "max_violation": float(max(0.0, -float(d.min()))), "pd_monotone": bool((dpd >= -EPS).all()),
             "pd_max_violation": float(max(0.0, -float(dpd.min()))), "pd_range": float(pdp.max() - pdp.min()),
             "monotone": bool(not bad.any())}
        if curves:
            r["ice"] = ice[:curves].round(6).tolist()
            r["ice_rows"] = [int(i) for i in sample.index[:curves]]
        out[col] = r
    return out


def report(predictor, holdout: pd.DataFrame, m: dict[str, Any]) -> dict[str, Any]:
    """The run's monotone section: the request, where each constraint landed, which models are monotone by
    construction, and the ICE / PD check of every model on the holdout (curves for the best model)."""
    cons = m["constraints"]
    models = model_map(predictor)
    for r in models:
        try:
            r["check"] = {c: {k: v for k, v in x.items() if k not in ("grid", "pd")}
                          for c, x in ice_check(predictor, holdout, cons, r["model"], rows=100).items()}
            r["monotone_checked"] = all(x.get("monotone", False) for x in r["check"].values())
        except Exception as e:  # noqa: BLE001 — a model that cannot predict (e.g. no longer in memory) is reported
            r["check_error"] = f"{type(e).__name__}: {e}"
    best = predictor.model_best
    served = ice_check(predictor, holdout, cons, best, curves=30)
    links = feature_links(predictor, cons)
    where = {}
    for c in cons:
        hit = [r["model"] for r in models if r.get("kind") == "model" and c in (r.get("applied") or {})]
        where[c] = {"features": links.get(c, []), "models": hit,
                    "status": "constrained" if hit else ("dropped by AutoGluon's feature generator" if not links.get(c) else "not used by a constrained model")}
    non = [r["model"] for r in models if r["kind"] == "model" and not r["constrained"]]
    return {**m, "columns": where, "models": models, "best_model": best, "served": served,
            "best_monotone": all(x.get("monotone", False) for x in served.values()),
            "unconstrained_models": non,
            "grid_points": GRID, "eps": EPS}
