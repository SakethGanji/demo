"""F8: monotone constraints that survive AutoGluon's feature pipeline.

LightGBM, XGBoost and CatBoost take `monotone_constraints` as one sign per column of the matrix they are trained on,
by position. That matrix is not the user's table: AutoGluon's feature generator drops useless columns, turns two-valued
numbers into 0/1, adds text n-grams and stack features (the base models' predictions at stack level ≥ 2); each model
then drops constant columns, LightGBM renames columns with special characters, and XGBoost one-hot encodes categories
and puts them first. A list written against the raw columns would land on the wrong features.

So the constraints travel as a hyperparameter `monotone = {"constraints": {raw column: +1 | -1}, "stack": true}` of
three registered subclasses (GBM_MONO, CAT_MONO, XGB_MONO, same model names as their parents), and each model turns it
into its own vector at fit time, from the columns it is actually about to train on:

* a column with a constraint gets its sign (numeric columns keep their name through AutoGluon's pipeline; a two-valued
  number becomes 0/1 with the larger value as 1, so the order is kept);
* a stack feature (special type "stack": a base model's P(positive class) for binary, its prediction for regression)
  gets +1 when `stack` is on. If every base model is monotone in x with sign s, a stacker that is non-decreasing in the
  base predictions and monotone with sign s in x is monotone with sign s in x, so the whole stack stays monotone;
* everything else (categories, one-hot columns, n-grams, other numbers) gets 0.

The vector is written into the params dict the model's `_fit` is about to pass to its library (the same dict object
`_get_model_params()` returned at the start of `_fit`), when `preprocess(..., is_train=True)` has produced the final
columns. After the fit each model checks that its library holds the constraint (`_monotone_verify`) and records
`monotone_applied` = {internal column: sign}, so the mapping is on the saved model for the run's report.

A weighted ensemble over these models (non-negative weights, AutoGluon's greedy ensemble selection) is monotone too.
"""

from __future__ import annotations

from typing import Any

import numpy as np
from autogluon.tabular.models.catboost.catboost_model import CatBoostModel
from autogluon.tabular.models.lgb.lgb_model import LGBModel
from autogluon.tabular.models.xgboost.xgboost_model import XGBoostModel

PARAM = "monotone"
# The families that can honour a monotone constraint, and the subclass key each is trained as.
COMPLIANT = {"GBM": "GBM_MONO", "CAT": "CAT_MONO", "XGB": "XGB_MONO"}
BASE_OF = {v: k for k, v in COMPLIANT.items()}


class MonotoneConstraintError(RuntimeError):
    pass


class _MonotoneMixin:
    """Shared by the three subclasses. `self._mono_params` is the dict `_fit` will hand to the library."""

    _mono_params: dict | None = None
    monotone_applied: dict[str, int] | None = None
    monotone_columns: list[str] | None = None

    def _get_model_params(self, convert_search_spaces_to_default: bool = False) -> dict:
        p = super()._get_model_params(convert_search_spaces_to_default=convert_search_spaces_to_default)
        spec = p.pop(PARAM, None)
        if spec:
            self._mono_spec = spec
            self._mono_params = p
        return p

    def _mono_sign(self, name: str, stack: set[str], categorical: set[str]) -> int:
        spec = getattr(self, "_mono_spec", None) or {}
        cons = spec.get("constraints") or {}
        if name in categorical:
            return 0
        if name in cons:
            return int(np.sign(cons[name]))
        if spec.get("stack", True) and name in stack:
            return 1
        return 0

    def _mono_stack_features(self) -> set[str]:
        fm = getattr(self, "_feature_metadata", None) or getattr(self, "feature_metadata", None)
        try:
            return set(fm.get_features(required_special_types=["stack"]))
        except Exception:  # noqa: BLE001
            return set()

    def preprocess(self, X, *args, is_train: bool = False, **kwargs):
        out = super().preprocess(X, *args, is_train=is_train, **kwargs)
        if is_train and getattr(self, "_mono_spec", None) and self._mono_params is not None:
            names, internal, categorical = self._mono_columns(out)
            stack = self._mono_stack_features()
            vec = [self._mono_sign(n, stack, categorical) for n in names]
            self.monotone_columns = list(internal)
            self.monotone_applied = {n: s for n, s in zip(names, vec) if s}  # AutoGluon's feature names
            self.monotone_internal = {i: s for i, s in zip(internal, vec) if s}  # the names the library sees
            self._mono_params["monotone_constraints"] = self._mono_format(vec)
            self._mono_vec = vec
        return out

    def _fit(self, X, y, **kwargs):
        self._mono_spec = None
        self._mono_params = None
        self._mono_vec = None
        super()._fit(X, y, **kwargs)
        if getattr(self, "_mono_spec", None):
            self._monotone_verify()
        self._mono_params = None  # never pickle the params dict twice

    # -- per library
    def _mono_columns(self, X) -> tuple[list[str], list[str], set[str]]:
        """(AutoGluon feature names in matrix order, the names the library sees, categorical columns)."""
        cols = list(X.columns)
        cat = set(X.select_dtypes(include="category").columns)
        return cols, cols, cat

    def _mono_format(self, vec: list[int]) -> Any:
        return list(vec)

    def _monotone_verify(self) -> None:
        raise NotImplementedError


class MonotoneLGBModel(_MonotoneMixin, LGBModel):
    """LightGBM with `monotone_constraints` mapped onto its training columns (see the module docstring)."""

    ag_key = "GBM_MONO"
    ag_name = "LightGBM"

    def _mono_columns(self, X):
        internal = list(X.columns)
        if getattr(self, "_requires_remap", False):  # LightGBM-safe renames: map back to AutoGluon's names
            inv = {v: k for k, v in self._features_internal_map.items()}
            names = [inv.get(c, c) for c in internal]
        else:
            names = internal
        cat_internal = set(X.select_dtypes(include="category").columns)
        return names, internal, {n for n, i in zip(names, internal) if i in cat_internal}

    def _monotone_verify(self) -> None:
        got = list(self.model.params.get("monotone_constraints") or [])
        if got != list(self._mono_vec or []) or self.model.num_feature() != len(got):
            raise MonotoneConstraintError(f"{self.name}: LightGBM did not receive the monotone constraints "
                                          f"({len(got)} signs for {self.model.num_feature()} features)")


class MonotoneXGBoostModel(_MonotoneMixin, XGBoostModel):
    """XGBoost with `monotone_constraints` mapped onto its training columns: one-hot blocks of the categories first
    (all 0), then the other columns in order, exactly as AutoGluon's OheFeatureGenerator builds the matrix."""

    ag_key = "XGB_MONO"
    ag_name = "XGBoost"

    def _mono_columns(self, X):
        if getattr(self, "_ohe", True) and self._ohe_generator is not None:
            # The width comes from the matrix itself: the encoder merges rare levels (max_levels), so its list of
            # category names can be longer than the one-hot block it actually produces.
            g = self._ohe_generator
            n_ohe = int(X.shape[1]) - len(g.other_cols)
            names = [f"__onehot_{i}" for i in range(n_ohe)] + list(g.other_cols)
            return names, list(names), set(names[:n_ohe])
        return super()._mono_columns(X)

    def _mono_format(self, vec: list[int]) -> Any:
        return "(" + ",".join(str(int(v)) for v in vec) + ")"

    def _monotone_verify(self) -> None:
        got = self.model.get_params().get("monotone_constraints")
        n = int(getattr(self.model, "n_features_in_", len(self._mono_vec or [])))
        if got != self._mono_format(self._mono_vec or []) or n != len(self._mono_vec or []):
            raise MonotoneConstraintError(f"{self.name}: XGBoost did not receive the monotone constraints ({got!r}, {n} features)")


class MonotoneCatBoostModel(_MonotoneMixin, CatBoostModel):
    """CatBoost with `monotone_constraints` mapped onto its training columns (categorical columns stay 0)."""

    ag_key = "CAT_MONO"
    ag_name = "CatBoost"

    def _monotone_verify(self) -> None:
        got = self.model.get_params().get("monotone_constraints")
        if list(got or []) != list(self._mono_vec or []):
            raise MonotoneConstraintError(f"{self.name}: CatBoost did not receive the monotone constraints ({got!r})")


MODELS = (MonotoneLGBModel, MonotoneXGBoostModel, MonotoneCatBoostModel)


def register() -> None:
    from autogluon.tabular.registry import ag_model_registry

    for cls in MODELS:
        if not ag_model_registry.exists(cls) and cls.ag_key not in ag_model_registry.keys:
            ag_model_registry.add(cls)


def translate_hyperparameters(hp: dict[str, Any], constraints: dict[str, int], *, compliant_only: bool,
                              stack: bool = True) -> tuple[dict[str, Any], list[str], list[str]]:
    """The run's hyperparameters with GBM / CAT / XGB swapped for their monotone subclasses, each variant carrying
    `monotone`. Returns (hyperparameters, compliant keys kept, non-compliant keys: removed when `compliant_only`, else
    kept and reported). Level-keyed dicts ({1: {...}, 2: {...}}) are translated level by level."""
    if hp and all(isinstance(k, int) or (isinstance(k, str) and k.isdigit()) for k in hp):
        out, kept, other = {}, [], []
        for lvl, sub in hp.items():
            out[lvl], k, o = translate_hyperparameters(sub, constraints, compliant_only=compliant_only, stack=stack)
            kept += [x for x in k if x not in kept]
            other += [x for x in o if x not in other]
        return out, kept, other
    spec = {"constraints": dict(constraints), "stack": bool(stack)}
    out: dict[str, Any] = {}
    kept: list[str] = []
    other: list[str] = []
    for key, cfg in hp.items():
        base = BASE_OF.get(key, key)
        if key == "SCORECARD":  # F11: monotone by its binning (see ensemble_models.scorecard); kept, not counted here
            variants = cfg if isinstance(cfg, list) else [cfg]
            new = [{**(c or {}), PARAM: spec} for c in variants]
            out[key] = new if isinstance(cfg, list) else new[0]
            continue
        if base in COMPLIANT:
            variants = cfg if isinstance(cfg, list) else [cfg]
            new = [{**(c or {}), PARAM: spec} for c in variants]
            out[COMPLIANT[base]] = new if isinstance(cfg, list) else new[0]
            kept.append(base)
        else:
            other.append(str(key))
            if not compliant_only:
                out[key] = cfg
    return out, kept, other
