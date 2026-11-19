"""Custom AutoGluon models shipped with ENSEMBLE.

Importing this package registers the models in AutoGluon's model registry, so their keys work in a
`hyperparameters` dict exactly like the built-in ones, in the app and in plain AutoGluon:

    import ensemble_models  # registers TFIDF_LR, TFIDF_SVM, SCORECARD and the _MONO subclasses
    from autogluon.tabular import TabularPredictor
    TabularPredictor(label="category").fit(df, hyperparameters={"GBM": {}, "TFIDF_LR": {}, "TFIDF_SVM": {}},
                                           _feature_generator_kwargs={"enable_raw_text_features": True})

The models read raw text columns, so the feature generator must keep them
(`_feature_generator_kwargs={"enable_raw_text_features": True}`). A saved predictor that contains
them loads with `TabularPredictor.load(path)` once this package is importable (it is in `backend/`).
"""

from . import monotone
from .monotone import MonotoneCatBoostModel, MonotoneLGBModel, MonotoneXGBoostModel
from .scorecard import ScorecardModel
from .text import TfidfLogRegModel, TfidfSVMModel


def _register(*classes) -> None:
    from autogluon.tabular.registry import ag_model_registry

    for cls in classes:
        if not ag_model_registry.exists(cls) and cls.ag_key not in ag_model_registry.keys:
            ag_model_registry.add(cls)


def register() -> None:
    """Register every shipped model in AutoGluon's registry (idempotent): TFIDF_LR / TFIDF_SVM, SCORECARD (F11) and
    GBM_MONO, CAT_MONO, XGB_MONO (F8)."""
    _register(TfidfLogRegModel, TfidfSVMModel, ScorecardModel, *monotone.MODELS)


register()

__all__ = ["TfidfLogRegModel", "TfidfSVMModel", "MonotoneLGBModel", "MonotoneXGBoostModel", "MonotoneCatBoostModel",
           "ScorecardModel", "register"]
