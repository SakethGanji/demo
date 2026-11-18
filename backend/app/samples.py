"""Seeded generators for the demo datasets (plan §5).

Both are synthetic by design and labelled as samples in the UI. Each generator returns the frame
plus a sidecar dict that records the ground truth a later test can measure against.

- Support tickets: text, six classes, ~20,000 rows. Vocabulary overlaps between classes, Security is
  rare, ~1.5% of rows are exact duplicates of another row (apart from the ticket ID), and a few
  hundred labels are deliberately wrong. The sidecar lists every flipped row.
- Account churn: tabular, two classes, ~10,000 rows. Numeric and categorical columns, ~14% churn,
  driven by a known logistic signal recorded in the sidecar.
"""

from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd

# ---------------------------------------------------------------------------
# Support tickets
# ---------------------------------------------------------------------------

TICKET_CLASSES = ["Billing", "Technical", "Account", "Shipping", "Feature Request", "Security"]
TICKET_SHARES = [0.24, 0.26, 0.18, 0.16, 0.13, 0.03]  # Security is the rare class

# Classes a tired agent mixes up; used to pick a plausible wrong label.
CONFUSABLE = {
    "Billing": ["Account", "Shipping"],
    "Technical": ["Feature Request", "Account"],
    "Account": ["Security", "Billing"],
    "Shipping": ["Billing", "Technical"],
    "Feature Request": ["Technical", "Account"],
    "Security": ["Account", "Technical"],
}

SLOTS = {
    "product": ["the mobile app", "the dashboard", "the web portal", "the desktop client", "the API", "the reports page", "the checkout page", "the admin console"],
    "month": ["January", "February", "March", "April", "May", "June", "July", "August", "September", "October", "November", "December"],
    "amount": ["$19.99", "$49.00", "$120.00", "$310.50", "$9.99", "$75.25", "$1,200.00", "$64.10"],
    "item": ["order", "package", "parcel", "shipment", "delivery", "replacement unit", "return"],
    "carrier": ["UPS", "FedEx", "DHL", "the courier", "USPS"],
    "feature": ["dark mode", "bulk export", "a CSV import", "SSO for our team", "custom fields", "an audit log", "keyboard shortcuts", "scheduled reports", "a Slack integration"],
    "error": ["error 500", "a blank screen", "a timeout", "error 403", "a spinning wheel", "an 'unknown error' banner", "a crash on launch"],
    "days": ["two days", "three days", "a week", "ten days", "since Monday", "over a week"],
    "device": ["iPhone", "Android phone", "Windows laptop", "Mac", "Chromebook", "iPad"],
}

# Intent sentences per class: the main signal.
INTENTS: dict[str, list[str]] = {
    "Billing": [
        "I was charged twice for my {month} invoice.",
        "My card was billed {amount} but my plan should be cheaper.",
        "Can you send a refund for the duplicate charge of {amount}?",
        "The invoice for {month} shows the wrong amount.",
        "Why did my subscription renew at {amount} without notice?",
        "I need a copy of the receipt for my last payment.",
        "The payment failed but the charge still appears on my statement.",
        "Please cancel the upgrade and refund the difference.",
        "Our tax ID is missing from the invoices.",
        "I was billed after I cancelled the subscription.",
    ],
    "Technical": [
        "{product} shows {error} when I try to save.",
        "Since the last update {product} keeps crashing on my {device}.",
        "Sync has been broken for {days} and nothing loads.",
        "I get {error} every time I open {product}.",
        "The export never finishes and {product} freezes.",
        "Notifications stopped working on my {device}.",
        "Pages load very slowly and then show {error}.",
        "The integration returns {error} after the latest release.",
        "Uploads fail halfway with {error}.",
        "Search in {product} returns no results even for exact matches.",
    ],
    "Account": [
        "I can't log in to my account even with the right password.",
        "Please change the email address on my account.",
        "How do I add a new user to our team account?",
        "I want to delete my account and all my data.",
        "My username was changed and I don't recognise it.",
        "The password reset email never arrives.",
        "Can you merge my two accounts into one?",
        "I need to transfer account ownership to a colleague.",
        "My profile settings reset every time I sign in.",
        "Please update the company name on our account.",
    ],
    "Shipping": [
        "My {item} has not arrived after {days}.",
        "Tracking for my {item} hasn't updated in {days}.",
        "{carrier} says the {item} was delivered but I never got it.",
        "The {item} arrived damaged and the box was crushed.",
        "Can I change the delivery address for my {item}?",
        "I received the wrong item in my {item}.",
        "How long does international shipping take?",
        "My {item} is stuck in customs.",
        "{carrier} lost my {item}, what are my options?",
        "Can you expedite the {item}? I need it sooner.",
    ],
    "Feature Request": [
        "It would be great if {product} supported {feature}.",
        "Please add {feature}, our team really needs it.",
        "Is {feature} on the roadmap?",
        "Could you consider adding {feature} to {product}?",
        "We would pay more for {feature}.",
        "Suggestion: let us use {feature} in {product}.",
        "Any plans to bring {feature} to the {device} app?",
        "A feature idea: {feature} would save us hours.",
        "Our admins keep asking for {feature}.",
        "Would love {feature} in the next release.",
    ],
    "Security": [
        "I got a login alert from a location I don't recognise.",
        "Someone may have accessed my account without permission.",
        "I received a phishing email that looks like it came from you.",
        "Two-factor codes are arriving that I did not request.",
        "I think my password was leaked, please lock my account.",
        "There are API keys in our workspace that nobody created.",
        "We need to report a possible vulnerability in {product}.",
        "Unknown devices are signed in to my account.",
        "My account settings were changed by someone else.",
        "Please confirm whether our data was part of a breach.",
    ],
}

# Sentences that could appear in any class: the overlap that keeps the problem from being trivial.
SHARED = [
    "Please help as soon as possible.",
    "This is affecting my whole team.",
    "I already tried logging out and back in.",
    "My account is on the business plan.",
    "Thanks in advance.",
    "I contacted you last week about this too.",
    "This started after the last update.",
    "Can someone call me back?",
    "I'm using {product} on my {device}.",
    "Order and account details are below.",
    "We have been customers for years.",
    "It's urgent because we have a deadline.",
    "Is there any update on this?",
    "Please check my account and the last payment.",
]

# Short subject lines; half the tickets get a generic one instead.
SUBJECTS: dict[str, list[str]] = {
    "Billing": ["Double charge", "Refund request", "Wrong invoice amount", "Billing question", "Payment issue", "Charged after cancel"],
    "Technical": ["App not working", "Error when saving", "Sync broken", "Crash after update", "Slow pages", "Bug report"],
    "Account": ["Can't log in", "Change email", "Account access", "Add a user", "Delete my account", "Password reset"],
    "Shipping": ["Where is my order", "Late delivery", "Damaged package", "Tracking not updating", "Wrong item", "Delivery address"],
    "Feature Request": ["Feature idea", "Request: new feature", "Roadmap question", "Suggestion", "Would love this", "Missing feature"],
    "Security": ["Suspicious login", "Possible breach", "Phishing email", "Unrecognised device", "Account compromised", "Security concern"],
}
GENERIC_SUBJECTS = ["Help needed", "Question", "Urgent", "Issue with my account", "Following up", "Problem"]


def _fill(tpl: str, rng: np.random.Generator) -> str:
    out = tpl
    for key, options in SLOTS.items():
        token = "{" + key + "}"
        while token in out:
            out = out.replace(token, options[rng.integers(len(options))], 1)
    return out


def _ticket_body(label: str, rng: np.random.Generator) -> str:
    parts = [_fill(INTENTS[label][rng.integers(len(INTENTS[label]))], rng)]
    if rng.random() < 0.55:  # a second intent from the same class
        parts.append(_fill(INTENTS[label][rng.integers(len(INTENTS[label]))], rng))
    if rng.random() < 0.5:  # a sentence from a neighbouring class: realistic overlap
        other = CONFUSABLE[label][rng.integers(2)] if rng.random() < 0.7 else TICKET_CLASSES[rng.integers(len(TICKET_CLASSES))]
        parts.append(_fill(INTENTS[other][rng.integers(len(INTENTS[other]))], rng))
    for _ in range(rng.integers(1, 3)):
        parts.append(_fill(SHARED[rng.integers(len(SHARED))], rng))
    order = rng.permutation(len(parts))
    return " ".join(parts[i] for i in order)


def generate_tickets(n: int = 20_000, seed: int = 42, n_flips: int = 300, dup_share: float = 0.015) -> tuple[pd.DataFrame, dict[str, Any]]:
    rng = np.random.default_rng(seed)
    n_dup = int(round(n * dup_share))
    n_base = n - n_dup

    labels = rng.choice(TICKET_CLASSES, size=n_base, p=TICKET_SHARES)
    bodies = [_ticket_body(lbl, rng) for lbl in labels]
    subjects = [
        SUBJECTS[lbl][rng.integers(len(SUBJECTS[lbl]))] if rng.random() < 0.5 else GENERIC_SUBJECTS[rng.integers(len(GENERIC_SUBJECTS))]
        for lbl in labels
    ]
    channel = rng.choice(["email", "chat", "web form", "phone"], size=n_base, p=[0.45, 0.3, 0.17, 0.08])
    tier = rng.choice(["free", "pro", "business", "enterprise"], size=n_base, p=[0.35, 0.3, 0.23, 0.12])
    prio_p = {"Security": [0.05, 0.25, 0.7], "Technical": [0.2, 0.45, 0.35]}
    priority = np.array([rng.choice(["low", "normal", "high"], p=prio_p.get(lbl, [0.35, 0.5, 0.15])) for lbl in labels])

    base = pd.DataFrame({
        "subject": subjects, "body": bodies, "channel": channel, "priority": priority,
        "customer_tier": tier, "category": labels,
    })

    # Plant wrong labels on base rows (never on a row that gets duplicated, so every flip is unambiguous).
    flip_idx = rng.choice(n_base, size=n_flips, replace=False)
    true_labels = base["category"].copy()
    for i in flip_idx:
        lbl = base.at[i, "category"]
        base.at[i, "category"] = CONFUSABLE[lbl][rng.integers(2)] if rng.random() < 0.75 else rng.choice([c for c in TICKET_CLASSES if c != lbl])

    # Exact duplicates of clean rows (a resubmitted ticket gets a new ID but is otherwise identical).
    clean = np.setdiff1d(np.arange(n_base), flip_idx)
    dup_src = rng.choice(clean, size=n_dup, replace=False)
    df = pd.concat([base, base.iloc[dup_src]], ignore_index=True)
    is_flipped = np.zeros(len(df), dtype=bool)
    is_flipped[flip_idx] = True
    truth = pd.concat([true_labels, true_labels.iloc[dup_src]], ignore_index=True)

    order = rng.permutation(len(df))
    df = df.iloc[order].reset_index(drop=True)
    is_flipped = is_flipped[order]
    truth = truth.iloc[order].reset_index(drop=True)
    df.insert(0, "ticket_id", [f"T-{100000 + i}" for i in range(len(df))])

    flipped_rows = np.flatnonzero(is_flipped)
    sidecar = {
        "dataset": "support-tickets",
        "seed": seed,
        "rows": len(df),
        "label_column": "category",
        "classes": TICKET_CLASSES,
        "rare_class": "Security",
        "duplicate_rows": n_dup,
        "n_flipped": int(len(flipped_rows)),
        "flipped": [
            {"row": int(r), "ticket_id": df.at[r, "ticket_id"], "true_label": truth.iat[r], "given_label": df.at[r, "category"]}
            for r in flipped_rows
        ],
    }
    return df, sidecar


# ---------------------------------------------------------------------------
# Account churn
# ---------------------------------------------------------------------------

CHURN_COEFS = {
    "intercept": -2.5,
    "last_login_days": 0.045,
    "tickets_90d": 0.22,
    "tenure_months": -0.035,
    "contract=monthly": 1.1,
    "plan=starter": 0.55,
    "nps": -0.025,
}


def generate_churn(n: int = 10_000, seed: int = 42) -> tuple[pd.DataFrame, dict[str, Any]]:
    rng = np.random.default_rng(seed + 1)
    plan = rng.choice(["starter", "business", "enterprise"], size=n, p=[0.5, 0.35, 0.15])
    region = rng.choice(["na", "emea", "apac", "latam"], size=n, p=[0.42, 0.3, 0.18, 0.1]).astype(object)
    contract = rng.choice(["monthly", "annual"], size=n, p=[0.55, 0.45])
    seat_scale = np.select([plan == "starter", plan == "business"], [4, 25], 120)
    seats = np.maximum(1, rng.poisson(seat_scale)).astype(int)
    price = np.select([plan == "starter", plan == "business"], [15.0, 32.0], 55.0)
    mrr = np.round(seats * price * rng.uniform(0.85, 1.1, size=n), 2)
    tenure = np.clip(rng.gamma(2.0, 12.0, size=n), 1, 120).round().astype(int)
    tickets = rng.poisson(2.2, size=n)
    last_login = np.clip(rng.exponential(9.0, size=n), 0, 120).round().astype(int)
    nps = np.clip(rng.normal(30, 28, size=n), -100, 100).round()

    logit = (
        CHURN_COEFS["intercept"]
        + CHURN_COEFS["last_login_days"] * last_login
        + CHURN_COEFS["tickets_90d"] * tickets
        + CHURN_COEFS["tenure_months"] * tenure
        + CHURN_COEFS["contract=monthly"] * (contract == "monthly")
        + CHURN_COEFS["plan=starter"] * (plan == "starter")
        + CHURN_COEFS["nps"] * nps
    )
    p = 1 / (1 + np.exp(-logit))
    churned = np.where(rng.random(n) < p, "Yes", "No")

    # Missing values: NPS is often not answered, region sometimes unknown.
    nps_out = nps.astype(float)
    nps_out[rng.random(n) < 0.06] = np.nan
    region[rng.random(n) < 0.02] = None

    df = pd.DataFrame({
        "account_id": [f"A-{20000 + i}" for i in range(n)],
        "plan": plan, "region": region, "contract": contract, "seats": seats, "mrr_usd": mrr,
        "tenure_months": tenure, "tickets_90d": tickets, "last_login_days": last_login, "nps": nps_out,
        "churned": churned,
    })
    sidecar = {
        "dataset": "account-churn",
        "seed": seed,
        "rows": n,
        "label_column": "churned",
        "positive_class": "Yes",
        "churn_rate": float((churned == "Yes").mean()),
        "signal": "logit(p) = intercept + Σ coef · feature (nps missing → value before masking)",
        "coefficients": CHURN_COEFS,
        "noise_features": ["region", "seats", "mrr_usd"],
    }
    return df, sidecar


# ---------------------------------------------------------------------------
# Credit default (binary, imbalanced) and loan amount (regression)
# ---------------------------------------------------------------------------

CREDIT_COEFS = {
    "intercept": -4.5,
    "credit_score_per_100": -1.05,   # (credit_score - 680) / 100
    "debt_to_income": 3.2,
    "utilization": 1.6,
    "delinquencies_2y": 0.45,
    "months_employed_per_12": -0.12,
    "employment=unemployed": 1.0,
    "employment=self-employed": 0.35,
    "home=rent": 0.3,
    "purpose=debt consolidation": 0.25,
    "purpose=small business": 0.6,
}


def generate_credit(n: int = 12_000, seed: int = 42) -> tuple[pd.DataFrame, dict[str, Any]]:
    """Loan applicants and whether they defaulted within 12 months. ~8% default (imbalanced)."""
    rng = np.random.default_rng(seed + 2)
    age = np.clip(rng.normal(41, 12, size=n), 19, 80).round().astype(int)
    employment = rng.choice(["salaried", "self-employed", "contract", "unemployed", "retired"], size=n,
                            p=[0.58, 0.14, 0.13, 0.05, 0.10])
    income = np.round(np.exp(rng.normal(10.9, 0.5, size=n)) * np.select(
        [employment == "unemployed", employment == "retired"], [0.35, 0.6], 1.0), -2)
    home = rng.choice(["rent", "mortgage", "own", "other"], size=n, p=[0.42, 0.38, 0.17, 0.03])
    purpose = rng.choice(["debt consolidation", "home improvement", "car", "small business", "education", "medical", "other"],
                         size=n, p=[0.36, 0.17, 0.15, 0.07, 0.08, 0.06, 0.11])
    region = rng.choice(["north", "south", "east", "west", "central"], size=n).astype(object)
    credit_score = np.clip(rng.normal(690, 62, size=n), 450, 850).round().astype(int)
    dti = np.round(np.clip(rng.beta(2.2, 6.0, size=n), 0, 0.95), 3)
    utilization = np.round(np.clip(rng.beta(2.0, 3.2, size=n) + 0.15 * (credit_score < 620), 0, 1.0), 3)
    delinquencies = rng.poisson(np.where(credit_score < 640, 1.1, 0.25))
    months_employed = np.where(np.isin(employment, ["unemployed", "retired"]), 0,
                               np.clip(rng.gamma(1.8, 30, size=n), 0, 480).round()).astype(int)
    loan_amount = np.round(np.clip(income * rng.uniform(0.05, 0.6, size=n), 1000, 250_000), -2)

    logit = (
        CREDIT_COEFS["intercept"]
        + CREDIT_COEFS["credit_score_per_100"] * (credit_score - 680) / 100
        + CREDIT_COEFS["debt_to_income"] * dti
        + CREDIT_COEFS["utilization"] * utilization
        + CREDIT_COEFS["delinquencies_2y"] * delinquencies
        + CREDIT_COEFS["months_employed_per_12"] * np.minimum(months_employed, 120) / 12
        + CREDIT_COEFS["employment=unemployed"] * (employment == "unemployed")
        + CREDIT_COEFS["employment=self-employed"] * (employment == "self-employed")
        + CREDIT_COEFS["home=rent"] * (home == "rent")
        + CREDIT_COEFS["purpose=debt consolidation"] * (purpose == "debt consolidation")
        + CREDIT_COEFS["purpose=small business"] * (purpose == "small business")
    )
    p = 1 / (1 + np.exp(-logit))
    defaulted = np.where(rng.random(n) < p, "Yes", "No")

    income_out = income.astype(float)
    income_out[rng.random(n) < 0.04] = np.nan  # not stated
    months_out = months_employed.astype(float)
    months_out[rng.random(n) < 0.03] = np.nan
    region[rng.random(n) < 0.02] = None

    df = pd.DataFrame({
        "applicant_id": [f"C-{500000 + i}" for i in range(n)],
        "age": age, "employment": employment, "income_usd": income_out, "home_ownership": home,
        "loan_purpose": purpose, "region": region, "loan_amount_usd": loan_amount, "credit_score": credit_score,
        "debt_to_income": dti, "utilization": utilization, "delinquencies_2y": delinquencies,
        "months_employed": months_out, "defaulted": defaulted,
    })
    sidecar = {
        "dataset": "credit-default",
        "seed": seed,
        "rows": n,
        "label_column": "defaulted",
        "positive_class": "Yes",
        "default_rate": float((defaulted == "Yes").mean()),
        "signal": "logit(p) = intercept + Σ coef · feature (values before masking)",
        "coefficients": CREDIT_COEFS,
        "noise_features": ["age", "region", "income_usd", "loan_amount_usd"],
    }
    return df, sidecar


LOAN_COEFS = {
    "intercept": 2_000.0,
    "income_usd": 0.22,
    "credit_score_over_600": 95.0,
    "existing_debt_usd": -0.18,
    "term_months": 210.0,
    "purpose=home improvement": 9_000.0,
    "purpose=small business": 16_000.0,
    "purpose=car": 4_000.0,
    "home=own": 3_500.0,
}


def generate_loan_amount(n: int = 8_000, seed: int = 42, n_missing_labels: int = 40) -> tuple[pd.DataFrame, dict[str, Any]]:
    """Approved loan amount (USD) from applicant features: a regression target with a known signal.

    A few rows have no recorded amount (the label is missing); the engine drops them before fitting."""
    rng = np.random.default_rng(seed + 3)
    income = np.round(np.exp(rng.normal(11.0, 0.45, size=n)), -2)
    credit_score = np.clip(rng.normal(700, 55, size=n), 500, 850).round().astype(int)
    existing_debt = np.round(np.clip(rng.gamma(1.5, 9_000, size=n), 0, 200_000), -1)
    term = rng.choice([12, 24, 36, 48, 60, 72], size=n, p=[0.08, 0.17, 0.3, 0.18, 0.2, 0.07])
    purpose = rng.choice(["debt consolidation", "home improvement", "car", "small business", "education", "other"],
                         size=n, p=[0.33, 0.2, 0.2, 0.08, 0.09, 0.1])
    home = rng.choice(["rent", "mortgage", "own"], size=n, p=[0.4, 0.42, 0.18])
    region = rng.choice(["north", "south", "east", "west", "central"], size=n)
    open_accounts = rng.poisson(4.5, size=n)

    mean = (
        LOAN_COEFS["intercept"]
        + LOAN_COEFS["income_usd"] * income
        + LOAN_COEFS["credit_score_over_600"] * np.maximum(credit_score - 600, 0)
        + LOAN_COEFS["existing_debt_usd"] * existing_debt
        + LOAN_COEFS["term_months"] * term
        + LOAN_COEFS["purpose=home improvement"] * (purpose == "home improvement")
        + LOAN_COEFS["purpose=small business"] * (purpose == "small business")
        + LOAN_COEFS["purpose=car"] * (purpose == "car")
        + LOAN_COEFS["home=own"] * (home == "own")
    )
    noise_sd = 0.12 * np.abs(mean) + 1_500
    amount = np.round(np.clip(mean + rng.normal(0, 1, size=n) * noise_sd, 1_000, None), -2)
    label = amount.astype(float)
    missing_rows = np.sort(rng.choice(n, size=n_missing_labels, replace=False))
    label[missing_rows] = np.nan

    df = pd.DataFrame({
        "application_id": [f"L-{700000 + i}" for i in range(n)],
        "income_usd": income, "credit_score": credit_score, "existing_debt_usd": existing_debt, "term_months": term,
        "purpose": purpose, "home_ownership": home, "region": region, "open_accounts": open_accounts,
        "loan_amount_usd": label,
    })
    sidecar = {
        "dataset": "loan-amount",
        "seed": seed,
        "rows": n,
        "label_column": "loan_amount_usd",
        "problem_type": "regression",
        "signal": "E[amount] = intercept + Σ coef · feature; noise sd = 0.12·|mean| + 1500",
        "coefficients": LOAN_COEFS,
        "noise_features": ["region", "open_accounts"],
        "missing_label_rows": [int(r) for r in missing_rows],
    }
    return df, sidecar


SAMPLES = {
    "support-tickets": {
        "name": "Support tickets (sample)",
        "filename": "support_tickets_sample.csv",
        "note": "Generated sample · text, 6 classes, planted label errors",
        "generate": generate_tickets,
        "sidecar": "label_noise.json",
    },
    "account-churn": {
        "name": "Account churn (sample)",
        "filename": "account_churn_sample.csv",
        "note": "Generated sample · tabular, 2 classes, imbalanced",
        "generate": generate_churn,
        "sidecar": "signal.json",
    },
    "credit-default": {
        "name": "Credit default (sample)",
        "filename": "credit_default_sample.csv",
        "note": "Generated sample · tabular, 2 classes, imbalanced (~8% default)",
        "generate": generate_credit,
        "sidecar": "signal.json",
    },
    "loan-amount": {
        "name": "Loan amount (sample)",
        "filename": "loan_amount_sample.csv",
        "note": "Generated sample · regression, a few rows without a label",
        "generate": generate_loan_amount,
        "sidecar": "signal.json",
    },
}
