"""F16's sample: personal-loan defaults with a planted weak slice.

Outside the slice, default follows a known logistic signal. Inside it (applications introduced by a broker for
applicants under 30) the outcome is a coin flip with P(default) = 0.5 whatever the features say: the recorded
features carry no risk information there (think unverified, broker-supplied income and employment). No model can do
well on those rows, so a good weak-slice search should rank "channel = broker ∧ age < 30" (or the age bin that holds
it) near the top. The truth is in `slice_truth.json`.
"""

from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd

PERSONAL_COEFS = {
    "intercept": -1.8,
    "credit_score_per_100": -1.5,   # (credit_score - 690) / 100
    "debt_to_income": 3.0,          # (dti - 0.30)
    "prior_defaults": 0.55,
    "employment=unemployed": 1.0,
    "employment=self-employed": 0.3,
    "term_months_per_12": 0.08,     # (term - 36) / 12
}
PLANTED = {
    "conditions": [{"column": "channel", "op": "in", "values": ["broker"]},
                   {"column": "age", "op": "range", "lo": None, "hi": 30}],
    "label": "channel = broker ∧ age < 30",
    "p_default": 0.5,
}


def generate_personal_loans(n: int = 10_000, seed: int = 42) -> tuple[pd.DataFrame, dict[str, Any]]:
    rng = np.random.default_rng(seed + 16)
    age = rng.integers(21, 71, size=n)
    income = np.round(np.exp(rng.normal(10.9, 0.45, size=n)), -2)
    credit_score = np.clip(rng.normal(690, 60, size=n), 500, 850).round().astype(int)
    dti = np.round(np.clip(rng.beta(2.2, 5.0, size=n), 0.01, 0.95), 3)
    channel = rng.choice(["branch", "online", "broker", "partner"], size=n, p=[0.35, 0.35, 0.2, 0.1])
    region = rng.choice(["north", "south", "east", "west", "central"], size=n)
    employment = rng.choice(["salaried", "self-employed", "contract", "unemployed"], size=n, p=[0.62, 0.18, 0.13, 0.07])
    term = rng.choice([12, 24, 36, 48, 60], size=n, p=[0.1, 0.2, 0.35, 0.2, 0.15])
    amount = np.round(np.clip(income * rng.uniform(0.05, 0.4, size=n), 1_000, None), -2)
    prior = rng.poisson(0.35, size=n)

    c = PERSONAL_COEFS
    logit = (c["intercept"] + c["credit_score_per_100"] * (credit_score - 690) / 100 + c["debt_to_income"] * (dti - 0.30)
             + c["prior_defaults"] * prior + c["employment=unemployed"] * (employment == "unemployed")
             + c["employment=self-employed"] * (employment == "self-employed") + c["term_months_per_12"] * (term - 36) / 12)
    p = 1 / (1 + np.exp(-logit))
    in_slice = (channel == "broker") & (age < 30)
    p = np.where(in_slice, PLANTED["p_default"], p)
    y = (rng.random(n) < p).astype(int)

    df = pd.DataFrame({
        "application_id": [f"P-{300000 + i}" for i in range(n)],
        "age": age, "income_usd": income, "credit_score": credit_score, "debt_to_income": dti, "channel": channel,
        "region": region, "employment": employment, "term_months": term, "loan_amount_usd": amount,
        "prior_defaults": prior, "defaulted": y,
    })
    sidecar = {
        "dataset": "personal-loans", "seed": seed, "rows": n, "label_column": "defaulted", "problem_type": "binary",
        "signal": "logit P(default) = intercept + Σ coef · feature outside the slice; inside it P(default) = 0.5",
        "coefficients": c, "noise_features": ["region", "income_usd", "loan_amount_usd"],
        "planted_slice": {**PLANTED, "rows": [int(i) for i in np.nonzero(in_slice)[0]], "n": int(in_slice.sum()),
                          "default_rate_inside": float(y[in_slice].mean()), "default_rate_outside": float(y[~in_slice].mean())},
    }
    return df, sidecar
