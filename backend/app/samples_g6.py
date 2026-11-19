"""G6's sample: current accounts followed from opening until they close (churn) or until the data ends (censored),
for time-to-event models.

Each account's time to closing T (months) is drawn from a Weibull proportional-hazards model (seeded):

    h(t | x) = h₀(t) · exp(η),   h₀(t) = (k/λ)(t/λ)^(k−1),   k = 1.15, λ = 64 months
    η = −0.50·(num_products − 2) − 0.035·(digital_logins_30d − 10) + 0.60·complaints_12m − 0.90·rate_vs_market_pp
        − 0.90·has_salary_mandate − 0.30·ln(avg_balance_gbp / 3,000) + 1.00·[age < 28] + 0.50·[age ≥ 70]
        + {current 0, savings −0.35, premium −0.60}[product]

    T = λ · (−ln U / e^η)^(1/k),  U ~ Uniform(0, 1)

Accounts open uniformly over 2021-01 … 2025-11 and the data ends on 2025-12-31, so an account opened m months before
the end is censored at m if it has not closed (administrative censoring). 4 % per year also leave the data for reasons
unrelated to churn (moved abroad, deceased: independent random censoring). The table records

    duration_months = min(T, C)  (whole months, at least 1)      churned = 1 if T ≤ C (closed), 0 if censored

The age effect is a step (young and very old customers leave sooner), which a Cox model with a linear age term can only
approximate; a random survival forest can find it. The sidecar keeps the true η and T so the C-index and calibration can
be checked against the truth.
"""

from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd

PRODUCTS = {"current": 0.55, "savings": 0.25, "premium": 0.20}
PRODUCT_EFFECT = {"current": 0.0, "savings": -0.35, "premium": -0.60}
K, LAM = 1.15, 64.0
END = pd.Timestamp("2025-12-31")


def generate_attrition(n: int = 12_000, seed: int = 42) -> tuple[pd.DataFrame, dict[str, Any]]:
    rng = np.random.default_rng([seed, 6])
    opened = pd.Timestamp("2021-01-01") + pd.to_timedelta(rng.integers(0, (pd.Timestamp("2025-11-30") - pd.Timestamp("2021-01-01")).days, n), unit="D")
    opened = pd.DatetimeIndex(np.sort(opened.values))
    product = rng.choice(list(PRODUCTS), size=n, p=list(PRODUCTS.values()))
    age = np.clip(rng.normal(44, 15, n), 18, 90).round().astype(int)
    num_products = np.clip(rng.poisson(1.3, n) + 1, 1, 6)
    logins = np.clip(rng.gamma(2.0, 6.0, n), 0, 90).round().astype(int)
    complaints = rng.poisson(0.35, n)
    rate = np.round(rng.normal(-0.2, 0.6, n), 2)  # the account's rate minus the market rate, percentage points
    mandate = (rng.random(n) < np.where(product == "current", 0.55, 0.30)).astype(int)
    balance = np.round(np.exp(rng.normal(np.log(np.where(product == "premium", 12_000, 3_000)), 0.9, n)), -1)
    region = rng.choice(["north", "midlands", "south", "london", "wales", "scotland"], size=n, p=[0.2, 0.18, 0.22, 0.2, 0.1, 0.1])

    eta = (-0.50 * (num_products - 2) - 0.035 * (logins - 10) + 0.60 * complaints - 0.90 * rate - 0.90 * mandate
           - 0.30 * np.log(balance / 3_000) + 1.00 * (age < 28) + 0.50 * (age >= 70)
           + np.vectorize(PRODUCT_EFFECT.get)(product))
    T = LAM * (-np.log(rng.random(n)) / np.exp(eta)) ** (1 / K)
    admin = ((END - opened).days.to_numpy() / 30.4375)
    other = rng.exponential(12 / 0.04, n)  # 4 % a year leave for reasons unrelated to churn
    C = np.minimum(admin, other)
    event = (T <= C).astype(int)
    dur = np.maximum(1, np.ceil(np.minimum(T, C))).astype(int)

    df = pd.DataFrame({
        "account_id": [f"AC{500000 + i}" for i in range(n)],
        "open_date": opened.strftime("%Y-%m-%d"),
        "product": product, "region": region, "age": age, "num_products": num_products,
        "digital_logins_30d": logins, "complaints_12m": complaints, "rate_vs_market_pp": rate,
        "has_salary_mandate": mandate, "avg_balance_gbp": balance,
        "churned": event, "duration_months": dur,
    })
    sidecar = {
        "label_column": "churned", "duration_column": "duration_months", "event_column": "churned", "n": n,
        "event_rate": float(event.mean()), "censored_admin": int(((T > C) & (admin <= other)).sum()),
        "censored_other": int(((T > C) & (other < admin)).sum()),
        "true_eta": [round(float(e), 4) for e in eta],
        "weibull": {"k": K, "lambda": LAM},
        "generator": "samples_g6.generate_attrition (seeded)",
    }
    return df, sidecar


SAMPLES_G6 = {
    "account-attrition": {
        "name": "Account attrition, time to close (sample)",
        "filename": "account_attrition_sample.csv",
        "note": "Generated sample · 12,000 current accounts followed until they close or the data ends, with censoring (G6)",
        "generate": generate_attrition,
        "sidecar": "attrition_truth.json",
    },
}
