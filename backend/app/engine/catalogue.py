"""The model families ENSEMBLE trains through AutoGluon, with what each one minimises.

`key` is AutoGluon's hyperparameters key (use it in `fit_kwargs.hyperparameters`). `objective` is plain Unicode
maths for display; `objective_tex` is the same in LaTeX; `symbols` defines every symbol used, so the UI can show a
where-list. Losses are written for one training set {(xᵢ, yᵢ)}ᵢ₌₁ⁿ; ℓ is the task loss (log-loss for
classification, squared error for regression) unless the entry says otherwise.
"""

from __future__ import annotations

from typing import Any

_COMMON = {
    "n": "number of training rows",
    "xᵢ, yᵢ": "features and label of row i",
    "ℓ": "task loss: log-loss (classification) or squared error (regression)",
}

FAMILIES: list[dict[str, Any]] = [
    {
        "key": "GBM", "name": "LightGBM", "kind": "gradient-boosted trees",
        "description": "Leaf-wise gradient boosting on histogram-binned features; usually the strongest single model on tables.",
        "objective": "min_F Σᵢ ℓ(yᵢ, F(xᵢ)) + Σₜ (γ·Tₜ + ½λ‖wₜ‖²),  F = Σₜ η·fₜ",
        "objective_tex": r"\min_F \sum_i \ell(y_i, F(x_i)) + \sum_t \left(\gamma T_t + \tfrac12 \lambda \lVert w_t \rVert^2\right),\quad F=\sum_t \eta f_t",
        "symbols": {"fₜ": "tree t", "Tₜ": "number of leaves of tree t", "wₜ": "leaf values of tree t", "γ, λ": "leaf and L2 penalties", "η": "learning rate"},
        "problem_types": ["binary", "multiclass", "regression", "quantile"],
    },
    {
        "key": "CAT", "name": "CatBoost", "kind": "gradient-boosted trees",
        "description": "Boosting on symmetric (oblivious) trees with ordered target statistics for categorical columns.",
        "objective": "min_F Σᵢ ℓ(yᵢ, F(xᵢ)) + λ‖w‖²,  F = Σₜ η·fₜ (oblivious trees, ordered boosting)",
        "objective_tex": r"\min_F \sum_i \ell(y_i, F(x_i)) + \lambda \lVert w \rVert^2,\quad F=\sum_t \eta f_t",
        "symbols": {"fₜ": "symmetric tree t", "w": "leaf values", "λ": "L2 leaf regularisation", "η": "learning rate"},
        "problem_types": ["binary", "multiclass", "regression", "quantile"],
    },
    {
        "key": "XGB", "name": "XGBoost", "kind": "gradient-boosted trees",
        "description": "Depth-wise gradient boosting with a second-order (Newton) step and explicit tree complexity penalty.",
        "objective": "min_F Σᵢ ℓ(yᵢ, F(xᵢ)) + Σₜ (γ·Tₜ + ½λ‖wₜ‖² + α‖wₜ‖₁)",
        "objective_tex": r"\min_F \sum_i \ell(y_i, F(x_i)) + \sum_t \left(\gamma T_t + \tfrac12\lambda\lVert w_t\rVert^2 + \alpha \lVert w_t \rVert_1\right)",
        "symbols": {"Tₜ": "leaves of tree t", "wₜ": "leaf values", "γ": "split penalty", "λ, α": "L2 and L1 penalties"},
        "problem_types": ["binary", "multiclass", "regression"],
    },
    {
        "key": "RF", "name": "Random forest", "kind": "bagged trees",
        "description": "Average of deep trees, each grown on a bootstrap sample with random feature subsets at each split.",
        "objective": "f(x) = (1/B) Σ_b T_b(x),  each split: max_{j,s} ΔImpurity (Gini / entropy / MSE)",
        "objective_tex": r"f(x) = \frac{1}{B}\sum_{b=1}^{B} T_b(x),\quad \text{split} = \arg\max_{j,s} \Delta\,\mathrm{Impurity}",
        "symbols": {"B": "number of trees", "T_b": "tree b grown on bootstrap sample b", "j, s": "feature and threshold of a split"},
        "problem_types": ["binary", "multiclass", "regression", "quantile"],
    },
    {
        "key": "XT", "name": "Extra trees", "kind": "bagged trees",
        "description": "Like a random forest, but thresholds are drawn at random: more variance reduction, faster to fit.",
        "objective": "f(x) = (1/B) Σ_b T_b(x),  each split: max_j ΔImpurity at a random threshold s ~ U(min xⱼ, max xⱼ)",
        "objective_tex": r"f(x) = \frac{1}{B}\sum_{b=1}^{B} T_b(x),\quad s \sim \mathcal{U}(\min x_j, \max x_j)",
        "symbols": {"B": "number of trees", "T_b": "tree b", "s": "random split threshold on feature j"},
        "problem_types": ["binary", "multiclass", "regression", "quantile"],
    },
    {
        "key": "KNN", "name": "k-nearest neighbours", "kind": "instance-based",
        "description": "Predicts from the k closest training rows; no training loss, a baseline that catches local structure.",
        "objective": "f(x) = (1/k) Σ_{i ∈ N_k(x)} yᵢ  (uniform)  or  Σ wᵢyᵢ / Σ wᵢ, wᵢ = 1/‖x − xᵢ‖ (distance)",
        "objective_tex": r"f(x) = \frac{1}{k}\sum_{i \in N_k(x)} y_i",
        "symbols": {"k": "number of neighbours", "N_k(x)": "the k training rows closest to x"},
        "problem_types": ["binary", "multiclass", "regression"],
    },
    {
        "key": "LR", "name": "Linear model", "kind": "linear",
        "description": "Regularised logistic regression (classification) or ridge regression on one-hot and scaled numeric features.",
        "objective": "min_w Σᵢ log(1 + exp(−ỹᵢ·wᵀxᵢ)) + (1/2C)‖w‖²   (ridge: Σᵢ (yᵢ − wᵀxᵢ)² + α‖w‖²)",
        "objective_tex": r"\min_w \sum_i \log\left(1 + e^{-\tilde y_i w^\top x_i}\right) + \frac{1}{2C}\lVert w\rVert^2",
        "symbols": {"w": "weights", "ỹᵢ": "label as ±1", "C": "inverse regularisation strength", "α": "ridge penalty"},
        "problem_types": ["binary", "multiclass", "regression"],
    },
    {
        "key": "NN_TORCH", "name": "Neural net (PyTorch)", "kind": "neural network",
        "description": "Feed-forward network with embeddings for categorical columns, trained with Adam and early stopping.",
        "objective": "min_θ (1/n) Σᵢ ℓ(yᵢ, f_θ(xᵢ)) + λ‖θ‖²,  f_θ = W_L σ(… σ(W₁[x; E(c)]))",
        "objective_tex": r"\min_\theta \frac1n \sum_i \ell(y_i, f_\theta(x_i)) + \lambda \lVert\theta\rVert^2",
        "symbols": {"θ": "all network weights", "σ": "activation", "E(c)": "embeddings of the categorical columns", "λ": "weight decay"},
        "problem_types": ["binary", "multiclass", "regression", "quantile"],
    },
    {
        "key": "FASTAI", "name": "Neural net (fastai)", "kind": "neural network",
        "description": "fastai's tabular network: categorical embeddings, batch norm and dropout, one-cycle training.",
        "objective": "min_θ (1/n) Σᵢ ℓ(yᵢ, f_θ(xᵢ)) + λ‖θ‖²  (one-cycle learning rate)",
        "objective_tex": r"\min_\theta \frac1n \sum_i \ell(y_i, f_\theta(x_i)) + \lambda \lVert\theta\rVert^2",
        "symbols": {"θ": "network weights", "λ": "weight decay"},
        "problem_types": ["binary", "multiclass", "regression"],
    },
    {
        "key": "REALMLP", "name": "RealMLP", "kind": "neural network",
        "description": "A tuned MLP for tables: robust scaling, numeric embeddings and a fixed schedule that work well by default.",
        "objective": "min_θ (1/n) Σᵢ ℓ(yᵢ, f_θ(xᵢ))  (label smoothing ε for classification)",
        "objective_tex": r"\min_\theta \frac1n \sum_i \ell_\varepsilon(y_i, f_\theta(x_i))",
        "symbols": {"θ": "network weights", "ε": "label smoothing"},
        "problem_types": ["binary", "multiclass", "regression"],
    },
    {
        "key": "TABM", "name": "TabM", "kind": "neural network",
        "description": "An MLP that trains k weight-sharing members at once (BatchEnsemble) and averages them.",
        "objective": "min_θ (1/n) Σᵢ (1/k) Σⱼ ℓ(yᵢ, f_θ⁽ʲ⁾(xᵢ)),  f(x) = (1/k) Σⱼ f_θ⁽ʲ⁾(x)",
        "objective_tex": r"\min_\theta \frac1n \sum_i \frac1k \sum_j \ell\left(y_i, f^{(j)}_\theta(x_i)\right)",
        "symbols": {"k": "ensemble members", "f⁽ʲ⁾": "member j"},
        "problem_types": ["binary", "multiclass", "regression"],
    },
    {
        "key": "TFIDF_LR", "name": "TF-IDF → logistic regression (OvR)", "kind": "text · linear",
        "custom": True,
        "description": "TF-IDF of the raw text (1–2-grams), then one L2-regularised logistic regression per class (one-vs-rest).",
        "objective": "for each class c: min_{w_c} Σᵢ log(1 + exp(−ỹᵢᶜ·w_cᵀφ(xᵢ))) + (1/2C)‖w_c‖²,  φ(x)ₜ = tf(t,x)·log((1+n)/(1+df(t)))+1",
        "objective_tex": r"\forall c:\ \min_{w_c} \sum_i \log\left(1+e^{-\tilde y_i^{c}\, w_c^\top \phi(x_i)}\right) + \frac{1}{2C}\lVert w_c\rVert^2",
        "symbols": {"φ(x)": "L2-normalised TF-IDF vector of the text", "tf(t,x)": "1 + log count of term t in x (sublinear)", "df(t)": "rows containing t", "ỹᵢᶜ": "+1 if row i is class c, else −1", "C": "inverse regularisation (default 5)"},
        "problem_types": ["binary", "multiclass"],
        "explains": "top words per class (largest w_c)",
    },
    {
        "key": "TFIDF_SVM", "name": "TF-IDF → linear SVM", "kind": "text · linear",
        "custom": True,
        "description": "TF-IDF of the raw text, then a one-vs-rest linear SVM; Platt scaling (3-fold) turns margins into probabilities.",
        "objective": "for each class c: min_{w_c} ½‖w_c‖² + C Σᵢ max(0, 1 − ỹᵢᶜ·w_cᵀφ(xᵢ))²;  p(c|x) = σ(a_c·w_cᵀφ(x) + b_c)",
        "objective_tex": r"\forall c:\ \min_{w_c} \tfrac12\lVert w_c\rVert^2 + C\sum_i \max\left(0, 1-\tilde y_i^{c}\, w_c^\top\phi(x_i)\right)^2",
        "symbols": {"φ(x)": "TF-IDF vector", "C": "misclassification cost (default 0.5)", "a_c, b_c": "Platt calibration fitted on held-out folds", "σ": "logistic function"},
        "problem_types": ["binary", "multiclass"],
        "explains": "top words per class (largest w_c)",
    },
    {
        "key": "SCORECARD", "name": "Scorecard (WoE logistic regression)", "kind": "interpretable · linear",
        "custom": True,
        "description": "The credit scorecard: each feature binned (monotone event rate, ≥ 5 % of rows per bin, missing "
                       "on its own), bins replaced by their weight of evidence, a logistic regression on the WoE "
                       "columns, and points per bin. Binary only; on by default as the interpretable benchmark.",
        "objective": "logit P(y=1|x) = β₀ + Σⱼ βⱼ·WoEⱼ(xⱼ),  WoEᵢⱼ = ln((gᵢ+α)/(G+kα)) − ln((bᵢ+α)/(B+kα)),  βⱼ ≤ 0;  "
                     "score = offset − factor·(β₀ + Σⱼ βⱼ·WoEⱼ),  factor = PDO/ln 2",
        "objective_tex": r"\operatorname{logit} P(y{=}1\mid x) = \beta_0 + \sum_j \beta_j\,\mathrm{WoE}_j(x_j),\quad "
                         r"\mathrm{WoE}_{ij} = \ln\frac{(g_i+\alpha)/(G+k\alpha)}{(b_i+\alpha)/(B+k\alpha)}",
        "symbols": {"gᵢ, bᵢ": "non-events and events in bin i (training folds only)", "G, B": "their totals",
                    "α": "smoothing (0.5)", "k": "bins of the feature", "βⱼ": "coefficient of feature j (kept ≤ 0)",
                    "PDO": "points that double the odds of a non-event (default 20)",
                    "offset": "base score − factor·ln(base odds) (default 600 at 50 : 1)"},
        "problem_types": ["binary"],
        "explains": "the scorecard table (points per bin), IV per feature, ΔAUC of the ensemble over it",
    },
    {
        "key": "ENS_WEIGHTED", "name": "Weighted ensemble", "kind": "ensemble",
        "description": "Caruana greedy ensemble selection over the models' out-of-fold (or validation) predictions; the weights sum to 1.",
        "objective": "ŷ(x) = Σₘ wₘ·fₘ(x),  w = arg min L(y, Σₘ wₘ fₘ(Xᵥₐₗ)) over wₘ ∈ {0, 1/K, …, 1},  Σₘ wₘ = 1",
        "objective_tex": r"\hat y(x) = \sum_m w_m f_m(x),\quad w = \arg\min_{w\ge 0,\ \sum w = 1} L\left(y, \sum_m w_m f_m(X_{\mathrm{val}})\right)",
        "symbols": {"fₘ": "base model m", "wₘ": "its weight: the share of the K greedy steps that picked fₘ", "K": "greedy selection steps (AutoGluon: 25)", "L": "the eval metric as a loss", "Xᵥₐₗ": "out-of-fold or validation rows, y their labels"},
        "problem_types": ["binary", "multiclass", "regression", "quantile"],
    },
]

_BY_KEY = {f["key"]: f for f in FAMILIES}

# AutoGluon model class name → family key, for naming leaderboard rows.
CLASS_TO_KEY = {
    "LGBModel": "GBM", "CatBoostModel": "CAT", "XGBoostModel": "XGB", "RFModel": "RF", "XTModel": "XT",
    "KNNModel": "KNN", "LinearModel": "LR", "TabularNeuralNetTorchModel": "NN_TORCH",
    "NNFastAiTabularModel": "FASTAI", "RealMLPModel": "REALMLP", "TabMModel": "TABM",
    "TfidfLogRegModel": "TFIDF_LR", "TfidfSVMModel": "TFIDF_SVM", "ScorecardModel": "SCORECARD",
    # F8: the monotone-constrained subclasses are the same families (their runs say they are constrained)
    "MonotoneLGBModel": "GBM", "MonotoneCatBoostModel": "CAT", "MonotoneXGBoostModel": "XGB",
    "GreedyWeightedEnsembleModel": "ENS_WEIGHTED", "WeightedEnsembleModel": "ENS_WEIGHTED",
    "SimpleWeightedEnsembleModel": "ENS_WEIGHTED",
}


def family_for_class(class_name: str | None) -> str | None:
    if not class_name:
        return None
    if class_name in CLASS_TO_KEY:
        return CLASS_TO_KEY[class_name]
    try:  # anything else AutoGluon knows about (e.g. TabPFN, EBM)
        from autogluon.tabular.registry import ag_model_registry

        for cls in ag_model_registry.model_cls_list:
            if cls.__name__ == class_name:
                return ag_model_registry.key(cls)
    except Exception:  # noqa: BLE001
        pass
    return None


def catalogue() -> list[dict[str, Any]]:
    return [{**f, "symbols": {**_COMMON, **f["symbols"]}, "custom": bool(f.get("custom"))} for f in FAMILIES]


def get(key: str) -> dict[str, Any] | None:
    return _BY_KEY.get(key)
