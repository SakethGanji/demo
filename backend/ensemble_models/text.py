"""TF-IDF text models as AutoGluon `AbstractModel` subclasses.

- `TfidfLogRegModel` (key `TFIDF_LR`): TF-IDF → logistic regression, one-vs-rest for multiclass.
- `TfidfSVMModel` (key `TFIDF_SVM`): TF-IDF → linear SVM, calibrated (Platt, k-fold) so it gives probabilities.

Both use only the raw text columns (AutoGluon special type `text`, kept when the feature generator runs with
`enable_raw_text_features=True`); with no raw text they raise `NoValidFeatures` and AutoGluon skips them.
Both expose `top_words(k)`: the k words with the largest coefficient for each class.
"""

from __future__ import annotations

import logging

import numpy as np
import pandas as pd
import scipy.sparse as sp
from autogluon.common.features.types import S_TEXT
from autogluon.core.constants import BINARY, MULTICLASS
from autogluon.core.models import AbstractModel

log = logging.getLogger(__name__)


class _TfidfModel(AbstractModel):
    """Shared TF-IDF featurisation: one vectorizer per text column, stacked side by side."""

    _supported_problem_types = [BINARY, MULTICLASS]
    _default_auxiliary_params_extra = dict(get_features_kwargs=dict(required_special_types=[S_TEXT]))
    # Fit bagged folds in-process: sklearn is quick here, and Ray workers would need this package on their path.
    _default_ag_args_ensemble_extra = dict(fold_fitting_strategy="sequential_local")

    _tfidf_defaults = dict(ngram_max=2, min_df=2, max_features=200_000, sublinear_tf=True, lowercase=True)

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self._vectorizers: dict[str, object] | None = None
        self._vocab: list[str] | None = None

    def _set_default_params(self):
        for k, v in {**self._tfidf_defaults, **self._model_defaults()}.items():
            self._set_default_param_value(k, v)

    def _model_defaults(self) -> dict:
        return {}

    def _preprocess(self, X: pd.DataFrame, is_train: bool = False, **kwargs):
        X = super()._preprocess(X, **kwargs)
        from sklearn.feature_extraction.text import TfidfVectorizer

        T = X[self._features].fillna("").astype(str)
        p = self._get_model_params()
        if is_train:
            self._vectorizers, self._vocab = {}, []
            multi = len(T.columns) > 1
            for col in T.columns:
                vec = TfidfVectorizer(ngram_range=(1, int(p["ngram_max"])), min_df=int(p["min_df"]),
                                      max_features=int(p["max_features"]), sublinear_tf=bool(p["sublinear_tf"]),
                                      lowercase=bool(p["lowercase"]), dtype=np.float32)
                try:
                    vec.fit(T[col])
                except ValueError:  # empty vocabulary (e.g. min_df too high on a tiny fold)
                    vec = TfidfVectorizer(ngram_range=(1, 1), dtype=np.float32).fit(T[col].replace("", "_"))
                self._vectorizers[col] = vec
                self._vocab += [f"{col}: {w}" if multi else w for w in vec.get_feature_names_out()]
        mats = [self._vectorizers[c].transform(T[c]) for c in T.columns]
        return sp.hstack(mats, format="csr") if len(mats) > 1 else mats[0].tocsr()

    def _model_params(self) -> dict:
        p = self._get_model_params()
        return {k: v for k, v in p.items() if k not in self._tfidf_defaults}

    def _fit(self, X: pd.DataFrame, y: pd.Series, time_limit: float | None = None, num_cpus: int = 1, **kwargs):
        Xt = self.preprocess(X, is_train=True)
        self.model = self._make_estimator(y, num_cpus=num_cpus)
        self.model.fit(Xt, np.asarray(y))

    def _make_estimator(self, y: pd.Series, num_cpus: int = 1):
        raise NotImplementedError

    def _coef_matrix(self) -> np.ndarray:
        """Coefficients, one row per class (binary: one row, for the positive class)."""
        raise NotImplementedError

    def top_words(self, k: int = 15, class_names: list | None = None) -> dict[str, list[tuple[str, float]]]:
        """The k vocabulary terms with the largest coefficient per class. For binary, the negative class gets
        the most negative coefficients (sign flipped), so both lists read 'evidence for this class'."""
        W = self._coef_matrix()
        vocab = np.asarray(self._vocab)
        names = class_names if class_names is not None else list(range(self.num_classes))
        rows = [-W[0], W[0]] if W.shape[0] == 1 else list(W)
        out = {}
        for name, w in zip(names, rows):
            idx = np.argsort(-w)[:k]
            out[str(name)] = [(str(vocab[i]), round(float(w[i]), 6)) for i in idx]
        return out

    def _get_default_resources(self):
        return 1, 0

    def _more_tags(self):
        return {"can_refit_full": True}


class TfidfLogRegModel(_TfidfModel):
    """TF-IDF → logistic regression. Multiclass is one-vs-rest: one binary L2 log-loss model per class."""

    ag_key = "TFIDF_LR"
    ag_name = "TfidfLogReg"
    ag_priority = 95

    def _model_defaults(self) -> dict:
        return dict(C=5.0, max_iter=1000, class_weight=None)

    def _make_estimator(self, y, num_cpus: int = 1):
        from sklearn.linear_model import LogisticRegression
        from sklearn.multiclass import OneVsRestClassifier

        p = self._model_params()
        base = LogisticRegression(C=float(p["C"]), max_iter=int(p["max_iter"]), class_weight=p["class_weight"],
                                  solver="liblinear")
        return base if self.problem_type == BINARY else OneVsRestClassifier(base, n_jobs=1)

    def _coef_matrix(self) -> np.ndarray:
        if self.problem_type == BINARY:
            return self.model.coef_
        return np.vstack([e.coef_ for e in self.model.estimators_])


class TfidfSVMModel(_TfidfModel):
    """TF-IDF → linear SVM (squared hinge, one-vs-rest), Platt-calibrated with k-fold CV for probabilities."""

    ag_key = "TFIDF_SVM"
    ag_name = "TfidfLinearSVM"
    ag_priority = 94

    def _model_defaults(self) -> dict:
        return dict(C=0.5, max_iter=5000, class_weight=None, calibration="sigmoid", calibration_cv=3)

    def _make_estimator(self, y, num_cpus: int = 1):
        from sklearn.calibration import CalibratedClassifierCV
        from sklearn.svm import LinearSVC

        p = self._model_params()
        svc = LinearSVC(C=float(p["C"]), max_iter=int(p["max_iter"]), class_weight=p["class_weight"])
        min_count = int(pd.Series(np.asarray(y)).value_counts().min())
        cv = min(int(p["calibration_cv"]), min_count)
        if cv < 2:
            # A class with a single row can't be cross-calibrated: fall back to softmax of the margins.
            return _SoftmaxSVC(svc)
        return CalibratedClassifierCV(svc, method=p["calibration"], cv=cv)

    def _coef_matrix(self) -> np.ndarray:
        if isinstance(self.model, _SoftmaxSVC):
            return self.model.svc.coef_
        return np.mean([c.estimator.coef_ for c in self.model.calibrated_classifiers_], axis=0)


class _SoftmaxSVC:
    """LinearSVC with softmax(margins) as probabilities; used only when calibration CV is impossible."""

    def __init__(self, svc):
        self.svc = svc

    def fit(self, X, y):
        self.svc.fit(X, y)
        return self

    def predict_proba(self, X):
        m = self.svc.decision_function(X)
        if m.ndim == 1:
            m = np.column_stack([-m, m])
        e = np.exp(m - m.max(axis=1, keepdims=True))
        return e / e.sum(axis=1, keepdims=True)
