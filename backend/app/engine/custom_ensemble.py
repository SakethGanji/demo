"""A weighted ensemble of models the user chose, fitted on a finished run:  python -m app.engine.custom_ensemble <run_dir> <request.json>

Wraps `TabularPredictor.fit_weighted_ensemble(base_models=...)` (Caruana's greedy ensemble selection on the same
validation data the run's own WeightedEnsemble used: out-of-fold predictions for bagged runs, the tuning split
otherwise). It runs in its own process because it writes a new model into the predictor folder; the API drops its
cached copy of the predictor afterwards.

The run's default model (`model_best`) is not changed: AutoGluon would switch to the new ensemble if it scored
higher on validation, but a user's experiment should not silently change what the run predicts with. The result
says plainly whether the new ensemble beats the best single model, on validation and on the untouched holdout.

Prints one JSON object on the last line of stdout.
"""

from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path
from typing import Any

os.environ.setdefault("MLFLOW_DISABLE_AGENT_HINT", "1")


def _natural(metric, score):
    import pandas as pd

    try:
        return None if score is None or pd.isna(score) else float(metric.convert_score_to_original(score))
    except Exception:  # noqa: BLE001
        return None


def _row(lb, model: str, metric, n_holdout: int) -> dict[str, Any]:
    """A model's scores. Validation from the model itself; holdout from it, or from its refit twin (model_FULL)
    when the model can't predict (refit presets keep only the refit copies of bagged models)."""
    import pandas as pd

    by = lb.set_index("model")
    r = by.loc[model].to_dict()
    infer = model
    if pd.isna(r.get("score_test")) and f"{model}_FULL" in by.index:
        infer = f"{model}_FULL"
    t = by.loc[infer].to_dict()
    pt = t.get("pred_time_test")
    return {
        "model": model, "predict_model": infer,
        "stack_level": int(r.get("stack_level") or 1),
        "score_val": r.get("score_val"), "score_test": t.get("score_test"),
        "score_val_natural": _natural(metric, r.get("score_val")),
        "score_test_natural": _natural(metric, t.get("score_test")),
        "fit_time": r.get("fit_time"), "pred_time_test": pt,
        "pred_time_test_per_row": (pt / n_holdout) if pt is not None and not pd.isna(pt) and n_holdout else None,
    }


def fit(cfg: dict[str, Any], base_models: list[str], suffix: str) -> dict[str, Any]:
    from autogluon.tabular import TabularPredictor

    import ensemble_models  # noqa: F401 — the TF-IDF models must be importable to load the predictor

    from ..splits import load_splits
    from .jsonsafe import jsonable
    from .serving import holdout_frame

    t0 = time.time()
    predictor = TabularPredictor.load(cfg["predictor_path"], verbosity=0, require_py_version_match=False)
    trainer = predictor._trainer
    names = set(predictor.model_names())
    unknown = [m for m in base_models if m not in names]
    if unknown:
        raise ValueError(f"No model named {', '.join(map(repr, unknown))} in this run.")
    full = [m for m in base_models if m.endswith("_FULL")]
    if full:
        raise ValueError(f"Refit models ({', '.join(full)}) have no validation predictions to weight; "
                         "choose the models they were refit from.")
    # Refit presets (good/high quality) keep only the refit copies of bagged models, which can predict; the
    # bagged ones can't. Then the new ensemble is refit too, and its refit copy is the one that predicts.
    refit = not all(trainer.get_model_attribute(m, "can_infer") for m in base_models)
    best_before = predictor.model_best
    created = predictor.fit_weighted_ensemble(base_models=base_models, name_suffix=suffix, refit_full=refit)
    if not created:
        raise RuntimeError("AutoGluon could not fit a weighted ensemble on these models.")
    model = created[0]
    if predictor.model_best != best_before:
        predictor.set_model_best(best_before, save_trainer=True)
    fit_seconds = time.time() - t0

    m = trainer.load_model(model)
    weights = {k: float(v) for k, v in m._get_model_weights().items()}
    weights = dict(sorted(weights.items(), key=lambda kv: -kv[1]))

    holdout = holdout_frame(Path(cfg["csv_path"]), load_splits(Path(cfg["splits_path"])), cfg["label"], cfg["drop_columns"])
    lb = predictor.leaderboard(holdout, silent=True)
    metric = predictor.eval_metric
    n = len(holdout)
    new = _row(lb, model, metric, n)
    # AutoGluon names every weighted ensemble so; refit copies are compared through the model they came from.
    singles_all = lb[~lb["model"].str.startswith("WeightedEnsemble") & ~lb["model"].str.endswith("_FULL")]
    singles = singles_all[singles_all["model"].isin(base_models)]

    def top(df):
        return _row(lb, df.sort_values("score_val", ascending=False).iloc[0]["model"], metric, n) if len(df) else None

    best_chosen, best_single = top(singles), top(singles_all)
    run_best_name = best_before[:-5] if best_before.endswith("_FULL") and best_before[:-5] in names else best_before
    run_best = _row(lb, run_best_name, metric, n)

    def beats(a, b, key):
        if a is None or b is None or a.get(key) is None or b.get(key) is None or a[key] != a[key] or b[key] != b[key]:
            return None
        return {"beats": bool(a[key] > b[key]), "delta": float(a[key] - b[key]),
                "delta_natural": (a[f"{key}_natural"] - b[f"{key}_natural"])
                if a.get(f"{key}_natural") is not None and b.get(f"{key}_natural") is not None else None}

    return jsonable({
        "model": model, "predict_model": new["predict_model"], "refit": refit,
        "base_models": base_models, "weights": weights, "n_selected": sum(1 for w in weights.values() if w > 0),
        "fit_seconds": fit_seconds, "created_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "eval_metric": metric.name, "greater_is_better": int(getattr(metric, "_sign", 1)) > 0, "n_holdout": n,
        "validation": "out-of-fold predictions (bagged run)" if trainer.bagged_mode else "the tuning split AutoGluon held out of training",
        "scores": new,
        "best_chosen_single": best_chosen, "best_single": best_single, "run_best": run_best,
        "vs_best_single": {"val": beats(new, best_single, "score_val"), "holdout": beats(new, best_single, "score_test")},
        "vs_run_best": {"val": beats(new, run_best, "score_val"), "holdout": beats(new, run_best, "score_test")},
        "model_best_unchanged": best_before,
    })


def main() -> None:
    run_dir = Path(sys.argv[1])
    req = json.loads(Path(sys.argv[2]).read_text())
    cfg = json.loads((run_dir / "config.json").read_text())
    try:
        out = {"ok": True, "result": fit(cfg, req["models"], req["suffix"])}
    except Exception as e:  # noqa: BLE001
        out = {"ok": False, "error": f"{type(e).__name__}: {e}"}
    sys.stdout.write("\n" + json.dumps(out) + "\n")
    sys.stdout.flush()


if __name__ == "__main__":
    main()
