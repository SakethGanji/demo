"""D3 smoke data: crafted tables with a known answer for the Model Doctor 2 and What's missing (never the samples).

    leak_hinted     a weak credit signal plus `call_duration`, recorded after the call: AUC alone ≈ 0.93 (< 0.97), named
                    like a post-outcome column (the Data Doctor's hint), and dominant in the importance
    cost_run        a moderate binary signal, 20 % positives, trained to c_FN = 5 × c_FP
    flat_ceiling    a clean linear signal with sharp extremes on 20,000 rows: more rows of the same kind do not help
    twins           y = 1[x0 + x1 > 1] (no row within 0.15 of the line) except for brokered applications (30 %),
                    where the label is a coin flip: the Bayes error is exactly 0.3 × 0.5 = 0.15 (TRUTH['twins'])
    hidden_segment  for self-employed applicants (25 %) the outcome follows a hidden variable that is not in the table,
                    and their income is missing 40 % of the time (2 % elsewhere)
    label_overlap   six support topics; 'card_lost' and 'card_stolen' are the same in every feature (the label
                    definitions overlap), the other four are well separated
"""

from __future__ import annotations

import numpy as np
import pandas as pd

TRUTH = {"twins": 0.15}


def _sig(z):
    return 1 / (1 + np.exp(-z))


def leak_hinted(n: int = 4000, seed: int = 301) -> pd.DataFrame:
    r = np.random.default_rng(seed)
    df = pd.DataFrame({f"x{i}": np.round(r.normal(size=n), 4) for i in range(5)})
    df.insert(0, "contact_id", [f"C{seed}-{i:05d}" for i in range(n)])
    y = _sig(0.8 * df.x0 - 0.5 * df.x1 - 1.2) > r.random(n)
    df["call_duration"] = np.round(np.exp(r.normal(5 + 1.3 * y, 0.6))).astype(int)
    df["subscribed"] = np.where(y, "yes", "no")
    return df


def cost_run(n: int = 4000, seed: int = 302) -> pd.DataFrame:
    r = np.random.default_rng(seed)
    df = pd.DataFrame({f"x{i}": np.round(r.normal(size=n), 4) for i in range(6)})
    df.insert(0, "loan_id", [f"L{seed}-{i:05d}" for i in range(n)])
    y = _sig(1.1 * df.x0 - 0.8 * df.x1 + 0.5 * df.x2 * df.x3 - 1.9) > r.random(n)
    df["defaulted"] = np.where(y, "yes", "no")
    return df


def flat_ceiling(n: int = 20000, seed: int = 303) -> pd.DataFrame:
    r = np.random.default_rng(seed)
    df = pd.DataFrame({f"x{i}": np.round(r.normal(size=n), 4) for i in range(4)})
    df.insert(0, "app_id", [f"A{seed}-{i:05d}" for i in range(n)])
    y = _sig(2.5 * (df.x0 + 0.5 * df.x1 - 0.5 * df.x2)) > r.random(n)
    df["defaulted"] = np.where(y, "yes", "no")
    return df


def twins(n: int = 8000, seed: int = 304) -> pd.DataFrame:
    r = np.random.default_rng(seed)
    X = r.random((3 * n, 3))
    X = X[np.abs(X[:, 0] + X[:, 1] - 1) > 0.15][:n]  # a clear margin: the deterministic part has no Bayes error
    df = pd.DataFrame({f"x{i}": np.round(X[:, i], 4) for i in range(3)})
    df.insert(0, "app_id", [f"T{seed}-{i:05d}" for i in range(n)])
    df["channel"] = r.choice(["branch", "broker"], n, p=[0.7, 0.3])
    y = (df.x0 + df.x1 > 1).to_numpy()
    coin = r.random(n) < 0.5
    y = np.where(df.channel == "broker", coin, y)  # brokered look-alikes: the outcome depends on nothing in the table
    df["defaulted"] = np.where(y, "yes", "no")
    return df


def hidden_segment(n: int = 8000, seed: int = 305) -> pd.DataFrame:
    r = np.random.default_rng(seed)
    emp = r.choice(["employed", "self_employed", "retired"], n, p=[0.6, 0.25, 0.15])
    age = np.round(r.normal(42, 11, n)).clip(19, 85)
    util = np.round(r.beta(2, 3, n), 4)
    income = np.round(np.exp(r.normal(10.6, 0.5, n)), -1)
    late = r.poisson(0.6, n)
    hidden = r.normal(size=n)  # e.g. the self-employed's business cash flow: not in the table
    z = 3 * (4 * (util - 0.4) + 1.2 * (late - 0.6) - 0.05 * (age - 42))  # sharp: the table decides these
    z = np.where(emp == "self_employed", 2.2 * hidden - 0.3, z)
    y = _sig(z) > r.random(n)
    miss = np.where(emp == "self_employed", r.random(n) < 0.40, r.random(n) < 0.02)
    income = np.where(miss, np.nan, income)
    return pd.DataFrame({"app_id": [f"H{seed}-{i:05d}" for i in range(n)], "employment": emp, "age": age, "utilisation": util,
                         "income": income, "late_payments": late, "defaulted": np.where(y, "yes", "no")})


def label_overlap(n: int = 6000, seed: int = 306) -> pd.DataFrame:
    r = np.random.default_rng(seed)
    topics = ["card_lost", "card_stolen", "refund", "transfer_failed", "pin_change", "fee_question"]
    centre = {"card_lost": (0, 0), "card_stolen": (0, 0), "refund": (3, 0), "transfer_failed": (0, 3), "pin_change": (-3, 0),
              "fee_question": (0, -3)}
    t = r.choice(topics, n, p=[0.2, 0.2, 0.15, 0.15, 0.15, 0.15])
    c = np.array([centre[v] for v in t], dtype=float)
    df = pd.DataFrame({"f0": np.round(c[:, 0] + r.normal(0, 0.7, n), 4), "f1": np.round(c[:, 1] + r.normal(0, 0.7, n), 4),
                       "f2": np.round(r.normal(size=n), 4), "channel": r.choice(["app", "phone", "web"], n)})
    df.insert(0, "ticket_id", [f"K{seed}-{i:05d}" for i in range(n)])
    df["topic"] = t
    return df
