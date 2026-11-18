"""One AutoGluon fit, in its own process:  python -m app.engine.worker <run_dir>

Reads <run_dir>/config.json (written by the API), fits a TabularPredictor into <run_dir>/predictor, and writes
progress as run events and the final result into SQLite. The API process only starts, watches and cancels it.

The training frame is data.csv minus the fixed holdout rows (splits.json), minus rows with no label, minus the
dropped columns (ID columns by default). The holdout is scored at the end. `fit_kwargs` are passed to
`TabularPredictor.fit` unchanged; the only additions are documented in `resolve_fit_kwargs`.
"""

from __future__ import annotations

import json
import logging
import os
import sys
import threading
import time
import traceback
from pathlib import Path
from typing import Any

os.environ.setdefault("MLFLOW_DISABLE_AGENT_HINT", "1")

import numpy as np
import pandas as pd

from ..db import Database
from .jsonsafe import jsonable
from .store import RunStore, now

log = logging.getLogger("ensemble.worker")

CUSTOM_TEXT_KEYS = ("TFIDF_LR", "TFIDF_SVM")


# --------------------------------------------------------------------------- data


def load_frames(cfg: dict[str, Any]) -> tuple[pd.DataFrame, pd.DataFrame, dict[str, Any]]:
    """(train, holdout, info). Row positions of data.csv are kept as the index of both frames."""
    df = pd.read_csv(cfg["csv_path"])
    splits = json.loads(Path(cfg["splits_path"]).read_text())
    label = cfg["label"]
    holdout_rows = np.asarray(splits["holdout"], dtype=int)
    is_holdout = np.zeros(len(df), dtype=bool)
    is_holdout[holdout_rows] = True
    has_label = df[label].notna().to_numpy()
    drop = [c for c in cfg.get("drop_columns") or [] if c in df.columns and c != label]
    train = df.loc[~is_holdout & has_label].drop(columns=drop)
    holdout = df.loc[is_holdout & has_label].drop(columns=drop)
    info = {
        "rows": len(df), "train_rows": len(train), "holdout_rows": len(holdout),
        "dropped_missing_label": int((~has_label).sum()),
        "dropped_missing_label_train": int((~is_holdout & ~has_label).sum()),
        "dropped_missing_label_holdout": int((is_holdout & ~has_label).sum()),
        "dropped_columns": drop, "features": [c for c in train.columns if c != label], "label": label,
    }
    return train, holdout, info


# --------------------------------------------------------------------------- kwargs


def resolve_fit_kwargs(cfg: dict[str, Any], *, add_text_models: bool) -> tuple[dict[str, Any], list[str]]:
    """The exact kwargs given to `TabularPredictor.fit` (minus our progress callback) and notes on what was added.

    1. `presets` and `time_limit` from the request, then every key of `fit_kwargs` on top (fit_kwargs wins).
    2. If the data has a text column and `custom_text_models` is on: TFIDF_LR and TFIDF_SVM are added to the
       hyperparameters the presets would have used (or to the given ones), and raw text is kept for them
       (`_feature_generator_kwargs.enable_raw_text_features`). Skipped if `feature_generator` is given.
    3. Our progress callback is appended to the callbacks the presets would have used, so preset callbacks
       (for example early stopping) still run.
    """
    from autogluon.common.utils.decorators import _apply_presets
    from autogluon.tabular.configs.hyperparameter_configs import get_hyperparameter_config
    from autogluon.tabular.configs.presets_configs import tabular_presets_alias, tabular_presets_dict

    kw: dict[str, Any] = {}
    if cfg.get("presets") is not None:
        kw["presets"] = cfg["presets"]
    if cfg.get("time_limit") is not None:
        kw["time_limit"] = cfg["time_limit"]
    kw.update(cfg.get("fit_kwargs") or {})
    notes: list[str] = []

    _, merged = _apply_presets(tabular_presets_dict, tabular_presets_alias, **dict(kw))

    if add_text_models and "feature_generator" not in kw:
        hp = kw.get("hyperparameters", merged.get("hyperparameters", "default"))
        if isinstance(hp, str):
            hp = get_hyperparameter_config(hp)
        if isinstance(hp, dict):
            hp = dict(hp)
            added = [k for k in CUSTOM_TEXT_KEYS if k not in hp]
            for k in added:
                hp[k] = {}
            if added:
                kw["hyperparameters"] = hp
                fg = dict(kw.get("_feature_generator_kwargs") or merged.get("_feature_generator_kwargs") or {})
                fg.setdefault("enable_raw_text_features", True)
                kw["_feature_generator_kwargs"] = fg
                notes.append(f"added {', '.join(added)} for the text column(s) and kept raw text for them")
    if "callbacks" not in kw and merged.get("callbacks"):
        kw["callbacks"] = list(merged["callbacks"])
        notes.append("callbacks from the presets passed explicitly, so the progress callback can be added")
    return kw, notes


# --------------------------------------------------------------------------- results


def metric_info(metric) -> dict[str, Any]:
    """The metric in its natural sign. AutoGluon scores are 'higher is better' (errors are negated); the
    `*_natural` fields undo that, and `greater_is_better` says how to read them."""
    sign = int(getattr(metric, "_sign", 1))
    return {"name": metric.name, "greater_is_better": sign > 0,
            "optimum": _natural(metric, metric.optimum) if sign < 0 else float(metric.optimum),
            "scores_note": "score_* are AutoGluon's higher-is-better scores; *_natural are in the metric's own units"}


def _natural(metric, score):
    try:
        return None if score is None or pd.isna(score) else float(metric.convert_score_to_original(score))
    except Exception:  # noqa: BLE001
        return None


def build_leaderboard(predictor, holdout: pd.DataFrame, models_info: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    from .catalogue import family_for_class

    lb = predictor.leaderboard(holdout, extra_info=True, silent=True)
    metric = predictor.eval_metric
    rows = []
    for rank, r in enumerate(lb.to_dict(orient="records"), start=1):
        name = r["model"]
        mi = models_info.get(name, {})
        cls = r.get("child_model_type") or r.get("model_type")
        r = {k: v for k, v in r.items() if k not in ("eval_metric",)}
        r["rank"] = rank
        r["family"] = mi.get("family") or family_for_class(cls) or family_for_class(r.get("model_type"))
        r["score_val_natural"] = _natural(metric, r.get("score_val"))
        r["score_test_natural"] = _natural(metric, r.get("score_test"))
        r["fold_scores"] = mi.get("fold_scores")
        r["fold_scores_natural"] = mi.get("fold_scores_natural")
        r["val_score_std"] = mi.get("val_score_std")
        r["weights"] = mi.get("weights")
        rows.append(jsonable(r))
    return rows


def ensemble_weights(predictor) -> list[dict[str, Any]]:
    out = []
    trainer = predictor._trainer
    for name in predictor.model_names():
        try:
            m = trainer.load_model(name)
        except Exception:  # noqa: BLE001
            continue
        if hasattr(m, "_get_model_weights"):
            w = {k: float(v) for k, v in m._get_model_weights().items()}
            out.append({"model": name, "stack_level": trainer.get_model_attribute(name, "level"),
                        "weights": dict(sorted(w.items(), key=lambda kv: -kv[1])),
                        "best": name == predictor.model_best})
    return out


def holdout_metrics(predictor, holdout: pd.DataFrame, label: str) -> dict[str, Any]:
    from autogluon.core.metrics import get_metric

    scores = predictor.evaluate(holdout, silent=True)
    natural = {}
    for k, v in scores.items():
        try:
            natural[k] = float(get_metric(k, predictor.problem_type).convert_score_to_original(v))
        except Exception:  # noqa: BLE001
            natural[k] = v
    out: dict[str, Any] = {"n_rows": len(holdout), "model": predictor.model_best, "scores": scores,
                           "metrics": natural, "eval_metric": predictor.eval_metric.name}
    if predictor.problem_type in ("binary", "multiclass"):
        from sklearn.metrics import confusion_matrix

        classes = list(predictor.class_labels)
        y_pred = predictor.predict(holdout)
        out["classes"] = classes
        out["confusion_matrix"] = confusion_matrix(holdout[label], y_pred, labels=classes).tolist()
    return out


def save_holdout_predictions(predictor, holdout: pd.DataFrame, label: str, path: Path) -> None:
    """data.csv row, the true label, the prediction and (classification) one probability column per class."""
    out = pd.DataFrame({"row": holdout.index, "y_true": holdout[label].to_numpy()})
    out["y_pred"] = predictor.predict(holdout).to_numpy()
    if predictor.problem_type in ("binary", "multiclass"):
        proba = predictor.predict_proba(holdout)
        for c in proba.columns:
            out[f"p_{c}"] = proba[c].to_numpy()
    out.to_csv(path, index=False)


def text_top_words(predictor, k: int = 15) -> dict[str, Any]:
    """Top words per class for every TF-IDF model, averaged over bagged fold children."""
    out: dict[str, Any] = {}
    if predictor.problem_type not in ("binary", "multiclass"):
        return out
    trainer = predictor._trainer
    classes = list(predictor.class_labels)
    for name in predictor.model_names():
        try:
            m = trainer.load_model(name)
        except Exception:  # noqa: BLE001
            continue
        children = [m.load_child(c) for c in m.models] if hasattr(m, "load_child") else [m]
        children = [c for c in children if hasattr(c, "top_words")]
        if not children:
            continue
        agg: dict[str, dict[str, float]] = {}
        for c in children:
            for cls, words in c.top_words(k * 4, classes).items():
                d = agg.setdefault(cls, {})
                for w, v in words:
                    d[w] = d.get(w, 0.0) + v / len(children)
        out[name] = {cls: [[w, round(v, 6)] for w, v in sorted(d.items(), key=lambda kv: -kv[1])[:k]]
                     for cls, d in agg.items()}
    return out


def compute_feature_importance(predictor, holdout: pd.DataFrame, time_limit: float = 60) -> list[dict[str, Any]]:
    from .serving import feature_importance

    return feature_importance(predictor, holdout, time_limit=time_limit)


# --------------------------------------------------------------------------- main


def _watch_parent(ppid: int) -> None:
    def loop():
        while True:
            if os.getppid() != ppid:  # the API died: don't keep fitting as an orphan
                os._exit(3)
            time.sleep(1)

    threading.Thread(target=loop, daemon=True).start()


def run(run_dir: Path) -> int:
    cfg = json.loads((run_dir / "config.json").read_text())
    run_id = cfg["run_id"]
    store = RunStore(Database(Path(cfg["db_path"])))

    def emit(type_: str, **data: Any) -> int:
        return store.emit(run_id, type_, **data)

    started = time.time()
    try:
        emit("run_started", pid=os.getpid())
        train, holdout, info = load_frames(cfg)
        emit("data_ready", **info)
        store.merge_result(run_id, data=info)

        from autogluon.tabular import TabularPredictor

        import ensemble_models  # noqa: F401 — registers TFIDF_LR / TFIDF_SVM

        from .progress import EventLogHandler, ProgressCallback

        ag_logger = logging.getLogger("autogluon")
        ag_logger.addHandler(EventLogHandler(emit))

        add_text = bool(cfg.get("custom_text_models", True)) and bool(cfg.get("text_columns")) \
            and cfg.get("task") != "regression" and cfg.get("problem_type") not in ("regression", "quantile")
        fit_kwargs, notes = resolve_fit_kwargs(cfg, add_text_models=add_text)
        predictor_kwargs = {"label": cfg["label"], "problem_type": cfg.get("problem_type"),
                            "eval_metric": cfg.get("eval_metric"), **(cfg.get("predictor_kwargs") or {})}
        resolved = {"predictor_kwargs": predictor_kwargs, "fit_kwargs": fit_kwargs, "notes": notes,
                    "path": cfg["predictor_path"]}
        store.merge_result(run_id, resolved=resolved)
        emit("config_resolved", **resolved)

        progress = ProgressCallback(emit)
        call_kwargs = dict(fit_kwargs)
        call_kwargs["callbacks"] = list(call_kwargs.get("callbacks") or []) + [progress]
        predictor = TabularPredictor(path=cfg["predictor_path"], **predictor_kwargs)
        t0 = time.time()
        predictor.fit(train, **call_kwargs)
        fit_seconds = time.time() - t0

        weights = ensemble_weights(predictor)
        for e in weights:
            progress.models.setdefault(e["model"], {})["weights"] = e["weights"]
        best = predictor.model_best
        best_weights = next((e for e in weights if e["model"] == best), None)
        summary = {
            "problem_type": predictor.problem_type,
            "eval_metric": metric_info(predictor.eval_metric),
            "classes": [jsonable(c) for c in predictor.class_labels] if predictor.class_labels is not None else None,
            "positive_class": jsonable(predictor.positive_class) if predictor.problem_type == "binary" else None,
            "best_model": best, "fit_seconds": fit_seconds, "features": list(predictor.features()),
            "ensembles": weights, "ensemble": best_weights,
        }
        emit("fit_finished", best_model=best, fit_seconds=fit_seconds, n_models=len(predictor.model_names()),
             ensemble=best_weights)
        store.merge_result(run_id, **summary)

        leaderboard = build_leaderboard(predictor, holdout, progress.models)
        store.merge_result(run_id, leaderboard=leaderboard)
        emit("leaderboard", rows=[{k: r.get(k) for k in ("model", "family", "rank", "score_val", "score_test",
                                                         "score_val_natural", "score_test_natural", "fit_time",
                                                         "pred_time_val", "pred_time_test", "stack_level",
                                                         "val_score_std", "can_infer")} for r in leaderboard])

        holdout_out = holdout_metrics(predictor, holdout, cfg["label"])
        save_holdout_predictions(predictor, holdout, cfg["label"], run_dir / "holdout_predictions.csv")
        store.merge_result(run_id, holdout=holdout_out)
        emit("evaluation", **holdout_out)

        bagged = bool(predictor._trainer.bagged_mode)
        oof = {"available": bagged,
               "models": [m for m in predictor.model_names() if not m.endswith("_FULL")] if bagged else [],
               "note": None if bagged else "No bagging in this run (num_bag_folds = 0): no out-of-fold predictions."}
        store.merge_result(run_id, oof=oof)

        words = text_top_words(predictor)
        if words:
            store.merge_result(run_id, text_models=words)
            emit("text_models", models=words)

        if cfg.get("feature_importance", True):
            emit("explaining", what="feature_importance")
            try:
                fi = compute_feature_importance(predictor, holdout)
                store.merge_result(run_id, feature_importance=fi)
                emit("feature_importance", features=fi[:50])
            except Exception as e:  # noqa: BLE001 — not worth failing the run over
                emit("log", level="warning", logger="ensemble", message=f"feature importance failed: {e}")

        store.merge_result(run_id, elapsed_seconds=time.time() - started)
        if store.finish(run_id, "succeeded"):
            emit("run_succeeded", best_model=best, score_test=leaderboard[0].get("score_test") if leaderboard else None,
                 elapsed_seconds=time.time() - started)
        return 0
    except Exception as e:  # noqa: BLE001
        tb = traceback.format_exc()
        log.error("run %s failed:\n%s", run_id, tb)
        msg = f"{type(e).__name__}: {e}"
        if store.finish(run_id, "failed", error=msg):
            emit("run_failed", error=msg, traceback=tb[-4000:])
        return 1


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    run_dir = Path(sys.argv[1])
    if len(sys.argv) > 2:
        _watch_parent(int(sys.argv[2]))
    sys.exit(run(run_dir))


if __name__ == "__main__":
    main()
