"""Check a run body (what `POST /api/runs` takes) before it trains, and say what it will do.

`validate(body, dataset, unlabeled, history)` returns
* `errors` / `warnings`: `{path, message}`; any error means the run would be refused (or would fail in AutoGluon).
  Every `fit_kwargs` key is checked against the keywords the installed `TabularPredictor.fit` accepts.
* `resolved`: the exact `TabularPredictor(**predictor_kwargs).fit(train_data, **fit_kwargs)` the worker will run,
  the post-fit steps (`fit_pseudolabel`, `distill`), and `call`, the same as Python source.
* `diff`: every argument with its value, the default it replaces, and where it came from (you, a preset, ENSEMBLE).
* `effective`: what AutoGluon will decide from those arguments for this data: bag folds k, bag sets s, stack levels
  L, dynamic stacking, holdout fraction, refit (AutoGluon's own `get_validation_and_stacking_method`).
* `estimate`: the fit-time estimate T̂ = c₀ + Σ_f v_f · r_f · n · d · k · s · (L + 1), capped by the budget τ,
  with each family's rate r_f (seconds per row·feature per fold model) measured from finished runs on this machine
  (median), or a prior when there is no history.
"""

from __future__ import annotations

import copy
import difflib
import json
import math
from statistics import median
from typing import Any

from . import pycode, schema

RESERVED_FIT = {"train_data": "set by the run (the dataset minus its holdout)",
                "callbacks": "ENSEMBLE adds its progress callback; other callbacks cannot be sent as JSON",
                "verbosity": None}
RESERVED_PREDICTOR = {"label": "use the run's `label` field", "path": "the run's own folder"}
VALID_PREDICTOR_KWARGS = {"problem_type", "eval_metric", "sample_weight", "weight_evaluation", "groups", "positive_class",
                          "quantile_levels", "verbosity", "log_to_file", "log_file_path", "learner_type", "learner_kwargs"}

# Seconds per (row · feature) for one model fit (one fold model when bagged), before any history exists.
PRIOR_RATE = {"GBM": 2e-5, "CAT": 5e-5, "XGB": 2e-5, "RF": 1e-5, "XT": 5e-6, "KNN": 1e-6, "LR": 5e-6,
              "NN_TORCH": 2e-4, "FASTAI": 1.5e-4, "TFIDF_LR": 1e-5, "TFIDF_SVM": 1e-5}
OVERHEAD_S = 3.0


def _err(out: list, path: str, message: str) -> None:
    out.append({"path": path, "message": message})


def infer_problem_type(body: dict[str, Any], ds: dict[str, Any], label: str) -> str | None:
    pt = body.get("problem_type") or "auto"
    if pt != "auto":
        return pt
    profile = ds["profile"]
    col = next((c for c in profile["columns"] if c["name"] == label), None)
    if col is None:
        return None
    if label == profile.get("target") and profile.get("task") in ("binary", "multiclass", "regression"):
        return profile["task"]
    if col["type"] == "number" and col["unique"] > 50:
        return "regression"
    return "binary" if col["unique"] == 2 else "multiclass"


def _families_in(hp: Any, problem_type: str | None) -> dict[str, int]:
    """Family key → number of model configs it will fit (variants restricted to other problem types skipped)."""
    if isinstance(hp, str):
        from autogluon.tabular.configs.hyperparameter_configs import get_hyperparameter_config

        try:
            hp = get_hyperparameter_config(hp)
        except Exception:  # noqa: BLE001
            return {}
    if not isinstance(hp, dict):
        return {}
    out = {}
    for k, v in hp.items():
        variants = v if isinstance(v, list) else [v]
        n = 0
        for c in variants:
            pts = ((c or {}).get("ag_args") or {}).get("problem_types") if isinstance(c, dict) else None
            if pts and problem_type and problem_type not in pts:
                continue
            n += 1
        if n:
            out[str(k)] = n
    return out


def rates_from_history(history: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """Per family: median seconds per (row · feature · fold model) over the finished runs' leaderboards."""
    samples: dict[str, list[float]] = {}
    for rec in history:
        res = rec.get("result") or {}
        data = res.get("data") or {}
        n = data.get("train_rows")
        d = len(data.get("features") or []) or None
        if not n or not d:
            continue
        for r in res.get("leaderboard") or []:
            fam, t = r.get("family"), r.get("fit_time")
            if not fam or not isinstance(t, (int, float)) or t <= 0 or str(r.get("model", "")).endswith("_FULL"):
                continue
            if fam == "ENS_WEIGHTED" or r.get("custom"):
                continue
            children = r.get("num_models") or 1
            samples.setdefault(fam, []).append(t / (n * d * max(1, children)))
    return {f: {"rate": median(v), "n": len(v)} for f, v in samples.items()}


def estimate(fams: dict[str, int], n: int, d: int, k: int, s: int, L: int, tau: float | None,
             history: list[dict[str, Any]], hpo_trials: int | None) -> dict[str, Any]:
    rates = rates_from_history(history)
    rows = []
    total = OVERHEAD_S
    folds = max(1, k) * max(1, s) if k else 1
    for f, v in fams.items():
        h = rates.get(f)
        r = h["rate"] if h else PRIOR_RATE.get(f, 2e-5)
        trials = hpo_trials or 1
        t = v * r * n * max(1, d) * folds * (L + 1) * trials
        total += t
        rows.append({"family": f, "variants": v, "rate": r, "rate_source": f"median of {h['n']} fitted models" if h else "prior",
                     "seconds": t, "trials": trials})
    bound = tau is not None and total > tau
    return {"seconds": min(total, tau) if tau else total, "unbounded_seconds": total, "budget": tau, "budget_binds": bound,
            "overhead": OVERHEAD_S, "n": n, "d": d, "k": k, "s": s, "L": L, "families": rows,
            "formula": "T̂ = c₀ + Σ_f v_f · r_f · n · d · max(1, k·s) · (L + 1)",
            "note": ("The estimate exceeds the budget τ: AutoGluon will stop adding models when τ runs out, so some "
                     "families or stack levels may be skipped." if bound else
                     "Within the budget: AutoGluon should fit every configured model.")}


def _check_hp(hp: Any, errors: list, warnings: list, problem_type: str | None) -> None:
    if hp is None or isinstance(hp, str):
        if isinstance(hp, str):
            from autogluon.tabular.configs.hyperparameter_configs import hyperparameter_config_dict

            if hp not in hyperparameter_config_dict:
                _err(errors, "fit_kwargs.hyperparameters", f"Unknown hyperparameters preset '{hp}'; use one of "
                                                           f"{', '.join(sorted(hyperparameter_config_dict))} or a dict.")
        return
    if not isinstance(hp, dict):
        _err(errors, "fit_kwargs.hyperparameters", "hyperparameters must be a dict {family: {param: value}} or a preset name.")
        return
    if not hp:
        _err(errors, "fit_kwargs.hyperparameters", "No model family is switched on: switch on at least one.")
        return
    from autogluon.tabular.registry import ag_model_registry

    import ensemble_models  # noqa: F401

    known = set(ag_model_registry.keys)
    specs = {f: {p["key"]: p for p in ps} for f, ps in schema.FAMILY_PARAMS.items()}
    for fam, cfg in hp.items():
        path = f"fit_kwargs.hyperparameters.{fam}"
        if fam not in known and fam not in ("ENS_WEIGHTED",):
            close = difflib.get_close_matches(str(fam), list(known), n=1)
            _err(errors, path, f"Unknown model family '{fam}'." + (f" Did you mean '{close[0]}'?" if close else ""))
            continue
        fam_info = next((f for f in schema.schema_families_light() if f["key"] == fam), None)
        if fam_info and problem_type and fam_info.get("problem_types") and problem_type not in fam_info["problem_types"]:
            _err(warnings, path, f"{fam} does not support {problem_type}; AutoGluon will skip it.")
        for i, c in enumerate(cfg if isinstance(cfg, list) else [cfg]):
            if not isinstance(c, dict):
                _err(errors, path, "Each family takes a dict of hyperparameters (or a list of dicts, one per variant).")
                continue
            for key, val in c.items():
                spec = specs.get(fam, {}).get(key)
                if spec is None:
                    continue
                p = f"{path}{f'[{i}]' if isinstance(cfg, list) else ''}.{key}"
                if "choices" in spec:
                    if val not in spec["choices"]:
                        _err(errors, p, f"{key} must be one of {', '.join(map(str, spec['choices']))}.")
                    continue
                if isinstance(val, bool) or not isinstance(val, (int, float)):
                    if isinstance(val, dict) or isinstance(val, str):
                        continue  # a search space or a special value: AutoGluon checks it
                    _err(errors, p, f"{key} must be a number.")
                    continue
                if spec.get("integer") and float(val) != int(val):
                    _err(errors, p, f"{key} must be an integer.")
                if not (spec["min"] <= val <= spec["max"]):
                    _err(warnings, p, f"{key} = {val} is outside the usual interval [{spec['min']}, {spec['max']}].")


def validate(body: dict[str, Any], ds: dict[str, Any] | None, unlabeled: dict[str, Any] | None = None,
             history: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    errors: list[dict[str, str]] = []
    warnings: list[dict[str, str]] = []
    info: list[dict[str, str]] = []
    if ds is None:
        _err(errors, "dataset_id", f"No dataset with id '{body.get('dataset_id')}'.")
        return {"ok": False, "errors": errors, "warnings": warnings, "info": info}
    profile = ds["profile"]
    cols = {c["name"]: c for c in profile["columns"]}
    label = body.get("label") or ds.get("target") or profile.get("target")
    if not label:
        _err(errors, "label", "Say which column to predict.")
    elif label not in cols:
        _err(errors, "label", f"Column '{label}' is not in the dataset.")
    elif cols[label]["unique"] < 2:
        _err(errors, "label", f"Column '{label}' has fewer than two distinct values.")
    if errors:
        return {"ok": False, "errors": errors, "warnings": warnings, "info": info}

    problem_type = infer_problem_type(body, ds, label)
    fk = copy.deepcopy(body.get("fit_kwargs") or {})
    pk = copy.deepcopy(body.get("predictor_kwargs") or {})
    sig = schema.fit_signature()
    valid = set(sig["keys"])

    # -- keys against TabularPredictor.fit and TabularPredictor(...)
    for k in list(fk):
        if k in RESERVED_FIT and RESERVED_FIT[k]:
            _err(errors, f"fit_kwargs.{k}", f"{k} is {RESERVED_FIT[k]}.")
        elif k not in valid:
            close = difflib.get_close_matches(k, sig["public"], n=1)
            _err(errors, f"fit_kwargs.{k}", f"TabularPredictor.fit has no argument '{k}'." + (f" Did you mean '{close[0]}'?" if close else ""))
    for k in pk:
        if k in RESERVED_PREDICTOR:
            _err(errors, f"predictor_kwargs.{k}", f"predictor_kwargs.{k} is not allowed: {RESERVED_PREDICTOR[k]}.")
        elif k not in VALID_PREDICTOR_KWARGS:
            _err(errors, f"predictor_kwargs.{k}", f"TabularPredictor(...) has no argument '{k}'.")
    if "time_limit" in fk and body.get("time_limit") is not None:
        _err(warnings, "fit_kwargs.time_limit", "fit_kwargs.time_limit overrides the budget field.")
    if "presets" in fk and body.get("presets") is not None:
        _err(warnings, "fit_kwargs.presets", "fit_kwargs.presets overrides the presets field.")

    # -- metric and problem type
    metric = body.get("eval_metric") or pk.get("eval_metric")
    metrics = schema.metrics_by_problem_type()
    if metric and problem_type in metrics:
        from autogluon.core.metrics import METRICS

        if metric not in METRICS.get(problem_type, {}):
            _err(errors, "eval_metric", f"'{metric}' is not a {problem_type} metric in AutoGluon.")
    if body.get("calibrate_threshold") and problem_type != "binary":
        _err(warnings, "calibrate_threshold", "Threshold calibration applies to binary labels only; it will be skipped.")

    # -- types of the arguments the Gym edits
    def num(key: str, *, integer: bool = False, lo: float | None = None, hi: float | None = None, lo_open: bool = False):
        if key not in fk or fk[key] is None:
            return
        v = fk[key]
        if isinstance(v, bool) or not isinstance(v, (int, float)) or (integer and float(v) != int(v)):
            _err(errors, f"fit_kwargs.{key}", f"{key} must be {'an integer' if integer else 'a number'}.")
            return
        if lo is not None and (v <= lo if lo_open else v < lo):
            _err(errors, f"fit_kwargs.{key}", f"{key} must be {'>' if lo_open else '≥'} {lo}.")
        if hi is not None and v > hi:
            _err(errors, f"fit_kwargs.{key}", f"{key} must be ≤ {hi}.")

    num("num_bag_folds", integer=True, lo=0, hi=50)
    if fk.get("num_bag_folds") == 1:
        _err(errors, "fit_kwargs.num_bag_folds", "num_bag_folds = 1 is invalid in AutoGluon: use 0 (no bagging) or k ≥ 2.")
    num("num_bag_sets", integer=True, lo=1, hi=100)
    num("num_stack_levels", integer=True, lo=0, hi=5)
    num("holdout_frac", lo=0, hi=0.9, lo_open=True)
    num("infer_limit", lo=0, lo_open=True)
    num("infer_limit_batch_size", integer=True, lo=1)
    num("time_limit", lo=0, lo_open=True)
    for b in ("refit_full", "set_best_to_refit_full", "keep_only_best", "save_space", "fit_weighted_ensemble"):
        if b in fk and not isinstance(fk[b], bool) and not (b == "refit_full" and isinstance(fk[b], str)):
            _err(errors, f"fit_kwargs.{b}", f"{b} must be true or false.")
    if "dynamic_stacking" in fk and fk["dynamic_stacking"] not in (True, False, "auto"):
        _err(errors, "fit_kwargs.dynamic_stacking", "dynamic_stacking must be true, false or 'auto'.")
    gpus = fk.get("num_gpus")
    gpu = schema.gpu_info()
    if gpus not in (None, "auto", 0):
        if not isinstance(gpus, (int, float)) or isinstance(gpus, bool) or gpus < 0:
            _err(errors, "fit_kwargs.num_gpus", "num_gpus must be 'auto' or a number ≥ 0.")
        elif gpus > gpu["count"]:
            _err(errors, "fit_kwargs.num_gpus", f"num_gpus = {gpus} but this machine has {gpu['count']} CUDA device(s).")
    for key in ("excluded_model_types", "included_model_types"):
        v = fk.get(key)
        if v is not None:
            if not isinstance(v, list) or not all(isinstance(x, str) for x in v):
                _err(errors, f"fit_kwargs.{key}", f"{key} must be a list of family keys.")
    htk = fk.get("hyperparameter_tune_kwargs")
    if htk is not None:
        if isinstance(htk, str):
            if htk not in ("auto", "random", "bayes"):
                _err(errors, "fit_kwargs.hyperparameter_tune_kwargs", "Use 'auto', 'random', 'bayes' or {scheduler, searcher, num_trials}.")
        elif isinstance(htk, dict):
            if htk.get("scheduler", "local") not in schema.HPO["schedulers"]:
                _err(errors, "fit_kwargs.hyperparameter_tune_kwargs.scheduler", "Only the 'local' scheduler runs here (no Ray cluster).")
            if htk.get("searcher", "auto") not in schema.HPO["searchers"]:
                _err(errors, "fit_kwargs.hyperparameter_tune_kwargs.searcher", f"searcher must be one of {', '.join(schema.HPO['searchers'])}.")
            nt = htk.get("num_trials")
            if nt is not None and (not isinstance(nt, int) or isinstance(nt, bool) or nt < 1):
                _err(errors, "fit_kwargs.hyperparameter_tune_kwargs.num_trials", "num_trials must be an integer ≥ 1.")
            if body.get("time_limit") is None and fk.get("time_limit") is None:
                _err(warnings, "fit_kwargs.hyperparameter_tune_kwargs", "HPO without a time budget can run for a long time.")
        else:
            _err(errors, "fit_kwargs.hyperparameter_tune_kwargs", "hyperparameter_tune_kwargs must be a string or a dict.")
    _check_hp(fk.get("hyperparameters"), errors, warnings, problem_type)

    # -- columns
    sw = pk.get("sample_weight")
    if sw is not None and sw not in ("auto_weight", "balance_weight"):
        if sw not in cols:
            _err(errors, "predictor_kwargs.sample_weight", f"No column named '{sw}'.")
        elif sw == label:
            _err(errors, "predictor_kwargs.sample_weight", "The label cannot be the sample weight.")
        elif cols[sw]["type"] != "number":
            _err(errors, "predictor_kwargs.sample_weight", f"'{sw}' is not numeric.")
    drop = body.get("drop_columns")
    if drop is None:
        drop = [c["name"] for c in profile["columns"] if c["type"] == "id" and c["name"] != label]
    for c in drop:
        if c not in cols:
            _err(errors, "drop_columns", f"No column named '{c}'.")
    if label in drop:
        _err(errors, "drop_columns", "The label column can't be dropped.")
    features = [c for c in cols if c != label and c not in drop and c != sw]

    # -- post-fit steps
    pl = body.get("pseudolabel")
    if pl:
        if unlabeled is None:
            _err(errors, "pseudolabel.dataset_id", "Upload the unlabeled rows first (pseudolabel.dataset_id names no dataset).")
        else:
            ucols = {c["name"] for c in unlabeled["profile"]["columns"]}
            missing = [c for c in features if c not in ucols]
            if missing:
                _err(errors, "pseudolabel.dataset_id", f"The unlabeled data lacks {len(missing)} feature column(s): "
                                                       f"{', '.join(missing[:5])}{'…' if len(missing) > 5 else ''}.")
            if label in ucols:
                _err(warnings, "pseudolabel.dataset_id", f"The unlabeled data has a '{label}' column; it is dropped so the rows count as unlabeled.")
        if pl.get("method", "fit_pseudolabel") not in ("fit_pseudolabel", "unlabeled_data"):
            _err(errors, "pseudolabel.method", "method must be 'fit_pseudolabel' or 'unlabeled_data'.")
        if pl.get("method") == "fit_pseudolabel" and problem_type not in ("binary", "multiclass", "regression"):
            _err(errors, "pseudolabel.method", "fit_pseudolabel needs binary, multiclass or regression.")
    dist = body.get("distill")
    if dist:
        if not isinstance(dist, dict):
            _err(errors, "distill", "distill must be a dict of predictor.distill(...) arguments.")
        else:
            allowed = {"time_limit", "hyperparameters", "holdout_frac", "teacher_preds", "augment_method", "augment_args", "models_name_suffix"}
            for k in dist:
                if k not in allowed:
                    _err(errors, f"distill.{k}", f"predictor.distill has no argument '{k}'.")
            if dist.get("teacher_preds", "soft") not in ("soft", "hard", None):
                _err(errors, "distill.teacher_preds", "teacher_preds must be 'soft', 'hard' or null.")
            if dist.get("augment_method", "spunge") not in ("spunge", "munge", None):
                _err(errors, "distill.augment_method", "augment_method must be 'spunge', 'munge' or null.")

    # -- resolve exactly as the worker will
    from ..engine.worker import resolve_fit_kwargs

    text_cols = [c["name"] for c in profile["columns"] if (body.get("column_types") or {}).get(c["name"], c["type"]) == "text"
                 and c["name"] not in drop and c["name"] != label]
    add_text = bool(body.get("custom_text_models", True)) and bool(text_cols) and problem_type not in ("regression", "quantile")
    cfg = {"presets": body.get("presets", "medium_quality"), "time_limit": body.get("time_limit", 120), "fit_kwargs": fk}
    resolved_fk: dict[str, Any] = {}
    notes: list[str] = []
    merged: dict[str, Any] = {}
    if not any(e["path"].startswith("fit_kwargs.hyperparameters") for e in errors) and not any(
            e["path"].startswith("fit_kwargs.presets") for e in errors):
        try:
            resolved_fk, notes = resolve_fit_kwargs(cfg, add_text_models=add_text)
            from autogluon.common.utils.decorators import _apply_presets
            from autogluon.tabular.configs.presets_configs import tabular_presets_alias, tabular_presets_dict

            _, merged = _apply_presets(tabular_presets_dict, tabular_presets_alias,
                                       **{k: v for k, v in resolved_fk.items() if k != "callbacks"})
        except Exception as e:  # noqa: BLE001
            _err(errors, "presets", f"{type(e).__name__}: {e}")
    resolved_fk.pop("callbacks", None)
    merged.pop("callbacks", None)
    predictor_kwargs = {"label": label, "problem_type": None if (body.get("problem_type") in (None, "auto")) else body["problem_type"],
                        "eval_metric": body.get("eval_metric"), **pk}
    post_fit = []
    if pl and pl.get("method", "fit_pseudolabel") == "fit_pseudolabel":
        post_fit.append({"call": "fit_pseudolabel", "kwargs": {"pseudo_data": "<unlabeled rows>", "max_iter": int(pl.get("max_iter", 3))}})
    if dist:
        post_fit.append({"call": "distill", "kwargs": {k: v for k, v in dist.items()}})
    fit_shown = dict(resolved_fk)
    if pl and pl.get("method") == "unlabeled_data":
        fit_shown["unlabeled_data"] = "<unlabeled rows>"

    # -- diff against TabularPredictor.fit's defaults
    defaults = sig["defaults"]
    user_keys = set(fk) | ({"presets"} if body.get("presets") is not None else set()) | (
        {"time_limit"} if body.get("time_limit") is not None else set())
    diff = []
    for k, v in fit_shown.items():
        origin = "you" if k in user_keys else "ensemble"
        diff.append({"key": k, "value": v, "default": defaults.get(k), "origin": origin, "changed": v != defaults.get(k)})
    for k, v in merged.items():
        if k in fit_shown:
            continue
        diff.append({"key": k, "value": v, "default": defaults.get(k), "origin": "preset", "changed": v != defaults.get(k)})

    # -- effective validation / stacking for this data
    n_rows = int(profile.get("rows") or ds.get("rows") or 0)
    missing_label = int(cols[label].get("missing") or 0)
    holdout_share = float((ds.get("splits") or {}).get("holdout_share") or 0.2)
    n_train = max(0, int(round((n_rows - missing_label) * (1 - holdout_share))))
    eff = {"num_bag_folds": 0, "num_bag_sets": 1, "num_stack_levels": 0, "dynamic_stacking": False, "holdout_frac": None,
           "refit_full": False, "source": "fallback"}
    try:
        from autogluon.tabular.configs.pipeline_presets import get_validation_and_stacking_method

        g = {**merged, **resolved_fk}
        ds_ = g.get("dynamic_stacking")
        minority = None
        if problem_type in ("binary", "multiclass"):
            top = (cols[label].get("top") or [])
            counts = [t.get("count") for t in top if isinstance(t, dict) and t.get("count")]
            minority = int(min(counts) * (1 - holdout_share)) if counts else None
        res = get_validation_and_stacking_method(
            num_bag_folds=g.get("num_bag_folds"), num_bag_sets=g.get("num_bag_sets"),
            use_bag_holdout=None if g.get("use_bag_holdout") in (None, "auto") else g.get("use_bag_holdout"),
            holdout_frac=g.get("holdout_frac"), auto_stack=bool(g.get("auto_stack", False)),
            num_stack_levels=g.get("num_stack_levels"), dynamic_stacking=None if ds_ in (None, "auto") else ds_,
            refit_full=g.get("refit_full") if isinstance(g.get("refit_full"), bool) else None, num_train_rows=n_train,
            problem_type=problem_type or "binary",
            hpo_enabled=g.get("hyperparameter_tune_kwargs") is not None, n_samples_minority_class=minority)
        k_, s_, L_, dyn, ubh, hf, rf = res
        eff = {"num_bag_folds": int(k_ or 0), "num_bag_sets": int(s_ or 1), "num_stack_levels": int(L_ or 0),
               "dynamic_stacking": bool(dyn), "use_bag_holdout": bool(ubh), "holdout_frac": hf, "refit_full": bool(rf),
               "source": "autogluon.get_validation_and_stacking_method"}
    except Exception as e:  # noqa: BLE001
        k_ = resolved_fk.get("num_bag_folds") or 0
        L_ = resolved_fk.get("num_stack_levels") or 0
        eff.update({"num_bag_folds": k_, "num_stack_levels": L_, "error": f"{type(e).__name__}: {e}"})
        if L_ and not k_:
            _err(errors, "fit_kwargs.num_stack_levels", "Stacking (L ≥ 1) needs bagging: set num_bag_folds ≥ 2.")
    if fk.get("num_stack_levels") and fk.get("num_bag_folds") == 0:
        _err(errors, "fit_kwargs.num_stack_levels", "Stacking (L ≥ 1) needs bagging: set num_bag_folds ≥ 2.")
    if fk.get("dynamic_stacking") is True and not eff["num_stack_levels"]:
        _err(warnings, "fit_kwargs.dynamic_stacking", "dynamic_stacking only matters with stack levels L ≥ 1.")

    hp_eff = resolved_fk.get("hyperparameters", merged.get("hyperparameters", "default"))
    fams = _families_in(hp_eff, problem_type)
    for ex in fk.get("excluded_model_types") or []:
        fams.pop(ex, None)
    trials = None
    if isinstance(htk, dict):
        trials = htk.get("num_trials")
    elif isinstance(htk, str):
        trials = 10
    tau = resolved_fk.get("time_limit")
    est = estimate(fams, n_train, len(features), eff["num_bag_folds"], eff["num_bag_sets"], eff["num_stack_levels"],
                   tau, history or [], trials)
    if tau and est["budget_binds"]:
        _err(warnings, "time_limit", f"Estimated {est['unbounded_seconds']:.0f} s of fitting for a {tau:.0f} s budget: "
                                     "AutoGluon will stop early and may skip models or stack levels.")
    if n_train < 200 and eff["num_bag_folds"] >= 5:
        _err(info, "fit_kwargs.num_bag_folds", f"Only about {n_train} training rows: each of the k = {eff['num_bag_folds']} folds validates on ~{n_train // max(1, eff['num_bag_folds'])} rows.")

    call = ("from autogluon.tabular import TabularPredictor\n\n"
            + "predictor = " + pycode.call("TabularPredictor", [], {k: v for k, v in predictor_kwargs.items() if v is not None} | {"path": "…"})
            + "\npredictor.fit(\n    train_data,\n"
            + "".join(f"    {k}={pycode.py(v, 4)},\n" for k, v in fit_shown.items()) + ")")
    for p in post_fit:
        call += "\n" + pycode.call(f"predictor.{p['call']}", [], p["kwargs"])
    return {
        "ok": not errors, "errors": errors, "warnings": warnings, "info": info,
        "problem_type": problem_type, "label": label,
        "data": {"rows": n_rows, "train_rows": n_train, "features": features, "d": len(features), "dropped": drop,
                 "text_columns": text_cols, "holdout_share": holdout_share},
        "resolved": {"predictor_kwargs": predictor_kwargs, "fit_kwargs": fit_shown, "post_fit": post_fit, "notes": notes,
                     "preset_expansion": merged},
        "call": call, "diff": diff, "effective": eff, "estimate": est, "families": fams,
    }


def json_ok(v: Any) -> bool:
    try:
        json.dumps(v)
        return True
    except (TypeError, ValueError):
        return False


__all__ = ["validate", "infer_problem_type", "estimate", "rates_from_history", "math"]
