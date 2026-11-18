"""Three fresh, bank-flavoured CSVs that no earlier session or sample used, for the final end-to-end walkthrough and
the smoke's `fresh` section. Seeded, so the files (and the numbers in docs/demo-script.md) reproduce.

- card_fraud.csv           binary, rare positives (~1.7% fraud, labelled 1): card-not-present e-commerce authorisations
- complaints.csv           multiclass text: customer complaint narratives routed to one of 7 product teams
- deposit_balances.csv     regression: next-quarter average balance of a deposit account

    python scripts/fresh_data.py [out_dir]     (default: backend/data/fresh)
"""

from __future__ import annotations

import os
import sys

import numpy as np
import pandas as pd


B0 = -1.0  # intercept: ~1.7% fraud


def card_fraud(n: int = 12000, seed: int = 2026) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    merchant = rng.choice(["marketplace", "airline", "gaming", "subscription", "electronics", "fashion", "food delivery", "gift cards"],
                          size=n, p=[0.22, 0.06, 0.07, 0.18, 0.09, 0.16, 0.18, 0.04])
    base = {"marketplace": 45, "airline": 380, "gaming": 25, "subscription": 14, "electronics": 290, "fashion": 70,
            "food delivery": 28, "gift cards": 100}
    amount = np.round(np.array([base[m] for m in merchant]) * np.exp(rng.normal(0, 0.6, n)), 2)
    tenure = np.round(rng.gamma(2.2, 700, n)).astype(int) + 5
    three_ds = rng.random(n) < 0.72
    ip_country_match = rng.random(n) < 0.93
    device_age_days = np.round(rng.gamma(1.5, 120, n)).astype(int)
    attempts_1h = rng.poisson(0.4, n) + 1
    hour = np.clip(np.round(rng.normal(15, 4.5, n)), 0, 23).astype(int)
    billing_shipping_km = np.round(np.abs(rng.gamma(0.8, 25, n)), 1)
    segment = rng.choice(["mass", "affluent", "student", "business"], size=n, p=[0.6, 0.15, 0.12, 0.13])
    # a logistic risk with interactions, then a rare positive class
    z = (B0 + 2.6 * (merchant == "gift cards") + 1.6 * (merchant == "electronics") + 1.2 * (merchant == "gaming")
         + 0.9 * np.log1p(amount / 100) - 2.2 * three_ds + 2.6 * (~ip_country_match) - 0.9 * np.log1p(device_age_days)
         + 1.1 * (attempts_1h - 1) + 1.4 * ((hour <= 4) | (hour >= 23)) + 0.02 * np.minimum(billing_shipping_km, 300)
         - 0.4 * np.log1p(tenure / 100) + 0.7 * (segment == "student"))
    p = 1 / (1 + np.exp(-z))
    fraud = rng.random(n) < p
    df = pd.DataFrame({
        "auth_id": [f"A-{7_000_000 + i}" for i in range(n)], "amount_gbp": amount, "merchant_type": merchant,
        "three_ds_passed": np.where(three_ds, "yes", "no"), "ip_country_matches_card": np.where(ip_country_match, "yes", "no"),
        "device_age_days": device_age_days, "attempts_last_hour": attempts_1h, "local_hour": hour,
        "billing_shipping_distance_km": billing_shipping_km, "customer_tenure_days": tenure, "customer_segment": segment,
        "is_fraud": fraud.astype(int),
    })
    miss = rng.random(n) < 0.03
    df.loc[miss, "device_age_days"] = np.nan
    return df


COMPLAINTS = {
    "Mortgages": ["my mortgage payment went up without notice", "the escrow analysis is wrong and my monthly payment doubled",
                  "remortgage application has been stuck in underwriting for six weeks", "the early repayment charge was never explained",
                  "my fixed rate ended and I was moved to the standard variable rate", "overpayment on my home loan was not applied to the principal"],
    "Credit cards": ["interest was charged even though I paid the full statement balance", "my credit limit was cut without warning",
                     "a purchase I disputed is still on my card statement", "the balance transfer fee was higher than advertised",
                     "the annual fee on my rewards card was charged twice", "cashback points disappeared from my credit card account"],
    "Current accounts": ["my current account was overdrawn because a direct debit was taken early", "an unarranged overdraft fee was charged",
                         "my salary deposit is not showing in my account", "the branch closed my account without telling me",
                         "a standing order I cancelled was still paid", "my debit card was declined although there were funds"],
    "Loans": ["my personal loan application was declined with no reason", "the loan settlement figure is different every time I ask",
              "payment holiday on my car loan was not recorded", "I was sold payment protection insurance with my loan",
              "the loan interest rate is not what was quoted", "a late payment marker on my loan is wrong"],
    "Fraud & security": ["someone used my card details for purchases I did not make", "I was tricked into an authorised push payment scam",
                         "a payee I never set up received money from my account", "my online banking was accessed from another country",
                         "you refused to refund the fraudulent transfers", "I received a text pretending to be the bank and lost money"],
    "Digital banking": ["the mobile app logs me out every time I try to pay", "I cannot reset my online banking password",
                        "the app shows the wrong balance after the update", "two factor authentication codes never arrive",
                        "contactless payments in the app keep failing", "statements are missing from the online banking portal"],
    "Savings & ISAs": ["the ISA transfer from another provider took three months", "the bonus rate on my savings account was not paid",
                       "my fixed term savings matured and the money went to a low rate account", "I exceeded the ISA allowance because of your error",
                       "interest on my easy access saver was calculated wrongly", "withdrawal from my notice account was paid late"],
}
OPENERS = ["I am writing to complain because", "Third time contacting you:", "Please escalate.", "Hello,", "Formal complaint:",
           "I have been a customer for 12 years and", "Nobody on the phone could help.", ""]
CLOSERS = ["I want a refund and an apology.", "Please fix this urgently.", "This has caused me real distress.",
           "I will go to the Financial Ombudsman if this is not resolved.", "Can someone call me back?", ""]


def complaints(n: int = 6000, seed: int = 2027) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    teams = list(COMPLAINTS)
    share = np.array([0.13, 0.2, 0.22, 0.1, 0.12, 0.15, 0.08])
    y = rng.choice(len(teams), size=n, p=share / share.sum())
    rows = []
    for i, k in enumerate(y):
        phrases = COMPLAINTS[teams[k]]
        main = phrases[rng.integers(len(phrases))]
        # 20% mention a second product, so the text is not trivially separable
        other = ""
        if rng.random() < 0.2:
            j = rng.integers(len(teams))
            other = f" Also, {COMPLAINTS[teams[j]][rng.integers(6)]}."
        amt = f" The amount is £{rng.integers(20, 4000):,}." if rng.random() < 0.4 else ""
        text = f"{OPENERS[rng.integers(len(OPENERS))]} {main}.{amt}{other} {CLOSERS[rng.integers(len(CLOSERS))]}".strip()
        rows.append({"complaint_id": f"CMP-{50_000 + i}", "narrative": text,
                     "channel": rng.choice(["web form", "phone", "branch", "email", "social media"], p=[0.35, 0.3, 0.1, 0.18, 0.07]),
                     "customer_age_band": rng.choice(["18-24", "25-34", "35-49", "50-64", "65+"]),
                     "vulnerable_customer": rng.choice(["yes", "no"], p=[0.12, 0.88]),
                     "product_team": teams[k]})
    df = pd.DataFrame(rows)
    # 3% of labels routed to the wrong team, as in a real complaints log
    flip = rng.random(n) < 0.03
    df.loc[flip, "product_team"] = rng.choice(teams, size=int(flip.sum()))
    return df


def deposit_balances(n: int = 7000, seed: int = 2028) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    age = np.clip(np.round(rng.normal(46, 15, n)), 18, 92).astype(int)
    income = np.round(np.exp(rng.normal(10.4, 0.55, n)), -2)
    tenure_m = np.round(rng.gamma(2.0, 40, n)).astype(int) + 1
    product = rng.choice(["easy access", "current", "notice 95", "fixed 1y", "business"], size=n, p=[0.3, 0.35, 0.1, 0.12, 0.13])
    region = rng.choice(["London", "South East", "North West", "Scotland", "Midlands", "Wales"], size=n)
    bal_now = np.round(np.exp(rng.normal(8.3, 1.1, n)) * (1 + (product == "business") * 2.5 + (product == "fixed 1y") * 1.2), 2)
    salary_in = rng.random(n) < np.where(product == "current", 0.75, 0.15)
    rate = np.select([product == "easy access", product == "notice 95", product == "fixed 1y", product == "business"], [3.1, 4.0, 4.6, 1.2], 0.1)
    rate = np.round(rate + rng.normal(0, 0.15, n), 2)
    outflow_ratio = np.clip(rng.beta(2, 5, n), 0, 1).round(3)
    digital_logins_30d = rng.poisson(np.where(age < 40, 22, 9))
    # next quarter's average balance: persistence + income + rate seeking + outflows, multiplicative noise
    y = (bal_now * (0.92 + 0.012 * rate - 0.25 * outflow_ratio) + salary_in * income * 0.09
         + 0.002 * income * np.log1p(tenure_m) + (region == "London") * 600)
    y = np.round(np.maximum(0, y * np.exp(rng.normal(0, 0.12, n))), 2)
    df = pd.DataFrame({
        "account_id": [f"DEP-{300_000 + i}" for i in range(n)], "customer_age": age, "annual_income_gbp": income,
        "tenure_months": tenure_m, "product": product, "region": region, "balance_now_gbp": bal_now,
        "salary_credited": np.where(salary_in, "yes", "no"), "interest_rate_pct": rate, "outflow_ratio_90d": outflow_ratio,
        "digital_logins_30d": digital_logins_30d, "avg_balance_next_q_gbp": y,
    })
    df.loc[rng.random(n) < 0.04, "annual_income_gbp"] = np.nan
    return df


FILES = {"card_fraud.csv": card_fraud, "complaints.csv": complaints, "deposit_balances.csv": deposit_balances}


def write(out_dir: str) -> dict[str, str]:
    os.makedirs(out_dir, exist_ok=True)
    out = {}
    for name, fn in FILES.items():
        path = os.path.join(out_dir, name)
        fn().to_csv(path, index=False)
        out[name] = path
    return out


if __name__ == "__main__":
    here = os.path.dirname(os.path.abspath(__file__))
    d = sys.argv[1] if len(sys.argv) > 1 else os.path.join(here, "..", "backend", "data", "fresh")
    for k, v in write(d).items():
        df = pd.read_csv(v)
        print(f"{k}: {len(df):,} rows × {df.shape[1]} cols → {os.path.relpath(v)}")
