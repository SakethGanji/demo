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
    r = lb[lb["model"] == model].iloc[0].to_dict()
    return {
        "model": model,
        "stack_level": int(r.get("stack_level") or 1),
        "score_val": r.get("score_val"), "score_test": r.get("score_test"),
        "score_val_natural": _natural(metric, r.get("score_val")),
        "score_test_natural": _natural(metric, r.get("score_test")),
        "fit_time": r.get("fit_time"), "pred_time_test": r.get("pred_time_test"),
        "pred_time_test_per_row": (r.get("pred_time_test") / n_holdout) if r.get("pred_time_test") is not None and n_holdout else None,
    }


def fit(cfg: dict[str, Any], base_models: list[str], suffix: str) -> dict[str, Any]:
    from autogluon.tabular import TabularPredictor

    import ensemble_models  # noqa: F401 — the TF-IDF models must be importable to load the predictor

    from ..splits import load_splits
    from .jsonsafe import jsonable
    from .serving import holdout_frame

    t0 = time.time()
    predictor = TabularPredictor.load(cfg["predictor_path"], verbosity=0, require_py_version_match=False)
    names = set(predictor.model_names())
    unknown = [m for m in base_models if m not in names]
    if unknown:
        raise ValueError(f"No model named {', '.join(map(repr, unknown))} in this run.")
    best_before = predictor.model_best
    created = predictor.fit_weighted_ensemble(base_models=base_models, name_suffix=suffix)
    if not created:
        raise RuntimeError("AutoGluon could not fit a weighted ensemble on these models.")
    model = created[-1]
    if predictor.model_best != best_before:
        predictor.set_model_best(best_before, save_trainer=True)
    fit_seconds = time.time() - t0

    trainer = predictor._trainer
    m = trainer.load_model(model)
    weights = {k: float(v) for k, v in m._get_model_weights().items()}
    weights = dict(sorted(weights.items(), key=lambda kv: -kv[1]))

    holdout = holdout_frame(Path(cfg["csv_path"]), load_splits(Path(cfg["splits_path"])), cfg["label"], cfg["drop_columns"])
    lb = predictor.leaderboard(holdout, silent=True)
    metric = predictor.eval_metric
    n = len(holdout)
    new = _row(lb, model, metric, n)
    is_ens = lb["model"].str.startswith("WeightedEnsemble")  # AutoGluon names every weighted ensemble so
    singles = lb[~is_ens & lb["model"].isin(base_models)]
    all_singles = lb[~is_ens]
    # The best single model by validation score (what you'd pick without ensembling), among the chosen ones and overall.
    best_chosen = _row(lb, singles.sort_values("score_val", ascending=False).iloc[0]["model"], metric, n) if len(singles) else None
    best_single = _row(lb, all_singles.sort_values("score_val", ascending=False).iloc[0]["model"], metric, n) if len(all_singles) else None
    run_best = _row(lb, best_before, metric, n)

    def beats(a, b, key):
        if a is None or b is None or a.get(key) is None or b.get(key) is None:
            return None
        return {"beats": bool(a[key] > b[key]), "delta": float(a[key] - b[key]),
                "delta_natural": (a[f"{key}_natural"] - b[f"{key}_natural"])
                if a.get(f"{key}_natural") is not None and b.get(f"{key}_natural") is not None else None}

    return jsonable({
        "model": model, "base_models": base_models, "weights": weights, "n_selected": sum(1 for w in weights.values() if w > 0),
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
