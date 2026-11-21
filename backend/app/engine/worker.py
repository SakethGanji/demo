"""One AutoGluon fit, in its own process:  python -m app.engine.worker <run_dir>

Reads <run_dir>/config.json (written by the API), fits a TabularPredictor into <run_dir>/predictor, and writes
progress as run events and the final result into SQLite. The API process only starts, watches and cancels it.

The training frame is data.csv minus the fixed holdout rows (splits.json), minus rows with no label, minus the
dropped columns (ID columns by default). The holdout is scored at the end. `fit_kwargs` are passed to
`TabularPredictor.fit` unchanged; the only additions are documented in `resolve_fit_kwargs`.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import sys
import time
import traceback
from pathlib import Path
from typing import Any

os.environ.setdefault("MLFLOW_DISABLE_AGENT_HINT", "1")

import numpy as np
import pandas as pd

from ..db import Database
from ..splits import holdout_mask
from .jobs import watch_parent
from .jsonsafe import jsonable
from .store import RunStore

log = logging.getLogger("ensemble.worker")

CUSTOM_TEXT_KEYS = ("TFIDF_LR", "TFIDF_SVM")


# --------------------------------------------------------------------------- data


GROUP_FOLD_COLUMN = "__ensemble_group_fold"


def load_frames(cfg: dict[str, Any]) -> tuple[pd.DataFrame, pd.DataFrame, dict[str, Any], pd.DataFrame | None]:
    """(train, holdout, info, tuning). Row positions of data.csv are kept as the index of the frames.

    F6: with a group / time / time + group split, rows of the tuning slice (fold -3) form `tuning` (given to
    fit(tuning_data=...)) and rows excluded by the split (fold -2) are in no frame. Group splits add their
    group-disjoint fold id as GROUP_FOLD_COLUMN to `train`, for TabularPredictor(groups=...)."""
    from .. import frames

    h = frames.ReadHash()  # DL3 (M1): hash exactly the bytes this fit reads; P05: per Parquet part → table_sha256
    df = frames.read(frames.resolve(cfg), hash_into=h)  # P03: the typed reader, engine_v1 (text stays text)
    data_sha256 = h.hexdigest()
    pin = cfg.get("split_pin") or {}
    split_read: dict[str, Any] | None = None
    if pin.get("blob_sha256"):  # P09: the pinned Parquet split, read (and hashed) from its blob, mapped by __row_id
        splits, split_read = _pinned_split(cfg, pin, df)
        splits_sha = split_read["split_sha256"]
    else:
        split_raw = Path(cfg["splits_path"]).read_bytes()  # DL4 (M-d): hash exactly the split this fit uses
        splits = json.loads(split_raw)
        splits_sha = hashlib.sha256(split_raw).hexdigest()
    label = cfg["label"]
    fold = np.asarray(splits["fold"], dtype=int)
    is_holdout = holdout_mask(splits, len(df))
    structured = bool(splits.get("params"))
    is_tuning = (fold == -3) if structured else np.zeros(len(df), dtype=bool)
    is_excluded = (fold == -2) if structured else np.zeros(len(df), dtype=bool)
    is_train = ~is_holdout & ~is_tuning & ~is_excluded
    has_label = df[label].notna().to_numpy()
    drop = [c for c in cfg.get("drop_columns") or [] if c in df.columns and c != label]
    train = df.loc[is_train & has_label].drop(columns=drop)
    holdout = df.loc[is_holdout & has_label].drop(columns=drop)
    tuning = df.loc[is_tuning & has_label].drop(columns=drop) if is_tuning.any() else None
    info = {
        "rows": len(df), "train_rows": len(train), "holdout_rows": len(holdout),
        "dropped_missing_label": int((~has_label).sum()),
        "dropped_missing_label_train": int((~is_holdout & ~has_label).sum()),
        "dropped_missing_label_holdout": int((is_holdout & ~has_label).sum()),
        "dropped_columns": drop, "features": [c for c in train.columns if c != label], "label": label,
        "data_sha256": data_sha256, "splits_sha256": splits_sha, **({"split_read": split_read} if split_read else {}),
        "read": {"kind": h.kind, "mode": "engine_v1", **({"table_sha256": data_sha256, "parts": len(h.parts),
                                                         "parts_differing": h.bad_parts} if h.kind == "parquet" else {})},
    }
    if structured:
        info["tuning_rows"] = 0 if tuning is None else len(tuning)
        info["excluded_rows"] = int(is_excluded.sum())
        if splits.get("strategy") in ("group", "time-group"):
            train[GROUP_FOLD_COLUMN] = fold[is_train & has_label]  # by position (a Parquet index is __row_id)
            info["group_folds"] = {"column": GROUP_FOLD_COLUMN, "group_column": splits["params"]["group_column"],
                                   "rows": [int((train[GROUP_FOLD_COLUMN] == k).sum()) for k in range(splits["n_folds"])]}
    if cfg.get("split"):  # F4: the (label, strategy) split this run uses
        info["split"] = {k: v for k, v in cfg["split"].items() if k != "path"}
    return train, holdout, info, tuning


def apply_column_types(cfg: dict[str, Any], train: pd.DataFrame, holdout: pd.DataFrame,
                       tuning: pd.DataFrame | None = None) -> dict[str, Any] | None:
    """Column types chosen in the wizard (`column_types`: number | category | text) become AutoGluon's
    `feature_metadata`: the columns are cast (number → float, category/text → str) in both frames and the
    inferred FeatureMetadata is overridden for them (text → special type "text"). Returns the metadata as JSON
    (type_map_raw, type_group_map_special), or None when no types were given."""
    from autogluon.common.features.feature_metadata import FeatureMetadata

    types = {c: t for c, t in (cfg.get("column_types") or {}).items() if c in train.columns and c != cfg["label"]}
    if not types:
        return None
    for df in (train, holdout) if tuning is None else (train, holdout, tuning):
        for col, t in types.items():
            if t == "number":
                df[col] = pd.to_numeric(df[col], errors="coerce")
            else:
                df[col] = df[col].map(lambda v: v if pd.isna(v) else str(v)).astype(object)
    inferred = FeatureMetadata.from_df(train.drop(columns=[cfg["label"], GROUP_FOLD_COLUMN], errors="ignore"))
    special = {k: [f for f in v if f not in types] for k, v in inferred.type_group_map_special.items()}
    special = {k: v for k, v in special.items() if v}
    text = [c for c, t in types.items() if t == "text"]
    if text:
        special["text"] = special.get("text", []) + text
    return {"type_map_raw": dict(inferred.type_map_raw), "type_group_map_special": special}


def feature_metadata_object(fm: Any):
    """A FeatureMetadata from its JSON form ({type_map_raw, type_group_map_special}); anything else unchanged."""
    if isinstance(fm, dict) and "type_map_raw" in fm:
        from autogluon.common.features.feature_metadata import FeatureMetadata

        return FeatureMetadata(type_map_raw=fm["type_map_raw"], type_group_map_special=fm.get("type_group_map_special") or {})
    return fm


def load_unlabeled(cfg: dict[str, Any], train: pd.DataFrame) -> pd.DataFrame:
    """Unlabeled rows for pseudo-labelling: the training frame's feature columns only (the label is dropped, so
    AutoGluon treats every row as unlabeled), with the same column types."""
    from .. import frames

    u = frames.read(frames.from_json(cfg.get("unlabeled_ref")) or frames.LegacyPath(cfg["unlabeled_csv_path"]))
    feats = [c for c in train.columns if c not in (cfg["label"], GROUP_FOLD_COLUMN)]
    missing = [c for c in feats if c not in u.columns]
    if missing:
        raise ValueError(f"the unlabeled data lacks the feature column(s) {', '.join(missing)}")
    u = u[feats].copy()
    for col, t in (cfg.get("column_types") or {}).items():
        if col in u.columns:
            u[col] = pd.to_numeric(u[col], errors="coerce") if t == "number" else u[col].map(lambda v: v if pd.isna(v) else str(v)).astype(object)
    return u


# --------------------------------------------------------------------------- kwargs


def resolve_fit_kwargs(cfg: dict[str, Any], *, add_text_models: bool) -> tuple[dict[str, Any], list[str]]:
    """The exact kwargs given to `TabularPredictor.fit` (minus our progress callback) and notes on what was added.

    1. `presets` and `time_limit` from the request, then every key of `fit_kwargs` on top (fit_kwargs wins).
    2. If the data has a text column and `custom_text_models` is on: TFIDF_LR and TFIDF_SVM are added to the
       hyperparameters the presets would have used (or to the given ones), and raw text is kept for them
       (`_feature_generator_kwargs.enable_raw_text_features`). Skipped if `feature_generator` is given.
    3. F11: binary runs with the scorecard on (`scorecard` = its hyperparameters): SCORECARD is added at stack
       level 1 of those hyperparameters (it is never a stacker).
    3b. F8: with `monotone` constraints, GBM / CAT / XGB in those hyperparameters become GBM_MONO / CAT_MONO /
       XGB_MONO with the constraints as their `monotone` hyperparameter; other families are left out
       (`compliant_only`, the default) or kept and reported.
    4. Our progress callback is appended to the callbacks the presets would have used, so preset callbacks
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

    if (cfg.get("monotone") or {}).get("compliant_only"):
        add_text_models = False  # F8: the TF-IDF models cannot honour a monotone constraint
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
                notes.append(f"added {', '.join(added)} for the text column(s) and kept raw text for them")
            if any(k in hp for k in CUSTOM_TEXT_KEYS):
                # The TF-IDF models read the raw text, also when the request names them itself.
                fg = dict(kw.get("_feature_generator_kwargs") or merged.get("_feature_generator_kwargs") or {})
                if "enable_raw_text_features" not in fg:
                    fg["enable_raw_text_features"] = True
                    kw["_feature_generator_kwargs"] = fg
                    if not added:
                        notes.append("kept raw text for the TF-IDF models")
    card = cfg.get("scorecard")
    if isinstance(card, dict):  # F11: the scorecard challenger, at level 1 of whatever the run trains
        from .scorecard import add_to_hyperparameters

        hp = kw.get("hyperparameters", merged.get("hyperparameters", "default"))
        if isinstance(hp, str):
            hp = get_hyperparameter_config(hp)
        if isinstance(hp, dict):
            hp, added = add_to_hyperparameters(dict(hp), card)
            if added:
                kw["hyperparameters"] = hp
                notes.append(f"added SCORECARD (ensemble_models.scorecard) as the interpretable benchmark: binning "
                             f"(≤ {card['max_bins']} bins, ≥ {card['min_bin_share']:.0%} of rows each, monotone event rate) "
                             f"→ weight of evidence → logistic regression → points (PDO {card['pdo']:g}, "
                             f"{card['base_score']:g} at odds {card['base_odds']:g}:1); bins are fitted on each fold's "
                             "training rows only")
    mono = cfg.get("monotone")
    if mono:  # F8: GBM / CAT / XGB → their monotone subclasses, carrying the constraints
        from ensemble_models.monotone import translate_hyperparameters

        hp = kw.get("hyperparameters", merged.get("hyperparameters", "default"))
        if isinstance(hp, str):
            hp = get_hyperparameter_config(hp)
        hp, kept, other = translate_hyperparameters(dict(hp), mono["constraints"], compliant_only=mono["compliant_only"],
                                                    stack=mono.get("stack", True))
        kw["hyperparameters"] = hp
        cons = ", ".join(f"{c} {'↑' if s > 0 else '↓'}" for c, s in mono["constraints"].items())
        notes.append(f"monotone constraints ({cons}): {', '.join(kept) or 'no family'} trained as "
                     f"{', '.join(k + '_MONO' for k in kept) or '—'} (ensemble_models.monotone), which map each "
                     "constraint onto the columns AutoGluon's pipeline gives the model and constrain stack features "
                     "(base-model predictions) to +1, so stack levels stay monotone")
        if any("SCORECARD" in (sub or {}) for sub in ([hp] if "SCORECARD" in hp else hp.values()) if isinstance(sub, dict)):
            notes.append("monotone: SCORECARD bins each constrained column with a monotone event rate and keeps its "
                         "coefficient ≤ 0, so its points (and probability) are monotone by construction")
        if other:
            notes.append((f"monotone: left out {', '.join(other)} (they cannot honour a monotone constraint)")
                         if mono["compliant_only"] else
                         (f"monotone: {', '.join(other)} cannot honour the constraints and are kept: the served model "
                          "is monotone only if the ensemble does not use them (checked after the fit)"))
    if "callbacks" not in kw and merged.get("callbacks"):
        kw["callbacks"] = list(merged["callbacks"])
        notes.append("callbacks from the presets passed explicitly, so the progress callback can be added")
    return kw, notes


def prepare_fit(cfg: dict[str, Any], train: pd.DataFrame, holdout: pd.DataFrame, tuning: pd.DataFrame | None,
                info: dict[str, Any]) -> dict[str, Any]:
    """Everything `TabularPredictor(...).fit(train, ...)` gets for this run, worked out once: the run's worker uses it,
    and so does every refit of the same config on other rows (F12's learning curve), so both make the same call.

    Applies the wizard's column types to the frames (in place). Returns {predictor_kwargs, call_kwargs (without the
    progress callback; tuning_data / unlabeled_data included), scorer (F7, or None), post_fit, unlabeled, resolved}."""
    feature_metadata = apply_column_types(cfg, train, holdout, tuning)

    add_text = bool(cfg.get("custom_text_models", True)) and bool(cfg.get("text_columns")) \
        and cfg.get("task") != "regression" and cfg.get("problem_type") not in ("regression", "quantile")
    fit_kwargs, notes = resolve_fit_kwargs(cfg, add_text_models=add_text)
    if feature_metadata is not None and "feature_metadata" not in fit_kwargs:
        fit_kwargs["feature_metadata"] = feature_metadata
        notes.append("column types from the wizard passed as feature_metadata "
                     f"({', '.join(f'{c}: {t}' for c, t in cfg['column_types'].items())})")
    predictor_kwargs = {"label": cfg["label"], "problem_type": cfg.get("problem_type"),
                        "eval_metric": cfg.get("eval_metric"), **(cfg.get("predictor_kwargs") or {})}
    qcfg = cfg.get("quantile")
    if qcfg:  # F9: quantile regression with the run's levels; conformal recalibration is AutoGluon's calibrate
        predictor_kwargs["quantile_levels"] = qcfg["levels"]
        if qcfg.get("calibrate") is not None and "calibrate" not in fit_kwargs:
            fit_kwargs["calibrate"] = qcfg["calibrate"]
        notes.append(f"quantile regression at τ = {', '.join(f'{t:g}' for t in qcfg['levels'])} (pinball loss); "
                     + ("conformal recalibration (CQR): AutoGluon's fit(calibrate=True)" if fit_kwargs.get("calibrate") is True
                        else "no conformal recalibration (calibrate=False)" if fit_kwargs.get("calibrate") is False
                        else "conformal recalibration left to AutoGluon (calibrate='auto': on with ≥ 1,000 validation rows)"))
    scorer = None
    if cfg.get("cost"):  # F7: the business cost is the eval_metric (an AutoGluon make_scorer)
        scorer, cost_note = cost_scorer(cfg, train)
        notes.append(cost_note)
    data_args: dict[str, Any] = {}
    if tuning is not None:  # F6: the split's tuning slice (group-disjoint, or the latest training period)
        fit_kwargs["use_bag_holdout"] = True
        data_args["tuning_data"] = {"rows": len(tuning), "split": cfg["split"]["key"]}
        notes.append(f"tuning_data = the split's tuning slice ({len(tuning)} rows"
                     + (f", {cfg['split'].get('tuning_cutoff')} onwards" if cfg["split"].get("tuning_cutoff") else "")
                     + "); use_bag_holdout=True so bagged fits use it for the ensemble too")
    if GROUP_FOLD_COLUMN in train.columns:
        predictor_kwargs["groups"] = GROUP_FOLD_COLUMN
        notes.append(f"groups='{GROUP_FOLD_COLUMN}': the split's {cfg['split']['n_folds']} folds, disjoint in "
                     f"'{info['group_folds']['group_column']}', are AutoGluon's bagging folds (LeaveOneGroupOut)")
    resolved: dict[str, Any] = {"predictor_kwargs": predictor_kwargs, "fit_kwargs": fit_kwargs, "notes": notes,
                                "path": cfg["predictor_path"]}
    if data_args:
        resolved["data_args"] = data_args

    call_kwargs = dict(fit_kwargs)
    if "feature_metadata" in call_kwargs:
        call_kwargs["feature_metadata"] = feature_metadata_object(call_kwargs["feature_metadata"])
    unlabeled = load_unlabeled(cfg, train) if cfg.get("pseudolabel") else None
    pl_method = (cfg.get("pseudolabel") or {}).get("method", "fit_pseudolabel")
    post_fit = []
    if unlabeled is not None:
        if pl_method == "unlabeled_data":
            call_kwargs["unlabeled_data"] = unlabeled
            resolved["data_args"] = {**resolved.get("data_args", {}),
                                     "unlabeled_data": {"dataset_id": cfg["pseudolabel"].get("dataset_id"), "rows": len(unlabeled)}}
        else:
            post_fit.append({"call": "fit_pseudolabel",
                             "kwargs": {"pseudo_data": f"<{len(unlabeled)} unlabeled rows>",
                                        "max_iter": int(cfg["pseudolabel"].get("max_iter", 3))}})
    if cfg.get("distill"):
        dk = dict(cfg["distill"])
        dk.setdefault("time_limit", max(30.0, 0.5 * float(cfg.get("time_limit") or 120)))
        post_fit.append({"call": "distill", "kwargs": dk})
    if post_fit or "data_args" in resolved:
        resolved["post_fit"] = post_fit
    if tuning is not None:
        call_kwargs["tuning_data"] = tuning
    if scorer is not None:
        resolved["cost_metric"] = {**cfg["cost"], "scorer": costs_describe(cfg["cost"]),
                                   "needs_proba": bool(scorer.needs_proba), "needs_class": bool(scorer.needs_class)}
    return {"predictor_kwargs": predictor_kwargs, "call_kwargs": call_kwargs, "scorer": scorer, "post_fit": post_fit,
            "unlabeled": unlabeled, "resolved": resolved}


def fit_predictor(cfg: dict[str, Any], prep: dict[str, Any], train: pd.DataFrame, path: str, *,
                  callbacks: list[Any] | None = None, emit=None):
    """TabularPredictor(path, **predictor_kwargs).fit(train, **call_kwargs), then the run's post-fit steps
    (fit_pseudolabel, distill). Returns (predictor, fit seconds)."""
    from autogluon.tabular import TabularPredictor

    emit = emit or (lambda *a, **k: None)
    call_kwargs = dict(prep["call_kwargs"])
    call_kwargs["callbacks"] = list(call_kwargs.get("callbacks") or []) + list(callbacks or [])
    if not call_kwargs["callbacks"]:
        call_kwargs.pop("callbacks")
    scorer = prep["scorer"]
    pk = prep["predictor_kwargs"]
    predictor = TabularPredictor(path=path, **({**pk, "eval_metric": scorer} if scorer is not None else pk))
    t0 = time.time()
    predictor.fit(train, **call_kwargs)
    for step in prep["post_fit"]:
        emit("post_fit", step=step["call"], status="started")
        s0 = time.time()
        if step["call"] == "fit_pseudolabel":
            predictor.fit_pseudolabel(pseudo_data=prep["unlabeled"], max_iter=step["kwargs"]["max_iter"])
        elif step["call"] == "distill":
            np.random.seed(0)  # distill's data augmentation draws from the global generator; seeded so it reproduces
            predictor.distill(**step["kwargs"])
        emit("post_fit", step=step["call"], status="finished", seconds=time.time() - s0,
             models=list(predictor.model_names()), best_model=predictor.model_best)
    return predictor, time.time() - t0


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
    em = predictor.eval_metric
    for k, v in scores.items():
        try:
            m = em if k == em.name else get_metric(k, predictor.problem_type)
            natural[k] = float(m.convert_score_to_original(v))
        except Exception:  # noqa: BLE001
            natural[k] = v
    if em.name == "expected_cost" and predictor.problem_type == "binary":
        # F7: AutoGluon scores the cost metric by the Bayes rule p > t*; the decisions the predictor serves use its
        # decision threshold. Report the served decisions' cost as the metric, and keep the Bayes-rule value.
        from ensemble_models.cost import binary_expected_cost_labels

        kw = em._kwargs
        y = (holdout[label] == predictor.positive_class).to_numpy().astype(int)
        yhat = (predictor.predict(holdout) == predictor.positive_class).to_numpy().astype(int)
        natural["expected_cost_bayes"] = natural.get("expected_cost")
        natural["expected_cost"] = binary_expected_cost_labels(y, yhat, **kw)
        scores = {**scores, "expected_cost_bayes": scores.get("expected_cost"), "expected_cost": -natural["expected_cost"]}
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
    holdout_prediction_frame(predictor, holdout, label).to_csv(path, index=False)


def holdout_prediction_frame(predictor, holdout: pd.DataFrame, label: str) -> pd.DataFrame:
    """data.csv row, y_true, y_pred; classification adds p_<class> per class; quantile (F9) adds q_<τ> per level and
    y_pred is the centre level (τ = 0.5, or the closest level), so residual views read it like a regression."""
    out = pd.DataFrame({"row": holdout.index, "y_true": holdout[label].to_numpy()})
    if predictor.problem_type == "quantile":
        from . import quantile as qt

        centre, cols = qt.holdout_columns(predictor, holdout)
        out["y_pred"] = centre.to_numpy()
        for k, v in cols.items():
            out[k] = v
        return out
    out["y_pred"] = predictor.predict(holdout).to_numpy()
    if predictor.problem_type in ("binary", "multiclass"):
        proba = predictor.predict_proba(holdout)
        for c in proba.columns:
            out[f"p_{c}"] = proba[c].to_numpy()
    return out


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
        # A child whose estimator is not in memory (refit_full under some presets keeps it None) is skipped.
        children = [c for c in children if hasattr(c, "top_words") and getattr(c, "model", None) is not None]
        if not children:
            continue
        agg: dict[str, dict[str, float]] = {}
        for c in children:
            try:
                tw = c.top_words(k * 4, classes)
            except Exception:  # noqa: BLE001 — top words are an explanation, never a reason to fail the run
                continue
            for cls, words in tw.items():
                d = agg.setdefault(cls, {})
                for w, v in words:
                    d[w] = d.get(w, 0.0) + v / len(children)
        out[name] = {cls: [[w, round(v, 6)] for w, v in sorted(d.items(), key=lambda kv: -kv[1])[:k]]
                     for cls, d in agg.items()}
    return out


def compute_feature_importance(predictor, holdout: pd.DataFrame, time_limit: float = 60) -> list[dict[str, Any]]:
    from .serving import feature_importance

    return feature_importance(predictor, holdout, time_limit=time_limit)


def cost_scorer(cfg: dict[str, Any], train: pd.DataFrame):
    """F7: the run's cost as an AutoGluon scorer (module-level functions in ensemble_models.cost, so it pickles into
    bagged workers and the saved predictor). Multiclass matrices are put in AutoGluon's class order: the categories
    of the training label, sorted, which is how its label cleaner numbers them."""
    from ensemble_models import cost as c

    cost = cfg["cost"]
    if cost["kind"] == "binary":
        return (c.binary_cost_scorer(cost["c_fp"], cost["c_fn"], cost["g_tp"]),
                f"eval_metric = expected cost per decision (c_FP = {cost['c_fp']:g}, c_FN = {cost['c_fn']:g}, "
                f"g_TP = {cost['g_tp']:g}), each model deciding by the Bayes rule p > t* = {cost['bayes_threshold']:.4f}: "
                "models, early stopping and ensemble weights are chosen on it")
    from .explain import _as_str

    classes = [_as_str(v) for v in train[cfg["label"]].dropna().astype("category").cat.categories]
    return (c.multiclass_cost_scorer(c.reorder_matrix(cost, classes)),
            f"eval_metric = expected cost per decision of the predicted labels under the {len(classes)} × {len(classes)} "
            "cost matrix: models, early stopping and ensemble weights are chosen on it")


def costs_describe(cost: dict[str, Any]) -> str:
    from .costs import describe

    return describe(cost)


def _decide_threshold(cfg: dict[str, Any], predictor, train: pd.DataFrame, emit) -> tuple[str, str | None, float | None]:
    """Binary runs: calibrate the decision threshold after the fit when the run asks for it (`calibrate_threshold`:
    a metric name, or "auto" = F1 when the minority class is under 20% of the training rows). A user's own
    `fit_kwargs.calibrate_decision_threshold` always wins: then AutoGluon did (or skipped) it inside fit()."""
    if predictor.problem_type != "binary":
        return "none", None, None
    from . import threshold as thr

    user = "calibrate_decision_threshold" in (cfg.get("fit_kwargs") or {})
    want = cfg.get("calibrate_threshold")
    if want == "auto":
        share = thr.minority_share(train[cfg["label"]])
        want = "f1" if share < 0.2 else None
        if want:
            emit("log", level="info", logger="ensemble",
                 message=f"minority class is {share:.1%} of the training rows: calibrating the decision threshold for F1")
    if want and not user:
        emit("calibrating", metric=want)
        t0 = time.time()
        try:
            if want == "expected_cost" and cfg.get("cost", {}).get("kind") == "binary":
                from ensemble_models.cost import binary_cost_class_scorer

                cc = cfg["cost"]
                t = float(predictor.calibrate_decision_threshold(
                    metric=binary_cost_class_scorer(cc["c_fp"], cc["c_fn"], cc["g_tp"]), verbose=False))
            else:
                t = thr.calibrate(predictor, want)
            predictor.set_decision_threshold(t)
            predictor.save()
        except Exception as e:  # noqa: BLE001 — never fail a finished fit over the threshold
            emit("log", level="warning", logger="ensemble",
                 message=f"threshold calibration for {want} failed ({type(e).__name__}: {e}); keeping t = 0.5")
            return "default", None, None
        return "calibrated", want, time.time() - t0
    if abs(predictor.decision_threshold - 0.5) > 1e-12:
        # AutoGluon calibrated inside fit() (its default "auto" does for metrics such as f1 and balanced_accuracy).
        return ("fit_kwargs" if user else "autogluon_auto"), predictor.eval_metric.name, None
    return "default", None, None



def _pinned_split(cfg: dict[str, Any], pin: dict[str, Any], df: pd.DataFrame) -> tuple[dict[str, Any], dict[str, Any]]:
    """P09: the split this run pinned at queue time, read from its content-addressed blob (not from the view file): its
    split_sha256 recomputed from the bytes read, its folds joined onto the frame's rows by __row_id. The positional view
    at cfg.splits_path is rewritten from it, so every later reader of the run sees the verified split."""
    from .. import frames
    from ..lake import manifest as LM
    from ..lake import splitstore as SS

    db = Database(Path(cfg["db_path"]))
    lake = frames._store()
    key = SS._blob_key(db, pin["blob_sha256"])
    if not key or not lake.exists(key):
        raise DataIntegrityError(f"split_integrity: the split blob this run pinned ({pin['blob_sha256'][:12]}…) is missing from the lake.")
    with lake.open(key) as fh:
        bsha = LM.file_sha256(fh)[0]
    ssha, n = LM.split_sha256_parquet(lake.local_copy(key))
    ids, folds = SS.read_blob(lake, key)
    rid = np.asarray(df.index, dtype="int64")
    if len(rid) != len(ids) or not np.array_equal(np.sort(rid), ids):
        raise DataIntegrityError(f"split_integrity: the pinned split covers {len(ids):,} rows, the table read has {len(rid):,}; "
                                 "they are not the same rows.")
    pos = np.searchsorted(ids, rid)
    row = SS.get_by_id(db, pin["split_id"]) or {**{k: None for k in ("params_json",)}, "id": pin["split_id"],
                                                 "seed": 42, "target": cfg.get("label"), "strategy": "random-stratified",
                                                 "split_key": pin.get("split_key"), "holdout_share": "0.2", "n_folds": 5,
                                                 "created_at": None, "split_sha256": ssha, "blob_sha256": pin["blob_sha256"],
                                                 "table_name": pin.get("table")}
    sp = SS.view({**row, "split_sha256": ssha, "blob_sha256": pin["blob_sha256"]}, folds[pos])
    if cfg.get("splits_path"):
        SS.write_view(Path(cfg["splits_path"]), sp)
    return sp, {"split_id": pin["split_id"], "split_sha256": ssha, "blob_sha256": bsha, "blob_ok": bsha == pin["blob_sha256"],
                "rows": int(n), "pinned_split_sha256": pin.get("split_sha256")}


class DataIntegrityError(RuntimeError):
    """DL3 (M1): the bytes on disk are not the version's: the fit is refused."""


def record_run_input(cfg: dict[str, Any], store: RunStore, run_id: str, sha: str | None, ok: bool | None,
                     info: dict[str, Any] | None = None) -> None:
    """P05: what this run read, as one insert-only `run_inputs` row (role 'train'): the table and its committed
    table_sha256 with the sha recomputed from the bytes read (Parquet), or the data.csv sha (CSV-era, legacy_csv_sha256);
    the split file's hash; the read mode; rows and dropped columns. Written once per run (a re-run keeps the first)."""
    import secrets as _secrets

    from ..catalog.schema import now as _now

    it = cfg.get("input_table") or {}
    parquet = bool(it.get("table_sha256"))
    try:
        with store.db.connect() as con:
            if con.execute("SELECT 1 FROM run_inputs WHERE run_id = ? AND role = 'train'", (run_id,)).fetchone():
                return
            pin = cfg.get("split_pin") or {}  # P09: the split this run read (the pinned blob's recomputed split_sha256)
            con.execute(
                "INSERT INTO run_inputs (id, run_id, role, version_id, table_name, table_sha256, read_sha256, match,"
                " legacy_csv_sha256, split_id, split_sha256, split_legacy_sha256, read_mode, rows_total, rows_used,"
                " columns_dropped, created_at)"
                " VALUES (?, ?, 'train', ?, ?, ?, ?, ?, ?, ?, ?, ?, 'engine_v1', ?, ?, ?, ?)",
                ("ri_" + _secrets.token_hex(6), run_id, cfg.get("dataset_id"), it.get("table") or "data",
                 it.get("table_sha256") if parquet else None, sha, None if ok is None else int(ok),
                 None if parquet else (cfg.get("data_sha256_expected") or sha),
                 pin.get("split_id"), (info or {}).get("splits_sha256") if pin else None,
                 None if pin else cfg.get("splits_sha256_expected"),
                 (info or {}).get("rows"), (info or {}).get("train_rows"),
                 json.dumps((info or {}).get("dropped_columns") or cfg.get("drop_columns") or []), _now()))
    except Exception as e:  # noqa: BLE001 - the record must not cost the run; the gate below still decides
        log.warning("run_inputs not recorded for %s: %s", run_id, e)


def data_gate(cfg: dict[str, Any], sha: str | None, store: RunStore, run_id: str, emit, basis: str = "the bytes this fit read",
              info: dict[str, Any] | None = None) -> None:
    """DL3 (M1): record the sha256 of the data this run trains on (result, config, the trained_on edge) and compare it with
    the sha256 stored when the version was made. On a mismatch the fit is refused (audited, and the version is flagged as
    failing its integrity check) unless an admin armed an override on the run (integrity_override_reason).
    P05: on a Parquet table the compared hash is the table's `table_sha256`, recomputed from the part bytes parsed."""
    from ..catalog.schema import audit, now as _now

    exp = cfg.get("data_sha256_expected")
    ok = None if not exp else (sha == exp)
    rec = {"data_sha256": sha, "expected": exp, "match": ok, "basis": basis, "dataset_id": cfg.get("dataset_id"),
           "version": cfg.get("data_version_label")}
    it = cfg.get("input_table") or {}
    if it.get("table_sha256"):
        rec.update(storage="parquet", table=it.get("table"), table_sha256=it["table_sha256"])
    record_run_input(cfg, store, run_id, sha, ok, info)
    store.merge_result(run_id, data_sha256=sha, data_integrity=rec)
    with store.db.connect() as con:
        con.execute("UPDATE runs SET config = json_set(config, '$.data_sha256', ?) WHERE id = ?", (sha, run_id))
        con.execute("UPDATE lineage_edges SET meta = json_set(COALESCE(meta, '{}'), '$.data_sha256', ?, '$.data_sha256_match', json(?))"
                    " WHERE relation = 'trained_on' AND dst_kind = 'run' AND dst_id = ?", (sha, "null" if ok is None else ("true" if ok else "false"), run_id))
    emit("data_hashed", **rec)
    if ok is not False:
        return
    ds = cfg.get("dataset_id")
    label = cfg.get("data_version_label") or ds
    ov = cfg.get("data_integrity_override") or {}
    with store.db.connect() as con:
        root = (con.execute("SELECT COALESCE(root_id, id) FROM datasets WHERE id = ?", (ds,)).fetchone() or [ds])[0]
        con.execute("UPDATE datasets SET verified_at = ?, verified_ok = 0 WHERE id = ?", (_now(), ds))
        audit(con, "system:train-time hash", "dataset_integrity_failed", run_id=run_id,
              details={"dataset_id": ds, "root_id": root, "run_id": run_id, "stored": exp, "read": sha, "basis": basis,
                       "remedy": "create a new version from this file (POST /api/datasets/{id}/adopt-disk-file), or restore the file"})
        if ov.get("by"):
            audit(con, ov["by"], "training_integrity_override", comment=str(ov.get("reason") or "")[:1000], run_id=run_id,
                  details={"dataset_id": ds, "root_id": root, "run_id": run_id, "stored": exp, "read": sha, "at": "fit start"})
        else:
            audit(con, cfg.get("by") or "unknown", "training_refused", run_id=run_id,
                  comment=f"data on disk differs from {label}",
                  details={"dataset_id": ds, "root_id": root, "run_id": run_id, "stored": exp, "read": sha, "status": 409})
    if ov.get("by"):
        emit("log", level="warning", logger="ensemble",
             message=f"data on disk differs from {label} (stored {exp[:12]}…, read {sha[:12]}…): training anyway on {ov['by']}'s override")
        return
    raise DataIntegrityError(
        f"data on disk differs from version {label}: its sha256 was {exp[:12]}… when the version was made, the file read now "
        f"is {sha[:12]}…. The file was changed outside the app, so this run was refused. Create a new version from this file "
        "(Data → the version → Create a new version from this file), or restore the file; an admin may train anyway with "
        "integrity_override_reason (audited).")

def splits_gate(cfg: dict[str, Any], sha: str | None, store: RunStore, run_id: str, emit) -> None:
    """DL4 (M-d): the split file (which rows are held out, the folds) is checked like data.csv: its sha256 is recorded on the
    run (splits_sha256, next to data_sha256) and compared with the hash stored when the split was drawn. A mismatch refuses
    the fit (audited; the version is flagged) unless an admin armed an override."""
    from ..catalog.schema import audit, now as _now

    pin = cfg.get("split_pin") or {}
    chk = None
    if pin.get("split_id"):  # P09: the five checks again at fit, plus "the bytes read are the pinned split"
        from .. import frames
        from ..lake import splitstore as SS

        row = SS.get_by_id(store.db, pin["split_id"])
        exp = pin.get("split_sha256")
        if row is None:
            chk = {"ok": False, "failed": ["row"], "message": f"split_integrity: split {pin['split_id']} is no longer in the splits table"}
        else:
            chk = SS.integrity(store.db, frames._store(), row, exclude_run=run_id)
        ok = bool(chk["ok"]) and sha == exp
        if chk["ok"] and sha != exp:
            chk["message"] = f"split_integrity: the split read ({str(sha)[:12]}…) is not the one pinned at queue time ({str(exp)[:12]}…)"
        rec = {"splits_sha256": sha, "expected": exp, "match": ok, "split_id": pin["split_id"], "storage": "parquet",
               "checks": [{k: c.get(k) for k in ("n", "check", "ok", "detail")} for c in (chk.get("checks") or [])],
               "failed": chk.get("failed")}
        ov0 = cfg.get("data_integrity_override") or {}
        if not ok and ov0.get("by"):
            rec["override"] = {"by": ov0["by"]}
    else:
        exp = cfg.get("splits_sha256_expected")
        ok = None if not exp else (sha == exp)
        rec = {"splits_sha256": sha, "expected": exp, "match": ok, "file": cfg.get("splits_file")}
        if ok is False and (cfg.get("data_integrity_override") or {}).get("by"):
            rec["override"] = {"by": cfg["data_integrity_override"]["by"]}
    store.merge_result(run_id, splits_sha256=sha, splits_integrity=rec)
    with store.db.connect() as con:
        con.execute("UPDATE runs SET config = json_set(config, '$.splits_sha256', ?) WHERE id = ?", (sha, run_id))
        con.execute("UPDATE lineage_edges SET meta = json_set(COALESCE(meta, '{}'), '$.splits_sha256', ?, '$.splits_sha256_match', json(?))"
                    " WHERE relation = 'trained_on' AND dst_kind = 'run' AND dst_id = ?", (sha, "null" if ok is None else ("true" if ok else "false"), run_id))
    emit("splits_hashed", **rec)
    if ok is not False:
        return
    ds = cfg.get("dataset_id")
    label = cfg.get("data_version_label") or ds
    ov = cfg.get("data_integrity_override") or {}
    with store.db.connect() as con:
        root = (con.execute("SELECT COALESCE(root_id, id) FROM datasets WHERE id = ?", (ds,)).fetchone() or [ds])[0]
        con.execute("UPDATE datasets SET verified_at = ?, verified_ok = 0 WHERE id = ?", (_now(), ds))
        audit(con, "system:train-time hash", "dataset_integrity_failed", run_id=run_id,
              details={"dataset_id": ds, "root_id": root, "run_id": run_id, "file": cfg.get("splits_file"), "stored": exp, "read": sha,
                       "what": "split file", "remedy": "restore the split as drawn (POST /api/datasets/{id}/restore-splits)"})
        if ov.get("by"):
            audit(con, ov["by"], "training_integrity_override", comment=str(ov.get("reason") or "")[:1000], run_id=run_id,
                  details={"dataset_id": ds, "root_id": root, "run_id": run_id, "what": "split file", "stored": exp, "read": sha, "at": "fit start"})
        else:
            audit(con, cfg.get("by") or "unknown", "training_refused", run_id=run_id, comment=f"split file of {label} differs",
                  details={"dataset_id": ds, "root_id": root, "run_id": run_id, "what": "split file", "stored": exp, "read": sha, "status": 409})
    if ov.get("by"):
        emit("log", level="warning", logger="ensemble",
             message=f"the split file of {label} differs from when it was drawn: training anyway on {ov['by']}'s override")
        return
    if chk is not None:
        raise DataIntegrityError(str(chk.get("message") or "split_integrity") + ". The run was refused at fit: GET /api/datasets/"
                                 f"{ds}/verify names the check; POST …/restore-splits offers the split the earliest run used; an "
                                 "admin may train anyway with integrity_override_reason (audited).")
    raise DataIntegrityError(
        f"the split file of version {label} ({cfg.get('splits_file')}: which rows are held out, and the folds) differs from when it "
        f"was drawn: sha256 {exp[:12]}… then, {sha[:12]}… now. It was changed outside the app, so this run was refused: its "
        "holdout would not be the one other runs on this version use. Restore it (Data → the version → Restore the split, or "
        "POST /api/datasets/{id}/restore-splits); an admin may train anyway with integrity_override_reason (audited).")


# --------------------------------------------------------------------------- main


def run(run_dir: Path) -> int:
    cfg = json.loads((run_dir / "config.json").read_text())
    run_id = cfg["run_id"]
    store = RunStore(Database(Path(cfg["db_path"])))

    def emit(type_: str, /, **data: Any) -> int:
        return store.emit(run_id, type_, **data)

    started = time.time()
    if cfg.get("kind") in ("forecast", "anomaly", "expected_loss", "segmented", "survival", "reject_inference", "multilabel"):
        return run_other(cfg, run_dir, store, emit, started)
    try:
        emit("run_started", pid=os.getpid())
        train, holdout, info, tuning = load_frames(cfg)
        data_gate(cfg, info.get("data_sha256"), store, run_id, emit, info=info)  # DL3 (M1): before anything is fit
        splits_gate(cfg, info.get("splits_sha256"), store, run_id, emit)  # DL4 (M-d): the holdout and folds too
        emit("data_ready", **info)
        store.merge_result(run_id, data=info)

        from autogluon.tabular import TabularPredictor

        import ensemble_models  # noqa: F401 — registers TFIDF_LR / TFIDF_SVM

        from .progress import EventLogHandler, ProgressCallback

        ag_logger = logging.getLogger("autogluon")
        ag_logger.addHandler(EventLogHandler(emit))

        if cfg.get("import"):
            predictor, fit_seconds = import_predictor(cfg, emit, store, run_id)
            progress = ProgressCallback(emit)
        else:
            prep = prepare_fit(cfg, train, holdout, tuning, info)
            resolved = prep["resolved"]
            store.merge_result(run_id, resolved=resolved)
            emit("config_resolved", **resolved)

            progress = ProgressCallback(emit)
            predictor, fit_seconds = fit_predictor(cfg, prep, train, cfg["predictor_path"], callbacks=[progress], emit=emit)

        threshold_source, threshold_metric, threshold_seconds = _decide_threshold(cfg, predictor, train, emit)

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
            "quantile_levels": [float(t) for t in predictor.quantile_levels] if predictor.problem_type == "quantile" else None,
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
        if predictor.problem_type == "binary":
            from . import threshold as thr
            from .explain import load_holdout

            rec = thr.record(predictor, load_holdout(run_dir), source=threshold_source, metric=threshold_metric,
                             seconds=threshold_seconds)
            store.merge_result(run_id, threshold=rec)
            emit("threshold", **rec)

        if predictor.problem_type == "quantile":  # F9: pinball per level, coverage raw / calibrated, fan chart
            emit("explaining", what="quantile")
            try:
                from . import quantile as qt

                qr = qt.report(predictor, holdout, cfg["label"])
                store.merge_result(run_id, quantile=qr)
                emit("quantile", levels=qr["levels"], pinball=qr["holdout"]["pinball"], served=qr["served"],
                     intervals=[{k: iv[k] for k in ("lo", "hi", "nominal", "coverage", "mean_width")} for iv in qr["holdout"]["intervals"]],
                     calibrated=[{k: iv[k] for k in ("lo", "hi", "nominal", "coverage")} for iv in (qr["calibrated"] or {}).get("intervals", [])],
                     raw=[{k: iv[k] for k in ("lo", "hi", "nominal", "coverage")} for iv in (qr["raw"] or {}).get("intervals", [])])
            except Exception as e:  # noqa: BLE001 — reported, not fatal
                emit("log", level="warning", logger="ensemble", message=f"quantile report failed: {type(e).__name__}: {e}")
                store.merge_result(run_id, quantile={"error": f"{type(e).__name__}: {e}"})

        if cfg.get("monotone"):  # F8: where the constraints landed, and ICE / PD of every model on raw holdout rows
            emit("explaining", what="monotone")
            try:
                from . import monotone as mono

                mr = mono.report(predictor, holdout, cfg["monotone"])
                store.merge_result(run_id, monotone=mr)
                emit("monotone", best_model=mr["best_model"], best_monotone=mr["best_monotone"],
                     columns={c: {k: v for k, v in x.items() if k not in ("grid", "pd", "ice", "ice_rows")}
                              for c, x in mr["served"].items()},
                     unconstrained_models=mr["unconstrained_models"])
            except Exception as e:  # noqa: BLE001 — reported, not fatal: GET /runs/{id}/monotone recomputes it
                emit("log", level="warning", logger="ensemble", message=f"monotone check failed: {type(e).__name__}: {e}")
                store.merge_result(run_id, monotone={**cfg["monotone"], "error": f"{type(e).__name__}: {e}"})

        if cfg.get("scorecard") and predictor.problem_type == "binary":  # F11: table, IV, ΔAUC vs the served model
            emit("explaining", what="scorecard")
            try:
                from . import scorecard as sc

                sr = sc.report(predictor, train, holdout, cfg["label"], cfg, run_dir)
                store.merge_result(run_id, scorecard=sr)
                if sr.get("available"):
                    h = sr["holdout"]
                    emit("scorecard", model=sr["model"], features=sr["table"]["n_features"], auc=h["scorecard"]["auc"],
                         served_auc=h["served"]["auc"], delta_auc=h["delta_auc"]["delta"], ci=h["delta_auc"]["ci"])
            except Exception as e:  # noqa: BLE001 — reported, not fatal
                emit("log", level="warning", logger="ensemble", message=f"scorecard report failed: {type(e).__name__}: {e}")
                store.merge_result(run_id, scorecard={"available": False, "error": f"{type(e).__name__}: {e}"})

        if predictor.problem_type in ("binary", "multiclass", "regression") and cfg.get("ladder", True):
            emit("explaining", what="ladder")  # D2: baseline → scorecard → single GBM → stack, one holdout, one rule
            try:
                from . import ladder as lad

                lr = lad.compute(predictor, train, holdout, cfg["label"], cfg)
                store.merge_result(run_id, ladder=lr)
                emit("ladder", recommended=lr.get("recommended"), sentence=lr.get("sentence"), level=lr["level"],
                     rungs=[{k: r.get(k) for k in ("rung", "name", "model", "score", "within", "latency_ms_p95", "size_bytes")}
                            for r in lr["rungs"]])
            except Exception as e:  # noqa: BLE001 — reported, not fatal: GET /runs/{id}/ladder recomputes it
                emit("log", level="warning", logger="ensemble", message=f"model ladder failed: {type(e).__name__}: {e}")
                store.merge_result(run_id, ladder={"error": f"{type(e).__name__}: {e}"})

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
        try:  # MLflow tracking: parameters, metrics and light artifacts (S5); never fails the run
            from ..registry import tracking

            mlr = tracking.ensure_logged(Path(cfg["db_path"]).parent, store, run_id, run_dir)
            # The MLflow run id goes out as `mlflow_run_id`: `run_id` names the ENSEMBLE run in every event.
            emit("tracked", **{("mlflow_run_id" if k == "run_id" else k): v for k, v in mlr.items()})
        except Exception as e:  # noqa: BLE001
            emit("log", level="warning", logger="ensemble", message=f"MLflow logging failed: {e}")
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


def import_predictor(cfg: dict[str, Any], emit, store: RunStore, run_id: str):
    """An imported TabularPredictor (already unpacked into the run's predictor folder by the API): load and check it.
    Nothing is refit; the run is scored on the chosen dataset's holdout like a trained one."""
    from autogluon.tabular import TabularPredictor

    emit("importing", path=cfg["predictor_path"])
    t0 = time.time()
    p = TabularPredictor.load(cfg["predictor_path"], require_version_match=False, require_py_version_match=False,
                              verbosity=0)
    info = p.info()
    fit_seconds = float(info.get("time_fit_training") or 0) or None
    imp = dict(cfg.get("import") or {})
    imp.update({"autogluon_version": info.get("version"), "load_seconds": time.time() - t0,
                "n_models": len(p.model_names()), "label": p.label, "problem_type": p.problem_type,
                "eval_metric": p.eval_metric.name, "features": list(p.original_features)})
    fk = {k: v for k, v in (getattr(p._learner, "_fit_kwargs", None) or {}).items() if isinstance(v, (int, float, str, bool, type(None)))} \
        if hasattr(p, "_learner") else {}
    resolved = {"predictor_kwargs": {"label": p.label, "problem_type": p.problem_type, "eval_metric": p.eval_metric.name},
                "fit_kwargs": fk, "path": cfg["predictor_path"],
                "notes": [f"imported from {imp.get('source_kind')}: {imp.get('source')}", "not refit: the predictor as it was saved",
                          "the holdout rows of this dataset may have been in the imported predictor's training data; "
                          "if so, holdout scores are optimistic"]}
    store.merge_result(run_id, resolved=resolved, imported=imp)
    emit("config_resolved", **resolved)
    emit("fit_started", problem_type=p.problem_type, eval_metric=p.eval_metric.name, imported=True)
    for name in p.model_names():
        lvl = p._trainer.get_model_attribute(name, "level")
        val = p._trainer.get_model_attribute(name, "val_score")
        emit("model_finished", model=name, stack_level=lvl, val_score=val, val_score_natural=_natural(p.eval_metric, val),
             fit_time=p._trainer.get_model_attribute(name, "fit_time"), imported=True)
    return p, fit_seconds


def run_other(cfg: dict[str, Any], run_dir: Path, store: RunStore, emit, started: float) -> int:
    """Forecast, anomaly and expected-loss runs: the same process, events and terminal states as a tabular fit."""
    run_id = cfg["run_id"]

    def merge(**parts: Any) -> None:
        store.merge_result(run_id, **parts)

    try:
        emit("run_started", pid=os.getpid(), kind=cfg["kind"])
        if (cfg.get("input_table") or {}).get("table_sha256"):  # P05: a Parquet table, its parts hashed at the start
            from .. import frames

            sha, _, _ = frames.hash_table(frames.from_json(cfg["input_ref"]))
            data_gate(cfg, sha, store, run_id, emit, basis="the table's files at the start of the run")
        elif cfg.get("csv_path") and Path(cfg["csv_path"]).exists():  # DL3 (M1): the file this run reads, hashed at its start
            from ..catalog.schema import file_sha256

            data_gate(cfg, file_sha256(Path(cfg["csv_path"])), store, run_id, emit, basis="the file at the start of the run")
        if cfg["kind"] == "forecast":
            from ..forecast import core
        elif cfg["kind"] == "expected_loss":  # G3: PD, LGD and EAD TabularPredictors chained
            from ..el import core
        elif cfg["kind"] == "segmented":  # G5: a global TabularPredictor and one per segment, routed
            from ..segmented import core
        elif cfg["kind"] == "survival":  # G6: Cox PH and a random survival forest (scikit-survival)
            from ..survival import core
        elif cfg["kind"] == "reject_inference":  # G7: approved-only against three reject-inference methods (AutoGluon)
            from ..reject import core
        elif cfg["kind"] == "multilabel":  # G8: one binary TabularPredictor per label, each with its own threshold
            from ..multilabel import core
        else:
            from ..anomaly import core
        out = core.run(cfg, run_dir, emit, merge)
        merge(elapsed_seconds=time.time() - started)
        try:
            from ..registry import tracking

            mlr = tracking.ensure_logged(Path(cfg["db_path"]).parent, store, run_id, run_dir)
            emit("tracked", **{("mlflow_run_id" if k == "run_id" else k): v for k, v in mlr.items()})
        except Exception as e:  # noqa: BLE001
            emit("log", level="warning", logger="ensemble", message=f"MLflow logging failed: {e}")
        if store.finish(run_id, "succeeded"):
            emit("run_succeeded", elapsed_seconds=time.time() - started, **out)
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
        watch_parent(int(sys.argv[2]), anchor=len(sys.argv) > 3 and sys.argv[3] == "anchor")
    sys.exit(run(run_dir))


if __name__ == "__main__":
    main()
