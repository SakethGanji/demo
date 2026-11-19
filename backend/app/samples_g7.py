"""G7's sample: every credit application that came through the door, approved or declined, for reject inference.

Only approved applicants have an outcome (`defaulted` is blank for declined ones); the outcome every declined
applicant *would* have had is drawn too and kept out of the table, in the sidecar (`reject_truth.json`), so a
"through-the-door" holdout can check what a model would do on everyone who applies. All seeded:

    true risk      logit PD = −2.05 − 0.013·(credit_score − 670) + 2.8·(debt_to_income − 0.35) + 1.3·(utilization − 0.45)
                              − 0.35·ln(income / 52,000) + 0.30·num_delinquencies + 0.12·recent_inquiries
                              − 0.004·(months_employed − 60) + 0.25·[home_ownership = rent] + 0.25·[channel = broker]
                              + 0.85·u,          u ~ N(0, 1) not recorded (what the underwriter learns at interview)
    old policy     approve ⟺ credit_score ≥ 560  and  L + 0.75·u + ε < c,  ε ~ N(0, 0.45)
                   L = 0.016·(670 − credit_score) + 2.2·debt_to_income + 0.45·num_delinquencies   (the legacy scorecard)
                   c = the 62nd percentile of L + 0.75·u + ε among applicants above the score floor

So the declines are *not* missing at random: the underwriter also saw u, which raises risk and is not in the table. An
applicant who was approved despite a weak profile had a good u, so approved-only data understates the risk of weak
profiles, and nobody below 560 was approved at all. That is the case reject inference is for.
"""

from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd


def _sigmoid(z: np.ndarray) -> np.ndarray:
    return 1 / (1 + np.exp(-z))


def generate_rejects(n: int = 24_000, seed: int = 42) -> tuple[pd.DataFrame, dict[str, Any]]:
    rng = np.random.default_rng([seed, 7])
    days = np.sort(rng.integers(0, 730, size=n))
    date = pd.Timestamp("2023-01-01") + pd.to_timedelta(days, unit="D")
    channel = rng.choice(["branch", "online", "broker"], size=n, p=[0.35, 0.45, 0.20])
    home = rng.choice(["rent", "mortgage", "own"], size=n, p=[0.42, 0.40, 0.18])
    score = np.clip(rng.normal(665, 72, n), 300, 850).round()
    income = np.round(np.exp(rng.normal(np.log(52_000), 0.48, n)), -2)
    dti = np.clip(rng.beta(3, 6, n) * 0.95, 0.01, 0.9).round(3)
    util = np.clip(rng.beta(2, 2.4, n), 0, 1).round(3)
    months = rng.integers(0, 300, n)
    inq = rng.poisson(1.3, n)
    delinq = rng.poisson(np.clip(0.35 + (650 - score) / 180, 0.05, 2.5))
    loan = np.round(np.exp(rng.normal(np.log(14_000), 0.6, n)), -2)
    u = rng.normal(0, 1, n)

    z = (-2.05 - 0.013 * (score - 670) + 2.8 * (dti - 0.35) + 1.3 * (util - 0.45) - 0.35 * np.log(income / 52_000)
         + 0.30 * delinq + 0.12 * inq - 0.004 * (months - 60) + 0.25 * (home == "rent") + 0.25 * (channel == "broker")
         + 0.85 * u)
    p = _sigmoid(z)
    outcome = (rng.random(n) < p).astype(int)

    legacy = 0.016 * (670 - score) + 2.2 * dti + 0.45 * delinq
    judged = legacy + 0.75 * u + rng.normal(0, 0.45, n)
    floor = score >= 560
    cut = np.quantile(judged[floor], 0.62)
    approved = floor & (judged < cut)

    ids = np.array([f"APP{500000 + i}" for i in range(n)])
    df = pd.DataFrame({
        "application_id": ids,
        "application_date": date.strftime("%Y-%m-%d"),
        "channel": channel, "home_ownership": home,
        "credit_score": score.astype(int), "income": income, "debt_to_income": dti, "utilization": util,
        "months_employed": months, "recent_inquiries": inq, "num_delinquencies": delinq, "loan_amount": loan,
        "decision": np.where(approved, "approved", "declined"),
        "defaulted": pd.array(np.where(approved, outcome, -1), dtype="Int64"),
    })
    df.loc[~approved, "defaulted"] = pd.NA
    sidecar = {
        "label_column": "defaulted", "decision_column": "decision", "id_column": "application_id", "n": n,
        "approval_rate": float(approved.mean()),
        "bad_rate": {"approved": float(outcome[approved].mean()), "declined": float(outcome[~approved].mean()),
                     "through_the_door": float(outcome.mean())},
        "hidden_outcomes": {"what": "the outcome each declined applicant would have had (validation only; never trained on)",
                            "ids": ids[~approved].tolist(), "defaulted": outcome[~approved].tolist()},
        "generator": "samples_g7.generate_rejects (seeded)",
    }
    return df, sidecar


SAMPLES_G7 = {
    "credit-rejects": {
        "name": "Applications with declines (sample)",
        "filename": "credit_rejects_sample.csv",
        "note": "Generated sample · 24,000 applications; outcomes only for the approved, hidden outcomes of the declined kept for validation (G7)",
        "generate": generate_rejects,
        "sidecar": "reject_truth.json",
    },
}
