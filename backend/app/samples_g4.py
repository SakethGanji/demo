"""G4's sample: loan applications with a surname and a zip code, for the fair-lending check without protected
attributes (BISG). A lender does not record race or ethnicity on most credit products; the check estimates each
applicant's group probabilities from surname × zip instead. To validate that proxy, this sample also carries the
applicant's **true group in `race_ethnicity_true`**: hidden from the model (its role is "protected attribute") and used
only to measure how far the proxy's numbers are from the truth.

Everything is synthetic, surnames and zips included (fairlending.tables' invented stand-in tables). The generator:

    surname s ~ count_s / Σ count                    (the surname table)
    group   r ~ P(r | s)                             (the surname table's shares)
    zip     g ~ P(g | r) = N_g,r / N_r               (the zip table) — so s ⟂ g | r, BISG's assumption
    ln income    = ln 62,000 + a_r + 𝒩(0, 0.45)      a: white 0, black −0.22, hispanic −0.15, api +0.05, aian −0.20, multi −0.05
    credit_score = 695 + b_r + 𝒩(0, 55), in [300, 850]   b: white 0, black −32, hispanic −20, api +6, aian −28, multi −8
    logit PD     = −2.25 − 0.012·(credit_score − 680) + 3.2·(debt_to_income − 0.33) − 0.5·ln(income / 60,000)
                   + 0.04·(loan_amount / 1,000 − 15) − 0.03·employment_years

The default depends on the features only (no group term), so any gap in approvals comes through the features — the
disparate impact a fair-lending review looks for.
"""

from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd

from .fairlending.tables import GROUPS, synthetic_tables

INCOME = {"white": 0.0, "black": -0.22, "hispanic": -0.15, "api": 0.05, "aian": -0.20, "multi": -0.05}
SCORE = {"white": 0, "black": -32, "hispanic": -20, "api": 6, "aian": -28, "multi": -8}


def generate_fair_lending(n: int = 20_000, seed: int = 42) -> tuple[pd.DataFrame, dict[str, Any]]:
    rng = np.random.default_rng([seed, 4])
    t = synthetic_tables()
    sur = t.surname
    s_idx = rng.choice(len(sur), size=n, p=(sur["count"] / sur["count"].sum()).to_numpy())
    P = sur[GROUPS].to_numpy()[s_idx]
    u = rng.random(n)[:, None]
    r_idx = (u > np.cumsum(P, axis=1)).sum(axis=1).clip(max=len(GROUPS) - 1)
    race = np.array(GROUPS)[r_idx]
    pzg = t.p_zip_given_group.fillna(0).to_numpy()  # zips × groups
    zips = t.zip.index.to_numpy()
    z_idx = np.empty(n, dtype=int)
    for k in range(len(GROUPS)):
        m = r_idx == k
        if m.any():
            col = pzg[:, k] / pzg[:, k].sum()
            z_idx[m] = rng.choice(len(zips), size=int(m.sum()), p=col)
    days = np.sort(rng.integers(0, 730, size=n))
    date = pd.Timestamp("2023-01-01") + pd.to_timedelta(days, unit="D")
    income = np.round(np.exp(np.log(62_000) + np.vectorize(INCOME.get)(race) + rng.normal(0, 0.45, n)), -2)
    score = np.clip(695 + np.vectorize(SCORE.get)(race) + rng.normal(0, 55, n), 300, 850).round()
    dti = np.clip(rng.beta(3, 6, n) + 0.04 * (np.vectorize(SCORE.get)(race) < 0), 0.02, 0.95).round(3)
    loan = np.round(np.clip(np.exp(rng.normal(np.log(15_000), 0.5, n)), 1_000, 80_000), -2)
    emp = np.clip(rng.gamma(2.0, 3.0, n), 0, 40).round(1)
    home = rng.choice(["rent", "mortgage", "own"], size=n, p=[0.42, 0.40, 0.18])
    logit = (-2.25 - 0.012 * (score - 680) + 3.2 * (dti - 0.33) - 0.5 * np.log(income / 60_000)
             + 0.04 * (loan / 1_000 - 15) - 0.03 * emp + 0.15 * (home == "rent"))
    pd_true = 1 / (1 + np.exp(-logit))
    defaulted = (rng.random(n) < pd_true).astype(int)
    df = pd.DataFrame({
        "application_id": [f"A{300000 + i}" for i in range(n)],
        "application_date": date.strftime("%Y-%m-%d"),
        "surname": sur.index.to_numpy()[s_idx].astype(str),
        "zip": zips[z_idx],
        "income": income, "credit_score": score.astype(int), "debt_to_income": dti, "loan_amount": loan,
        "employment_years": emp, "home_ownership": home,
        "race_ethnicity_true": race,
        "defaulted": defaulted,
    })
    df["surname"] = df["surname"].str.capitalize()
    sidecar = {
        "label_column": "defaulted", "surname_column": "surname", "zip_column": "zip", "truth_column": "race_ethnicity_true",
        "group_shares": {g: float((race == g).mean()) for g in GROUPS}, "default_rate": float(defaulted.mean()), "n": n,
        "tables": "fairlending.tables.synthetic_tables (seed 2024): invented surnames and zips, not Census data",
        "generator": "samples_g4.generate_fair_lending (seeded)",
    }
    return df, sidecar


SAMPLES_G4 = {
    "fair-lending": {
        "name": "Loan applications with surname and zip (sample)",
        "filename": "fair_lending_sample.csv",
        "note": "Generated sample · 20,000 applications with surname, zip and a hidden true group to validate the BISG proxy (G4)",
        "generate": generate_fair_lending,
        "sidecar": "bisg_truth.json",
    },
}
