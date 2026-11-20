"""D1: crafted tables with planted data problems for the `datadoctor` smoke section (seeded; known answers).

    planted(n)        a binary credit table with one of each issue the Data Doctor checks (see PLANTED)
    rare_classes()    a multiclass table whose smallest classes have 8, 12 and 30 rows
    small_sample()    400 rows, 32 defaults, 10 features (events per feature 3.2)
    drifting()        an application date; the default rate rises from 5 % to 25 % over two years
    noisy_labels()    an easy multiclass problem with 10 % of the labels flipped (the flipped rows are returned)

Never edits or imports backend/app/samples*.py.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

# What `planted` plants: check id → (column(s), what).
PLANTED = {
    "leakage": ("review_score", "filled after the outcome: alone it separates the label (AUC ≥ 0.97)"),
    "coded_values": ("months_late", "-2 / -1 codes on ~30 % of rows in a 0–6 count"),
    "disguised_missing": ("days_since_last", "999 on 30 % of rows; the rest is 0–120"),
    "rare_codes": ("education", "codes 0 and 9 (0.5 % each) outside the documented 1–4"),
    "informative_missing": ("income", "missing on 20 % of rows, where the default rate is ~3× higher"),
    "duplicates": ("*", "5 % of rows repeated (new app_id)"),
    "conflicts": ("*", "1.5 % of rows repeated with the other label"),
    "ids_constants": ("app_id, country", "a row ID and a constant"),
    "protected": ("gender, age", "protected attributes"),
    "missing_words": ("employer_type", "'unknown' on 10 % of rows"),
}


def planted(n: int = 40_000, seed: int = 401) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    m = n * 100 // 108  # base rows; duplicates and conflicts are added on top
    debt = rng.gamma(2.0, 0.18, m)
    util = rng.beta(2, 3, m)
    tenure = rng.integers(0, 240, m)
    late = rng.choice(np.arange(0, 7), m, p=[0.55, 0.2, 0.1, 0.06, 0.04, 0.03, 0.02])
    z = -3.0 + 2.2 * debt + 1.8 * util - 0.004 * tenure + 0.45 * late + rng.normal(0, 0.6, m)
    y = (rng.random(m) < 1 / (1 + np.exp(-z))).astype(int)
    df = pd.DataFrame({
        "app_id": np.arange(100_000, 100_000 + m),
        "debt_to_income": debt.round(4), "utilisation": util.round(4), "tenure_months": tenure,
        "months_late": late,
        "days_since_last": rng.integers(0, 121, m),
        "education": rng.choice([1, 2, 3, 4], m, p=[0.3, 0.4, 0.2, 0.1]),
        "income": rng.lognormal(10.6, 0.4, m).round(0),
        "employer_type": rng.choice(["private", "public", "self"], m, p=[0.6, 0.25, 0.15]),
        "gender": rng.choice(["F", "M"], m),
        "age": rng.integers(21, 75, m),
        "country": "US",
        "review_score": np.where(y == 1, rng.normal(80, 6, m), rng.normal(30, 6, m)).round(1),
        "defaulted": y,
    })
    # coded values: on-time payers coded -1, no-use -2 (as in Taiwan's PAY_*)
    paid = (late == 0) & (rng.random(m) < 0.4)
    nouse = (late == 0) & ~paid & (rng.random(m) < 0.3)
    df.loc[paid, "months_late"] = -1
    df.loc[nouse, "months_late"] = -2
    df.loc[rng.random(m) < 0.30, "days_since_last"] = 999
    for code in (0, 9):
        df.loc[rng.random(m) < 0.005, "education"] = code
    # informative missingness: income is missing more often for defaulters
    p_miss = np.where(y == 1, 0.45, 0.15)
    df.loc[rng.random(m) < p_miss, "income"] = np.nan
    df.loc[rng.random(m) < 0.10, "employer_type"] = "unknown"
    # exact duplicates (a resubmitted application gets a new id) and conflicting copies (the other label)
    dup = df.sample(n=int(m * 0.05), random_state=seed).copy()
    dup["app_id"] = np.arange(500_000, 500_000 + len(dup))
    con = df.sample(n=int(m * 0.015), random_state=seed + 1).copy()
    con["app_id"] = np.arange(700_000, 700_000 + len(con))
    con["defaulted"] = 1 - con["defaulted"]
    out = pd.concat([df, dup, con], ignore_index=True)
    return out.sample(frac=1, random_state=seed).reset_index(drop=True)


def rare_classes(seed: int = 402) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    sizes = {"A": 900, "B": 700, "C": 500, "D": 300, "E": 30, "F": 8, "G": 12}
    rows = []
    for i, (k, s) in enumerate(sizes.items()):
        rows.append(pd.DataFrame({"x1": rng.normal(i, 1, s), "x2": rng.normal(-i, 1.5, s), "x3": rng.integers(0, 5, s),
                                  "product_line": k}))
    return pd.concat(rows, ignore_index=True).sample(frac=1, random_state=seed).reset_index(drop=True)


def small_sample(seed: int = 403) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    n = 400
    X = rng.normal(size=(n, 10))
    y = np.zeros(n, int)
    y[np.argsort(-(X[:, 0] + rng.normal(0, 1, n)))[:32]] = 1
    df = pd.DataFrame(X.round(4), columns=[f"f{i}" for i in range(10)])
    df["defaulted"] = y
    return df


def drifting(seed: int = 404) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    n = 6000
    days = np.sort(rng.integers(0, 730, n))
    date = pd.Timestamp("2023-01-01") + pd.to_timedelta(days, "D")
    base = 0.05 + 0.20 * days / 730
    x = rng.normal(size=n)
    y = (rng.random(n) < np.clip(base + 0.03 * x, 0, 1)).astype(int)
    return pd.DataFrame({"application_date": date.strftime("%Y-%m-%d"), "score": x.round(4),
                         "amount": rng.lognormal(8, 0.5, n).round(0), "defaulted": y})


def noisy_labels(seed: int = 405, flip: float = 0.10) -> tuple[pd.DataFrame, list[int]]:
    rng = np.random.default_rng(seed)
    n, k = 5000, 4
    c = rng.integers(0, k, n)
    X = rng.normal(0, 0.6, (n, 3)) + np.stack([np.cos(c * np.pi / 2) * 3, np.sin(c * np.pi / 2) * 3, c * 0.5], 1)
    names = np.array(["Billing", "Fraud", "Account", "Card"])
    y = names[c].astype(object)
    flipped = np.flatnonzero(rng.random(n) < flip)
    for i in flipped:
        y[i] = names[(c[i] + rng.integers(1, k)) % k]
    df = pd.DataFrame(X.round(4), columns=["s1", "s2", "s3"])
    df["queue"] = y
    return df, flipped.tolist()
