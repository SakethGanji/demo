"""Custom AutoGluon models shipped with ENSEMBLE.

Importing this package registers the models in AutoGluon's model registry, so their keys work in a
`hyperparameters` dict exactly like the built-in ones, in the app and in plain AutoGluon:

    import ensemble_models  # registers TFIDF_LR and TFIDF_SVM
    from autogluon.tabular import TabularPredictor
    TabularPredictor(label="category").fit(df, hyperparameters={"GBM": {}, "TFIDF_LR": {}, "TFIDF_SVM": {}},
                                           _feature_generator_kwargs={"enable_raw_text_features": True})

The models read raw text columns, so the feature generator must keep them
(`_feature_generator_kwargs={"enable_raw_text_features": True}`). A saved predictor that contains
them loads with `TabularPredictor.load(path)` once this package is importable (it is in `backend/`).
"""

from . import monotone
from .monotone import MonotoneCatBoostModel, MonotoneLGBModel, MonotoneXGBoostModel
from .text import TfidfLogRegModel, TfidfSVMModel, register

register()
monotone.register()  # F8: GBM_MONO, CAT_MONO, XGB_MONO (LightGBM, CatBoost, XGBoost with mapped monotone constraints)

__all__ = ["TfidfLogRegModel", "TfidfSVMModel", "MonotoneLGBModel", "MonotoneXGBoostModel", "MonotoneCatBoostModel",
           "register"]
