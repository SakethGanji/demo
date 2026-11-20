"""D2 crafted data for the smoke sections `ladder` and `featurelab` (never edit backend/app/samples*.py).

simple()      a logistic, additive world: the outcome is a sum of monotone effects. A scorecard (binned, additive) is
              as good as anything, so the ladder must recommend a simpler rung than the stack.
complex()     an additive part over 60 weak columns (easy for linear / scorecard models, slow for trees) plus a sharp
              interaction (x·z sign and a threshold pair, easy for trees, impossible for an additive model). No single
              family gets both, so the stack clearly beats every single model.
ratio()       the outcome depends on debt / income (log-odds linear in log(debt/income)); debt and income alone are
              weak and wide-ranging, so a tree needs the ratio. Feature Lab must find `debt_per_income`.
leaky()       `amount_paid` / `amount_billed` is (almost) the label: paid in full ↔ no default. Each column alone is
              weak, the ratio alone has AUC ≈ 0.99. Feature Lab must reject the ratio by the leakage check.
"""

from __future__ import annotations

import numpy as np
import pandas as pd


def _sig(z):
    return 1 / (1 + np.exp(-z))


def simple(n: int = 2500, seed: int = 201) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    age = rng.integers(21, 70, n)
    income = rng.lognormal(10.5, 0.5, n).round(0)
    utilisation = rng.uniform(0, 1, n).round(3)
    late = rng.poisson(0.6, n)
    tenure = rng.integers(0, 30, n)
    region = rng.choice(["north", "south", "east", "west"], n)
    z = -1.4 + 1.6 * (utilisation - 0.5) + 0.55 * late - 0.03 * (tenure - 10) - 0.5 * (np.log(income) - 10.5) - 0.01 * (age - 45)
    y = rng.random(n) < _sig(z)
    return pd.DataFrame({"customer_id": np.arange(1, n + 1), "age": age, "income": income, "utilisation": utilisation,
                         "months_late": late, "tenure_years": tenure, "region": region,
                         "defaulted": np.where(y, "yes", "no")})


def complex(n: int = 9000, seed: int = 202, k: int = 60, w: float = 0.25) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    A = rng.normal(0, 1, (n, k))
    x, zc = rng.normal(0, 1, n), rng.normal(0, 1, n)
    u, v = rng.uniform(0, 1, n), rng.uniform(0, 1, n)
    inter = 2.2 * np.sign(x * zc) + 2.0 * ((u > 0.7) & (v < 0.3))
    logit = w * A.sum(axis=1) + inter - 0.4
    y = rng.random(n) < _sig(logit)
    df = pd.DataFrame(A.round(4), columns=[f"s{i + 1:02d}" for i in range(k)])
    df["x"], df["z"], df["u"], df["v"] = x.round(4), zc.round(4), u.round(4), v.round(4)
    df["churned"] = np.where(y, "yes", "no")
    return df


def ratio(n: int = 8000, seed: int = 203) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    income = rng.lognormal(10.8, 2.2, n).round(0)
    dti = rng.lognormal(-1.2, 0.4, n)
    debt = (income * dti).round(0)
    age = rng.integers(21, 70, n)
    late = rng.poisson(0.8, n)
    noise = rng.normal(0, 1, (n, 2))
    z = -1.6 + 8 * (np.log(dti) + 1.2) + 2.5 * (late - 0.8) - 0.015 * (age - 45)
    y = rng.random(n) < _sig(z)
    return pd.DataFrame({"income": income, "debt": debt, "age": age, "months_late": late,
                         "n1": noise[:, 0].round(3), "n2": noise[:, 1].round(3),
                         "defaulted": np.where(y, "yes", "no")})


def leaky(n: int = 6000, seed: int = 204) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    billed = rng.lognormal(7.5, 1.0, n).round(2)
    age = rng.integers(21, 70, n)
    util = rng.uniform(0, 1, n)
    y = rng.random(n) < _sig(-1.2 + 1.2 * (util - 0.5) - 0.01 * (age - 45))
    share = np.where(y, rng.uniform(0.0, 0.45, n), rng.uniform(0.55, 1.0, n))
    flip = rng.random(n) < 0.01
    share = np.where(flip, 1 - share, share)
    paid = (billed * share).round(2)
    return pd.DataFrame({"amount_billed": billed, "amount_paid": paid, "age": age, "utilisation": util.round(3),
                         "defaulted": np.where(y, "yes", "no")})


def dsl(n: int = 3000, seed: int = 205) -> pd.DataFrame:
    """Every DSL op has something to read: an entity that repeats, a date, two categories, two amounts."""
    rng = np.random.default_rng(seed)
    cust = rng.integers(1, 400, n)
    when = pd.Timestamp("2025-01-01") + pd.to_timedelta(rng.integers(0, 365 * 24, n), unit="h")
    amount = rng.lognormal(5, 1, n).round(2)
    limit = rng.lognormal(7, 0.5, n).round(0)
    channel = rng.choice(["web", "branch", "phone"], n)
    product = rng.choice(["card", "loan"], n)
    y = rng.random(n) < _sig(-1 + 2 * (amount / limit) + 0.4 * (channel == "web"))
    return pd.DataFrame({"customer_id": cust, "booked_at": when.strftime("%Y-%m-%d %H:%M"), "amount": amount, "limit": limit,
                         "channel": channel, "product": product, "flagged": np.where(y, "yes", "no")})
