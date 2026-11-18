"""An AutoGluon callback and a logging handler that turn a fit into run events.

Events written here (all carry `phase`: "main", or "dystack" for AutoGluon's dynamic-stacking sub-fit):

- fit_started      problem_type, eval_metric, greater_is_better, num_classes, level_end, k_fold, n_repeats, time_limit
- model_started    model, family, model_type, stack_level, stack_name, time_limit, bagged, order
- model_finished   model, family, stack_level, val_score, val_score_natural, fit_time, predict_time,
                   fold_scores, fold_scores_natural, val_score_std, n_children, elapsed
- model_failed     model, family, stack_level, error, skipped (true when the time limit ran out)
- ensemble         model, stack_level, weights {base model: w} (sum 1), val_score, val_score_natural
- log              level, message (AutoGluon's own log lines, capped)
"""

from __future__ import annotations

import logging
import time
from typing import Any, Callable

import numpy as np
from autogluon.core.callbacks import AbstractCallback

from .catalogue import family_for_class

Emit = Callable[..., Any]


def _model_class_name(model) -> str:
    base = getattr(model, "model_base", None)
    if base is not None:
        return base.__name__ if isinstance(base, type) else type(base).__name__
    return type(model).__name__


def _natural(trainer, score):
    try:
        return None if score is None else float(trainer.eval_metric.convert_score_to_original(score))
    except Exception:  # noqa: BLE001
        return None


class ProgressCallback(AbstractCallback):
    allow_recursive_calls = True

    def __init__(self, emit: Emit) -> None:
        super().__init__()
        self._emit = emit
        self._started: dict[str, float] = {}
        self._order = 0
        self.models: dict[str, dict[str, Any]] = {}  # what we learned per model, for the final leaderboard

    def __getstate__(self):  # never pickled into a predictor: AutoGluon may save the trainer mid-fit
        return {"_skip": False, "_emit": None, "_started": {}, "_order": 0, "models": {}}

    def emit(self, type_: str, trainer, **data) -> None:
        if self._emit is None:
            return
        phase = "dystack" if "ds_sub_fit" in str(getattr(trainer, "path", "")) else "main"
        try:
            self._emit(type_, phase=phase, **data)
        except Exception:  # noqa: BLE001 — progress must never break a fit
            logging.getLogger(__name__).exception("could not emit %s", type_)

    def before_trainer_fit(self, trainer, **kwargs):
        em = trainer.eval_metric
        self.emit("fit_started", trainer, problem_type=trainer.problem_type, eval_metric=em.name,
                  greater_is_better=int(getattr(em, "_sign", 1)) > 0,
                  num_classes=getattr(trainer, "num_classes", None), level_end=kwargs.get("level_end"),
                  k_fold=kwargs.get("k_fold", getattr(trainer, "k_fold", None)),
                  n_repeats=kwargs.get("n_repeats", getattr(trainer, "n_repeats", None)), time_limit=kwargs.get("time_limit"))

    def _before_model_fit(self, trainer, model, time_limit=None, stack_name="core", level=1):
        cls = _model_class_name(model)
        self._started[model.name] = time.time()
        self._order += 1
        self.emit("model_started", trainer, model=model.name, family=family_for_class(cls), model_type=cls,
                  stack_level=level, stack_name=stack_name, time_limit=time_limit,
                  bagged=hasattr(model, "model_base"), order=self._order)
        self.models.setdefault(model.name, {})["model_type"] = cls
        return False, False

    def _after_model_fit(self, trainer, model_names, stack_name="core", level=1):
        if not model_names:
            failed = getattr(trainer, "_models_failed_to_train_errors", {}) or {}
            # The name of the model that just failed is the most recent one started.
            name = max(self._started, key=self._started.get) if self._started else None
            err = failed.get(name, {}) if name else {}
            msg = f"{err.get('exc_type', '')}: {err.get('exc_str', '')}".strip(": ")
            skipped = False
            if not msg:
                limit, t0 = getattr(trainer, "_time_limit", None), getattr(trainer, "_time_train_start", None)
                if limit is not None and t0 is not None and limit - (time.time() - t0) < max(1.0, 0.02 * limit):
                    msg, skipped = "skipped: the time limit was reached", True
                else:
                    msg = "did not train"
            self.emit("model_failed", trainer, model=name, stack_level=level, skipped=skipped,
                      family=family_for_class(self.models.get(name, {}).get("model_type")), error=msg)
            return False
        for name in model_names:
            self._finished(trainer, name, level)
        return False

    def _finished(self, trainer, name: str, level: int) -> None:
        val = trainer.get_model_attribute(name, "val_score")
        info: dict[str, Any] = {
            "model": name, "stack_level": level, "val_score": val, "val_score_natural": _natural(trainer, val),
            "fit_time": trainer.get_model_attribute(name, "fit_time"),
            "predict_time": trainer.get_model_attribute(name, "predict_time"),
            "fold_scores": None, "fold_scores_natural": None, "val_score_std": None, "n_children": 1,
        }
        cls = self.models.get(name, {}).get("model_type")
        weights = None
        try:
            m = trainer.load_model(name)
            if hasattr(m, "_get_model_weights"):
                weights = {k: float(v) for k, v in m._get_model_weights().items()}
                cls = cls or "WeightedEnsembleModel"
            children = list(getattr(m, "models", []) or [])
            if hasattr(m, "load_child") and len(children) > 1:
                scores = [m.load_child(c).val_score for c in children]
                scores = [float(s) for s in scores if s is not None]
                if scores:
                    info["fold_scores"] = scores
                    info["fold_scores_natural"] = [_natural(trainer, s) for s in scores]
                    info["val_score_std"] = float(np.std(scores, ddof=1)) if len(scores) > 1 else 0.0
                info["n_children"] = len(children)
        except Exception:  # noqa: BLE001
            logging.getLogger(__name__).exception("could not inspect %s", name)
        info["family"] = "ENS_WEIGHTED" if weights is not None else family_for_class(cls)
        started = self._started.get(name)
        info["elapsed"] = time.time() - started if started else None
        self.models.setdefault(name, {}).update(info)
        self.emit("model_finished", trainer, **info)
        if weights is not None:
            self.models[name]["weights"] = weights
            self.emit("ensemble", trainer, model=name, stack_level=level, weights=weights,
                      val_score=info["val_score"], val_score_natural=info["val_score_natural"])


class EventLogHandler(logging.Handler):
    """Forwards AutoGluon's log lines as `log` events (capped, so a chatty fit can't flood the table)."""

    def __init__(self, emit: Emit, cap: int = 3000) -> None:
        super().__init__(level=logging.INFO)
        self._emit = emit
        self._n = 0
        self.cap = cap

    def emit(self, record: logging.LogRecord) -> None:  # noqa: A003 — logging API
        if self._n >= self.cap:
            return
        try:
            msg = record.getMessage().rstrip()
        except Exception:  # noqa: BLE001
            return
        if not msg.strip():
            return
        self._n += 1
        try:
            self._emit("log", level=record.levelname.lower(), logger=record.name, message=msg[:1000])
        except Exception:  # noqa: BLE001
            pass
