"""G5's sample: loan applications from four customer types whose risk is driven by different things, for segmented
models (one AutoGluon model per segment against one global model).

`customer_type` is the segment. The same eight inputs are recorded for everyone, but each segment's default process
uses them differently (all seeded):

    salaried        logit PD = −2.30 − 0.012·(credit_score − 680) + 3.0·(debt_to_income − 0.35) + 1.5·(utilization − 0.5)
                               − 0.45·ln(income / 55,000) − 0.008·(months_employed − 60)
    self_employed   logit PD = −2.05 − 0.004·(credit_score − 680) + 6.0·(debt_to_income − 0.35)
                               − 0.03·(min(months_employed, 150) − 36) + 0.30·recent_inquiries
    student         logit PD = −1.20 + 2.6·sin(9·utilization)·cos(7·debt_to_income)
                               + 4.2·sin(1.3·num_accounts)·(utilization − 0.4) + 0.9·cos(0.45·months_employed)
                               + 0.50·recent_inquiries
    small_business  logit PD = −0.30 + 3.0·sin(2.2·ln(4·loan_amount / income))·cos(6·utilization)
                               + 2.0·sin(8·debt_to_income) − 0.012·(credit_score − 680)

Salaried and self-employed risk is close to linear and noisy; student and small-business risk are strong, wavy surfaces
of two or three inputs. One global model has to learn those interactions from the segment column; a model per segment gets
them for free but has fewer rows. The process is the same in every month, so an out-of-time holdout (the latest
applications) measures the models, not a change in the book.
"""

from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd

TYPES = {"salaried": 0.50, "self_employed": 0.20, "student": 0.15, "small_business": 0.15}


def _sigmoid(z: np.ndarray) -> np.ndarray:
    return 1 / (1 + np.exp(-z))


def generate_segmented(n: int = 30_000, seed: int = 42) -> tuple[pd.DataFrame, dict[str, Any]]:
    rng = np.random.default_rng([seed, 5])
    seg = rng.choice(list(TYPES), size=n, p=list(TYPES.values()))
    days = rng.integers(0, 730, size=n)
    date = pd.Timestamp("2023-01-01") + pd.to_timedelta(np.sort(days), unit="D")
    stu, sme = seg == "student", seg == "small_business"
    income = np.exp(rng.normal(np.log(55_000), 0.45, n))
    income = np.where(stu, np.exp(rng.normal(np.log(14_000), 0.5, n)), income)
    income = np.where(sme, np.exp(rng.normal(np.log(120_000), 0.6, n)), income)
    income = np.round(income, -2)
    score = np.clip(rng.normal(np.where(stu, 650, 690), 55, n), 300, 850).round()
    dti = np.clip(rng.beta(3, 6, n) * 0.9, 0.01, 0.85).round(3)
    util = np.clip(rng.beta(2, 2.5, n), 0, 1).round(3)
    months = np.where(stu, rng.integers(0, 24, n), rng.integers(0, 300, n))
    accounts = np.clip(rng.poisson(np.where(stu, 3, 5)), 0, 25)
    loan = np.round(np.exp(rng.normal(np.log(np.where(sme, 60_000, 15_000)), 0.55, n)), -2)
    inq = rng.poisson(1.2, n)

    z = np.select(
        [seg == "salaried", seg == "self_employed", stu, sme],
        [-2.30 - 0.012 * (score - 680) + 3.0 * (dti - 0.35) + 1.5 * (util - 0.5) - 0.45 * np.log(income / 55_000)
         - 0.008 * (months - 60),
         -2.05 - 0.004 * (score - 680) + 6.0 * (dti - 0.35) - 0.03 * (np.minimum(months, 150) - 36) + 0.30 * inq,
         -1.20 + 2.6 * np.sin(9.0 * util) * np.cos(7.0 * dti) + 1.4 * np.sin(1.3 * accounts) * (util - 0.4) * 3
         + 0.9 * np.cos(0.45 * months) + 0.50 * inq,
         -0.30 + 3.0 * np.sin(2.2 * np.log(4 * loan / income)) * np.cos(6.0 * util) + 2.0 * np.sin(8.0 * dti)
         - 0.012 * (score - 680)])
    p = _sigmoid(z)
    defaulted = (rng.random(n) < p).astype(int)
    df = pd.DataFrame({
        "application_id": [f"A{300000 + i}" for i in range(n)],
        "application_date": date.strftime("%Y-%m-%d"),
        "customer_type": seg,
        "income": income, "credit_score": score.astype(int), "debt_to_income": dti, "utilization": util,
        "months_employed": months, "num_accounts": accounts, "loan_amount": loan, "recent_inquiries": inq,
        "defaulted": defaulted,
    })
    sidecar = {
        "label_column": "defaulted", "segment_column": "customer_type", "n": n,
        "default_rate": float(defaulted.mean()),
        "default_rate_by_segment": {k: float(defaulted[seg == k].mean()) for k in TYPES},
        "mean_true_pd_by_segment": {k: float(p[seg == k].mean()) for k in TYPES},
        "generator": "samples_g5.generate_segmented (seeded)",
    }
    return df, sidecar


SAMPLES_G5 = {
    "segmented-loans": {
        "name": "Loans by customer type (sample)",
        "filename": "segmented_loans_sample.csv",
        "note": "Generated sample · 30,000 applications from four customer types whose risk is driven differently (G5)",
        "generate": generate_segmented,
        "sidecar": "segments_truth.json",
    },
}
