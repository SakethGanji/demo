"""G3's sample: a loan book with the three expected-loss components, for PD × LGD × EAD.

Each loan carries the default flag (`defaulted`), and for defaulters only, the loss given default (`lgd`, the share of
the exposure that was lost, 0–1) and the exposure at default (`ead`, money owed when the loan defaulted). Non-defaulters
have both blank, as on a real loss database. The realised loss of a loan is `defaulted · lgd · ead`.

The generator (all seeded):

    logit PD = −2.35 − 0.011·(credit_score − 680) + 3.0·(debt_to_income − 0.35) + 1.6·(utilization − 0.5)
               − 0.45·ln(income / 55,000) + product effect (card +0.35, sme +0.25, auto −0.25, personal 0)
    LGD      = clip(base_product − 0.45·min(collateral_value / EAD, 1) + 𝒩(0, 0.10), 0, 1)
               base: card 0.78, personal 0.62, sme 0.55, auto 0.50
    EAD      = current_balance + CCF·(credit_limit − current_balance)   (revolving: card, sme; CCF ~ Beta(4, 4))
             = current_balance · U(0.85, 1.0)                          (instalment: personal, auto)

The process is the same in every month, so an out-of-time holdout (the latest applications) measures the models, not a
change in the book. Product and region are the segments of the expected-loss breakdown.
"""

from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd

PRODUCTS = {"card": 0.35, "personal": 0.30, "auto": 0.20, "sme": 0.15}
PD_EFFECT = {"card": 0.35, "personal": 0.0, "auto": -0.25, "sme": 0.25}
LGD_BASE = {"card": 0.78, "personal": 0.62, "auto": 0.50, "sme": 0.55}
REVOLVING = {"card", "sme"}
REGIONS = {"north": 0.3, "south": 0.25, "east": 0.25, "west": 0.2}


def generate_el_loans(n: int = 20_000, seed: int = 42) -> tuple[pd.DataFrame, dict[str, Any]]:
    rng = np.random.default_rng([seed, 3])
    product = rng.choice(list(PRODUCTS), size=n, p=list(PRODUCTS.values()))
    region = rng.choice(list(REGIONS), size=n, p=list(REGIONS.values()))
    days = rng.integers(0, 730, size=n)
    date = pd.Timestamp("2023-01-01") + pd.to_timedelta(np.sort(days), unit="D")
    income = np.round(np.exp(rng.normal(np.log(55_000), 0.45, n)), -2)
    score = np.clip(np.round(rng.normal(680, 60, n)), 450, 850)
    dti = np.round(np.clip(rng.beta(3, 6, n) * 0.9 + 0.02, 0.02, 0.9), 3)
    size_scale = np.select([product == "card", product == "personal", product == "auto"], [8_000, 18_000, 28_000], 60_000)
    limit = np.round(size_scale * np.exp(rng.normal(0, 0.45, n)) * np.clip(income / 55_000, 0.4, 2.5) ** 0.4, -2)
    util = np.where(np.isin(product, list(REVOLVING)), rng.beta(2.2, 2.8, n), rng.uniform(0.35, 0.98, n))
    util = np.round(util, 3)
    balance = np.round(limit * util, 2)
    collateral = np.where(product == "auto", limit * rng.uniform(0.5, 1.1, n),
                          np.where(product == "sme", limit * rng.uniform(0.0, 0.8, n), 0.0))
    collateral = np.round(collateral, -1)
    term = np.select([product == "card", product == "personal", product == "auto"],
                     [0, rng.choice([24, 36, 48, 60], n), rng.choice([36, 48, 60, 72], n)], rng.choice([12, 24, 36], n))

    logit = (-2.35 - 0.011 * (score - 680) + 3.0 * (dti - 0.35) + 1.6 * (util - 0.5) - 0.45 * np.log(income / 55_000)
             + np.vectorize(PD_EFFECT.get)(product))
    pd_true = 1 / (1 + np.exp(-logit))
    defaulted = (rng.random(n) < pd_true).astype(int)

    ccf = rng.beta(4, 4, n)
    ead_all = np.where(np.isin(product, list(REVOLVING)), balance + ccf * (limit - balance), balance * rng.uniform(0.85, 1.0, n))
    cover = np.minimum(collateral / np.maximum(ead_all, 1), 1.0)
    lgd_all = np.clip(np.vectorize(LGD_BASE.get)(product) - 0.45 * cover + rng.normal(0, 0.10, n), 0, 1)
    d = defaulted == 1
    df = pd.DataFrame({
        "loan_id": [f"L{100000 + i}" for i in range(n)],
        "application_date": date.strftime("%Y-%m-%d"),
        "product": product, "region": region,
        "income": income, "credit_score": score.astype(int), "debt_to_income": dti, "utilization": util,
        "credit_limit": limit, "current_balance": balance, "collateral_value": collateral, "term_months": term,
        "defaulted": defaulted,
        "lgd": np.where(d, np.round(lgd_all, 4), np.nan),
        "ead": np.where(d, np.round(ead_all, 2), np.nan),
    })
    loss = float((df["lgd"].fillna(0) * df["ead"].fillna(0)).sum())
    sidecar = {
        "label_column": "defaulted", "lgd_column": "lgd", "ead_column": "ead", "segments": ["product", "region"],
        "default_rate": float(defaulted.mean()), "realised_loss": loss, "n": n,
        "mean_true_pd": float(pd_true.mean()), "expected_loss_true": float((pd_true * lgd_all * ead_all).sum()),
        "generator": "samples_g3.generate_el_loans (seeded)",
    }
    return df, sidecar


SAMPLES_G3 = {
    "loan-losses": {
        "name": "Loan losses (sample)",
        "filename": "loan_losses_sample.csv",
        "note": "Generated sample · 20,000 loans with default flag, loss given default and exposure at default (G3)",
        "generate": generate_el_loans,
        "sidecar": "el_truth.json",
    },
}
