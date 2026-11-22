"""P12's sample: a multi-table credit book (one dataset version, three tables, keys and relations declared).

    applications  one row per application: app_id (key), customer_id, product, amount, income, term_months, applied_on,
                  channel, defaulted (the target, ~9 %)
    bureau        one row per customer the credit bureau knows: customer_id (key), score, n_inquiries_12m,
                  n_open_accounts, oldest_account_years. About 6 % of the applicants have no bureau record, so the
                  relation applications.customer_id → bureau.customer_id has orphans.
    payments      zero to twelve monthly payments per application: payment_id (key), app_id, due_on, paid_on, amount,
                  days_late. Every app_id is an application (no orphans).

Seeded. Default risk rises with a low bureau score, many inquiries, late payments and a high amount / income ratio, so a
model on applications alone does worse than one on a join (P14), which is what the sample is for.
"""

from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd

PRODUCTS = ["personal", "auto", "card", "overdraft"]
CHANNELS = ["branch", "online", "broker"]


def generate_credit_book(n_apps: int = 4_000, seed: int = 42) -> tuple[dict[str, pd.DataFrame], dict[str, Any]]:
    rng = np.random.default_rng(seed)
    n_cust = int(n_apps * 0.85)
    cust = np.array([f"C{i:06d}" for i in range(n_cust)])
    known = rng.random(n_cust) > 0.06  # ~6 % of customers have no bureau record
    score = np.clip(rng.normal(680, 70, n_cust), 300, 850).round().astype(int)
    inq = rng.poisson(1.4, n_cust)
    bureau = pd.DataFrame({"customer_id": cust[known], "score": score[known], "n_inquiries_12m": inq[known],
                           "n_open_accounts": rng.poisson(3, n_cust)[known],
                           "oldest_account_years": np.round(rng.gamma(3, 3, n_cust), 1)[known]})
    ci = rng.integers(0, n_cust, n_apps)
    product = rng.choice(PRODUCTS, n_apps, p=[0.4, 0.25, 0.25, 0.1])
    income = np.round(rng.lognormal(10.6, 0.45, n_apps), -2)
    amount = np.round(np.clip(income * rng.uniform(0.05, 0.6, n_apps), 500, None), -1)
    term = rng.choice([12, 24, 36, 48, 60], n_apps)
    applied = pd.Timestamp("2024-01-01") + pd.to_timedelta(rng.integers(0, 540, n_apps), unit="D")
    app_id = np.array([f"A{i:07d}" for i in range(n_apps)])
    n_pay = np.minimum(rng.integers(0, 13, n_apps), np.ceil((pd.Timestamp("2025-07-01") - applied).days / 30).astype(int))
    late_bias = rng.gamma(1.0, 2.0, n_apps)
    rows = []
    for i in range(n_apps):
        for k in range(int(n_pay[i])):
            due = applied[i] + pd.DateOffset(months=k + 1)
            late = int(max(0, rng.normal(late_bias[i] - 2, 4)))
            rows.append((app_id[i], due.date().isoformat(), (due + pd.Timedelta(days=late)).date().isoformat(),
                         round(float(amount[i]) / float(term[i]) * (1 + rng.normal(0, 0.02)), 2), late))
    payments = pd.DataFrame(rows, columns=["app_id", "due_on", "paid_on", "amount", "days_late"])
    payments.insert(0, "payment_id", [f"P{i:08d}" for i in range(len(payments))])
    sc = np.where(known[ci], score[ci], 640)
    z = (-2.6 + 0.012 * (650 - sc) + 0.25 * inq[ci] + 1.6 * (amount / income - 0.3) + 0.18 * late_bias
         + np.where(product == "card", 0.3, 0.0) + rng.normal(0, 0.6, n_apps))
    defaulted = (rng.random(n_apps) < 1 / (1 + np.exp(-z))).astype(int)
    applications = pd.DataFrame({"app_id": app_id, "customer_id": cust[ci], "product": product, "amount": amount,
                                 "income": income, "term_months": term, "applied_on": [d.date().isoformat() for d in applied],
                                 "channel": rng.choice(CHANNELS, n_apps, p=[0.3, 0.55, 0.15]), "defaulted": defaulted})
    tables = {"applications": applications, "bureau": bureau, "payments": payments}
    sidecar = {"label_column": "defaulted", "primary_table": "applications",
               "keys": {"applications": ["app_id"], "bureau": ["customer_id"], "payments": ["payment_id"]},
               "relations": [{"name": "applications_bureau", "from": {"table": "applications", "columns": ["customer_id"]},
                              "to": {"table": "bureau", "columns": ["customer_id"]}, "cardinality": "n:1"},
                             {"name": "payments_applications", "from": {"table": "payments", "columns": ["app_id"]},
                              "to": {"table": "applications", "columns": ["app_id"]}, "cardinality": "n:1"}],
               "planted": {"no_bureau_share": round(float(1 - known.mean()), 4),
                           "signal": "low score, inquiries, amount/income, late payments (the payments table)"}}
    return tables, sidecar


SAMPLES_P12 = {
    "credit-book": {
        "name": "Credit book (sample, 3 tables)",
        "filename": "credit_book_sample.xlsx",
        "note": "Generated sample · 3 tables (applications, bureau, payments) with keys and relations; 2 classes (~9% default)",
        "generate": generate_credit_book,
        "sidecar": "tables.json",
    },
}
