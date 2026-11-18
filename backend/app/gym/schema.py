"""What the Training Gym can configure, as data the UI renders.

* `FAMILY_PARAMS`: per model family (AutoGluon's hyperparameters key), the hyperparameters the Gym shows as sliders:
  AutoGluon's own key, a symbol that matches the family's objective in the catalogue, the interval, log or linear
  scale, integer or real, and the default AutoGluon uses (read from the model class where it sets one).
* `fit_signature()`: every keyword `TabularPredictor.fit` accepts, with its default: the named parameters of the
  signature plus the `**kwargs` AutoGluon validates itself (`_validate_fit_kwargs` / `_fit_extra_kwargs_dict`),
  collected by calling those validators on a probe, so the list follows the installed AutoGluon.
* `schema()`: the whole thing for `GET /api/gym/schema` (families with objectives, presets, metrics per problem
  type, hyperparameter search options, the GPU count, AutoGluon's default model zoo).
"""

from __future__ import annotations

import functools
import inspect
from typing import Any

from ..engine import catalogue

# The families the Gym lets you switch on or off (S6 brief), in display order.
GYM_FAMILIES = ["GBM", "CAT", "XGB", "RF", "XT", "KNN", "LR", "NN_TORCH", "FASTAI", "TFIDF_LR", "TFIDF_SVM"]


def _p(key: str, sym: str, name: str, lo: float, hi: float, default: Any, *, log: bool = False, integer: bool = False,
       help: str, choices: list[Any] | None = None) -> dict[str, Any]:  # noqa: A002
    out = {"key": key, "sym": sym, "name": name, "min": lo, "max": hi, "log": log, "integer": integer,
           "default": default, "help": help}
    if choices is not None:
        out = {"key": key, "sym": sym, "name": name, "choices": choices, "default": default, "help": help}
    return out


FAMILY_PARAMS: dict[str, list[dict[str, Any]]] = {
    "GBM": [
        _p("learning_rate", "η", "Learning rate", 1e-3, 1.0, 0.05, log=True, help="Shrinkage applied to each tree's contribution."),
        _p("num_leaves", "T", "Leaves per tree", 2, 256, 31, integer=True, log=True, help="Upper bound on Tₜ, the leaves of each leaf-wise tree."),
        _p("min_data_in_leaf", "m", "Min rows per leaf", 1, 200, 20, integer=True, log=True, help="A leaf needs at least this many training rows."),
        _p("feature_fraction", "ρ", "Feature fraction", 0.1, 1.0, 1.0, help="Share of columns sampled for each tree."),
        _p("lambda_l2", "λ", "L2 penalty", 0.0, 10.0, 0.0, help="λ in ½λ‖wₜ‖² on the leaf values."),
        _p("num_boost_round", "M", "Boosting rounds (max)", 10, 10000, 10000, integer=True, log=True, help="Upper bound on trees; AutoGluon stops early on validation."),
    ],
    "CAT": [
        _p("learning_rate", "η", "Learning rate", 1e-3, 1.0, 0.05, log=True, help="Shrinkage per symmetric tree."),
        _p("depth", "D", "Tree depth", 1, 10, 6, integer=True, help="Depth of each oblivious tree (2ᴰ leaves)."),
        _p("l2_leaf_reg", "λ", "L2 leaf regularisation", 1e-2, 100.0, 3.0, log=True, help="λ in λ‖w‖²."),
        _p("iterations", "M", "Iterations (max)", 10, 10000, 10000, integer=True, log=True, help="Upper bound on trees; early stopping on validation."),
    ],
    "XGB": [
        _p("learning_rate", "η", "Learning rate", 1e-3, 1.0, 0.1, log=True, help="Shrinkage per tree."),
        _p("max_depth", "D", "Max depth", 1, 12, 6, integer=True, help="Depth-wise trees grow to at most this depth."),
        _p("min_child_weight", "h", "Min child weight", 0.1, 20.0, 1.0, log=True, help="Minimum Hessian sum in a child."),
        _p("colsample_bytree", "ρ", "Column sample per tree", 0.1, 1.0, 1.0, help="Share of columns per tree."),
        _p("reg_lambda", "λ", "L2 penalty", 1e-3, 100.0, 1.0, log=True, help="λ in ½λ‖wₜ‖²."),
        _p("reg_alpha", "α", "L1 penalty", 0.0, 10.0, 0.0, help="α in α‖wₜ‖₁."),
        _p("n_estimators", "M", "Trees (max)", 10, 10000, 10000, integer=True, log=True, help="Upper bound on trees; early stopping on validation."),
    ],
    "RF": [
        _p("n_estimators", "B", "Trees", 10, 1000, 300, integer=True, log=True, help="B in (1/B) Σ_b T_b(x)."),
        _p("max_leaf_nodes", "L", "Max leaves per tree", 10, 50000, 15000, integer=True, log=True, help="Caps the size of each tree."),
        _p("min_samples_leaf", "m", "Min rows per leaf", 1, 50, 1, integer=True, help="A leaf needs at least this many rows."),
    ],
    "XT": [
        _p("n_estimators", "B", "Trees", 10, 1000, 300, integer=True, log=True, help="B in (1/B) Σ_b T_b(x)."),
        _p("max_leaf_nodes", "L", "Max leaves per tree", 10, 50000, 15000, integer=True, log=True, help="Caps the size of each tree."),
        _p("min_samples_leaf", "m", "Min rows per leaf", 1, 50, 1, integer=True, help="A leaf needs at least this many rows."),
    ],
    "KNN": [
        _p("n_neighbors", "k", "Neighbours", 1, 100, 5, integer=True, log=True, help="k in N_k(x)."),
        _p("weights", "w", "Weighting", 0, 0, "uniform", choices=["uniform", "distance"], help="Uniform average, or wᵢ = 1/‖x − xᵢ‖."),
    ],
    "LR": [
        _p("C", "C", "Inverse regularisation", 1e-3, 1e3, 1.0, log=True, help="C in (1/2C)‖w‖²; higher fits harder."),
        _p("penalty", "‖·‖", "Penalty", 0, 0, "L2", choices=["L2", "L1"], help="Ridge (L2) or lasso (L1) on w."),
    ],
    "NN_TORCH": [
        _p("learning_rate", "η", "Learning rate", 1e-5, 1e-1, 3e-4, log=True, help="Adam step size."),
        _p("weight_decay", "λ", "Weight decay", 1e-10, 1e-1, 1e-6, log=True, help="λ in λ‖θ‖²."),
        _p("dropout_prob", "p", "Dropout", 0.0, 0.5, 0.1, help="Probability a hidden unit is dropped while training."),
        _p("num_layers", "L", "Hidden layers", 1, 8, 4, integer=True, help="L in W_L σ(… σ(W₁x))."),
        _p("hidden_size", "h", "Units per layer", 8, 512, 128, integer=True, log=True, help="Width of each hidden layer."),
        _p("num_epochs", "E", "Epochs (max)", 5, 1000, 1000, integer=True, log=True, help="Upper bound; early stopping on validation."),
    ],
    "FASTAI": [
        _p("lr", "η", "Learning rate", 1e-4, 1e-1, 1e-2, log=True, help="Peak of the one-cycle schedule."),
        _p("ps", "p", "Dropout", 0.0, 0.5, 0.1, help="Dropout on the hidden layers."),
        _p("emb_drop", "pₑ", "Embedding dropout", 0.0, 0.5, 0.1, help="Dropout on the categorical embeddings."),
        _p("epochs", "E", "Epochs", 1, 100, 30, integer=True, help="One-cycle epochs (AutoGluon picks by data size when unset)."),
    ],
    "TFIDF_LR": [
        _p("C", "C", "Inverse regularisation", 1e-3, 1e3, 5.0, log=True, help="C in (1/2C)‖w_c‖²."),
        _p("ngram_max", "g", "Max n-gram", 1, 3, 2, integer=True, help="Terms are 1- to g-grams."),
        _p("min_df", "f", "Min document frequency", 1, 20, 2, integer=True, help="Drop terms in fewer rows than f."),
        _p("max_features", "V", "Vocabulary size", 1000, 500000, 200000, integer=True, log=True, help="Keep the V most frequent terms."),
    ],
    "TFIDF_SVM": [
        _p("C", "C", "Misclassification cost", 1e-3, 1e3, 0.5, log=True, help="C in C Σᵢ max(0, 1 − ỹ w_cᵀφ)²."),
        _p("ngram_max", "g", "Max n-gram", 1, 3, 2, integer=True, help="Terms are 1- to g-grams."),
        _p("min_df", "f", "Min document frequency", 1, 20, 2, integer=True, help="Drop terms in fewer rows than f."),
        _p("max_features", "V", "Vocabulary size", 1000, 500000, 200000, integer=True, log=True, help="Keep the V most frequent terms."),
    ],
}

PRESETS = [
    {"key": "medium_quality", "name": "Medium", "note": "Fast; no bagging. Good for a first look and for prototyping."},
    {"key": "good_quality", "name": "Good", "note": "Bagged and stacked, refit for fast inference."},
    {"key": "high_quality", "name": "High", "note": "Bagged and stacked with the zero-shot portfolio, refit; strong and still deployable."},
    {"key": "best_quality", "name": "Best", "note": "Bagged and stacked with dynamic stacking; the most accurate, slowest to predict."},
    {"key": "optimize_for_deployment", "name": "Deployment", "note": "Adds save_space and keeps only the best model."},
    {"key": "interpretable", "name": "Interpretable", "note": "Rule-based models only (needs imodels)."},
]

HPO = {
    "schedulers": ["local"],
    "searchers": ["auto", "random", "bayes"],
    "note": "hyperparameter_tune_kwargs = {scheduler, searcher, num_trials}: each family is tuned with num_trials "
            "configurations inside its share of the time budget; searcher 'bayes' uses a Gaussian-process model.",
}


def metrics_by_problem_type() -> dict[str, list[str]]:
    from autogluon.core.metrics import METRICS

    keep = {"acc", "nll", "pac", "pac_score", "mse", "mean_squared_error", "mean_absolute_error",
            "mean_absolute_percentage_error", "symmetric_mean_absolute_percentage_error", "pinball"}
    return {pt: sorted(k for k in v if k not in keep or pt == "quantile") for pt, v in METRICS.items()}


@functools.lru_cache(maxsize=1)
def fit_signature() -> dict[str, Any]:
    """Every keyword TabularPredictor.fit accepts (beyond train_data), with its default value as JSON or a repr."""
    from autogluon.tabular import TabularPredictor

    sig = inspect.signature(TabularPredictor.fit)
    named = {n: p.default for n, p in sig.parameters.items()
             if n not in ("self", "train_data") and p.kind is not inspect.Parameter.VAR_KEYWORD}
    captured: dict[str, Any] = {}

    probe = TabularPredictor.__new__(TabularPredictor)
    probe.verbosity = 2

    def fake_extra(kwargs, extra_valid_keys=None):
        captured["extra_keys"] = list(extra_valid_keys or [])
        return kwargs

    probe._validate_fit_extra_kwargs = fake_extra  # type: ignore[method-assign]
    try:
        base = TabularPredictor._validate_fit_kwargs(probe, kwargs={})
    except Exception:  # noqa: BLE001 — older/newer AutoGluon: fall back to the extra dict only
        base = {}
    extra = TabularPredictor._fit_extra_kwargs_dict(probe)
    kw_defaults = {**{k: None for k in captured.get("extra_keys", [])}, **extra, **base}
    out: dict[str, Any] = {}
    for k, v in {**kw_defaults, **named}.items():
        out[k] = v if isinstance(v, (str, int, float, bool, type(None), list, dict)) else repr(v)
    out.pop("verbosity", None)
    out["verbosity"] = 2
    return {"keys": sorted(out), "defaults": out, "named": sorted(named),
            "public": sorted(k for k in out if not k.startswith("_"))}


@functools.lru_cache(maxsize=1)
def model_defaults() -> dict[str, dict[str, Any]]:
    """The defaults each model class sets for itself (AutoGluon's `_set_default_params`), JSON-able ones only."""
    from autogluon.tabular.registry import ag_model_registry

    import ensemble_models  # noqa: F401

    out: dict[str, dict[str, Any]] = {}
    for key in GYM_FAMILIES:
        try:
            cls = ag_model_registry.key_to_cls(key)
            m = cls(path="/nonexistent/probe", name="probe", problem_type="binary", eval_metric="accuracy")
            m.params = {}
            m._set_default_params()
            out[key] = {k: v for k, v in m.params.items() if isinstance(v, (int, float, str, bool)) and v is not None}
        except Exception:  # noqa: BLE001 — e.g. CatBoost needs the trainer's eval metric
            out[key] = {}
    return out


def gpu_info() -> dict[str, Any]:
    try:
        import torch

        n = int(torch.cuda.device_count()) if torch.cuda.is_available() else 0
        names = [torch.cuda.get_device_name(i) for i in range(n)]
    except Exception:  # noqa: BLE001
        n, names = 0, []
    return {"count": n, "names": names,
            "note": None if n else "No CUDA device on this machine: num_gpus must stay 0 (AutoGluon would fail or ignore it)."}


@functools.lru_cache(maxsize=1)
def default_zoo() -> dict[str, Any]:
    from autogluon.tabular.configs.hyperparameter_configs import get_hyperparameter_config

    return get_hyperparameter_config("default")


def preset_values() -> dict[str, dict[str, Any]]:
    """What each preset expands to (AutoGluon's own preset dicts), JSON-able values only."""
    from autogluon.tabular.configs.presets_configs import tabular_presets_dict

    out = {}
    for p in PRESETS:
        d = tabular_presets_dict.get(p["key"]) or {}
        out[p["key"]] = {k: (v if isinstance(v, (str, int, float, bool, type(None), list, dict)) else repr(v)) for k, v in d.items()}
    return out


def schema() -> dict[str, Any]:
    ag_defaults = model_defaults()
    families = []
    for key in GYM_FAMILIES:
        fam = catalogue.get(key) or {"key": key, "name": key}
        params = []
        for p in FAMILY_PARAMS.get(key, []):
            d = ag_defaults.get(key, {}).get(p["key"])
            params.append({**p, "default": d if d is not None else p["default"],
                           "default_source": "autogluon" if d is not None else "library"})
        families.append({"key": key, "name": fam.get("name"), "kind": fam.get("kind"), "description": fam.get("description"),
                         "objective": fam.get("objective"), "symbols": {**catalogue._COMMON, **(fam.get("symbols") or {})},
                         "custom": bool(fam.get("custom")), "problem_types": fam.get("problem_types"), "params": params,
                         "in_default_zoo": key in default_zoo()})
    from autogluon.tabular.version import __version__ as ag_version

    return {"engine": {"name": "AutoGluon", "version": ag_version}, "families": families, "presets": PRESETS,
            "preset_values": preset_values(), "metrics": metrics_by_problem_type(), "hpo": HPO, "gpu": gpu_info(),
            "fit": fit_signature(), "default_zoo": default_zoo(),
            "calibration_metrics": ["f1", "balanced_accuracy", "mcc", "accuracy", "precision", "recall"]}


def schema_families_light() -> list[dict[str, Any]]:
    """Catalogue entries (key, problem types) without touching AutoGluon's model classes."""
    return [catalogue.get(k) or {"key": k} for k in GYM_FAMILIES]
