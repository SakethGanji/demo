"""G8's sample: customer complaints, each tagged with one or more topics (a multi-label target).

`topics` is a ';'-separated list drawn from six topics. All seeded:

    base rate      P(topic k) = σ(a_k + b_k[product] + c_k[channel] + 0.04·(amount_disputed / 100 − 3)·[k ∈ {fees, billing_error}])
    co-occurrence  fees ⇒ billing_error with extra probability 0.35; fraud ⇒ service with extra 0.30 (a complaint about fraud
                   is often also about how it was handled); collections ⇒ fees with extra 0.25
    at least one   a row whose draw is empty gets its most likely topic
    narrative      one or two sentences per topic, from that topic's phrase bank, in a random order, with neutral filler.
                   12% of the time a topic is *not* written out (only the product / channel / amount hint at it), and 10% of
                   rows mention another topic in passing ("no issue with the app itself"), so the text alone is not a lookup.

So the topics overlap (47% of rows carry two or more), labels correlate, and the narrative carries most of the signal with
the structured columns adding some: the case one AutoGluon model per label with a per-label threshold is for.
"""

from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd

TOPICS = ["fees", "fraud", "billing_error", "service", "online_banking", "collections"]

PHRASES: dict[str, list[str]] = {
    "fees": ["I was charged an overdraft fee I never agreed to", "there is a monthly maintenance fee that was never explained",
             "they took a late payment charge even though I paid on time", "an annual fee appeared on my statement",
             "the foreign transaction fee was far higher than advertised", "I want the account charges refunded",
             "a returned item fee was applied twice", "the fee for the transfer was not disclosed"],
    "fraud": ["there are transactions on my card that I did not make", "someone opened an account in my name",
              "my card was cloned and used abroad", "I reported unauthorised withdrawals from my account",
              "a scammer convinced me to send money and the bank did nothing", "a payment I never authorised went out",
              "I received a text about a purchase I never made", "my identity was stolen and used for a loan"],
    "billing_error": ["the statement shows the wrong balance", "I was billed twice for the same purchase",
                      "the interest was calculated on the wrong amount", "my payment was not applied to the account",
                      "the refund never appeared on my statement", "the minimum payment on the bill is wrong",
                      "a direct debit was taken on the wrong date", "the amount on my statement does not match my receipts"],
    "service": ["the staff on the phone were rude and unhelpful", "I waited over an hour on hold",
                "nobody called me back as promised", "the branch manager refused to escalate my case",
                "I was passed between departments five times", "my complaint was closed without an answer",
                "the adviser gave me wrong information", "I have written three letters with no reply"],
    "online_banking": ["I cannot log in to the mobile app", "the website keeps showing an error at checkout",
                       "the app crashes when I try to make a transfer", "my one-time passcode never arrives",
                       "online banking locked me out after the update", "the app shows a blank screen after login",
                       "I could not reset my password online", "the online transfer page times out"],
    "collections": ["a debt collector keeps calling me at work", "I am being chased for a debt I already paid",
                    "collections agents threatened legal action", "they sold my debt without telling me",
                    "I receive collection letters for an account that is not mine", "the recovery team calls me every day",
                    "they reported a default to the credit bureau by mistake", "the collections team refused a payment plan"],
}
FILLER = ["I have been a customer for years.", "Please look into this.", "This has caused me a lot of stress.",
          "I expect a full response.", "I am very disappointed.", "This needs to be fixed.", "I would like an explanation.",
          "Thank you."]
PASSING = {"fees": "the fees themselves are fine", "fraud": "this is not fraud", "billing_error": "the statement itself is correct",
           "service": "the staff were polite", "online_banking": "no issue with the app itself",
           "collections": "I have no debt with you"}

PRODUCTS = ["credit_card", "current_account", "mortgage", "personal_loan"]
CHANNELS = ["web", "phone", "branch", "app"]
#                      fees  fraud  billing service online collections
BASE = np.array([-1.3, -1.5, -1.5, -1.2, -1.6, -2.2])
B_PRODUCT = {"credit_card": [0.3, 0.9, 0.4, 0.0, 0.0, -0.3], "current_account": [0.8, 0.3, -0.2, 0.0, 0.5, -0.8],
             "mortgage": [-0.2, -1.2, 0.6, 0.5, -0.5, 0.2], "personal_loan": [0.0, -0.5, 0.1, 0.0, -0.4, 1.4]}
C_CHANNEL = {"web": [0.0, 0.0, 0.0, -0.3, 0.6, 0.0], "phone": [0.0, 0.2, 0.0, 0.6, -0.4, 0.3],
             "branch": [0.1, -0.2, 0.0, 0.4, -0.8, 0.0], "app": [0.0, 0.1, 0.0, -0.4, 1.1, -0.2]}


def _sigmoid(z: np.ndarray) -> np.ndarray:
    return 1 / (1 + np.exp(-z))


def generate_complaints(n: int = 6_000, seed: int = 42) -> tuple[pd.DataFrame, dict[str, Any]]:
    rng = np.random.default_rng([seed, 8])
    days = np.sort(rng.integers(0, 548, size=n))
    date = pd.Timestamp("2025-01-01") + pd.to_timedelta(days, unit="D")
    product = rng.choice(PRODUCTS, size=n, p=[0.35, 0.30, 0.15, 0.20])
    channel = rng.choice(CHANNELS, size=n, p=[0.30, 0.30, 0.15, 0.25])
    tenure = np.round(rng.gamma(2.0, 3.5, n), 1)
    amount = np.round(np.exp(rng.normal(np.log(180), 1.0, n)), 2)
    z = BASE[None, :] + np.array([B_PRODUCT[p] for p in product]) + np.array([C_CHANNEL[c] for c in channel])
    z[:, [0, 2]] += 0.04 * (amount[:, None] / 100 - 3).clip(-3, 10)
    p = _sigmoid(z)
    Y = rng.random((n, 6)) < p
    Y[:, 2] |= Y[:, 0] & (rng.random(n) < 0.35)
    Y[:, 3] |= Y[:, 1] & (rng.random(n) < 0.30)
    Y[:, 0] |= Y[:, 5] & (rng.random(n) < 0.25)
    empty = ~Y.any(axis=1)
    Y[empty, np.argmax(p[empty], axis=1)] = True

    narratives, topics = [], []
    for i in range(n):
        on = [k for k in range(6) if Y[i, k]]
        sents = []
        for k in on:
            if rng.random() < 0.12 and len(on) > 1:
                continue  # not written out: only the structured columns hint at it
            ph = PHRASES[TOPICS[k]]
            for j in rng.choice(len(ph), size=1 + int(rng.random() < 0.3), replace=False):
                sents.append(ph[j][0].upper() + ph[j][1:] + ".")
        if rng.random() < 0.10:
            off = [k for k in range(6) if not Y[i, k]]
            if off:
                sents.append("To be clear, " + PASSING[TOPICS[int(rng.choice(off))]] + ".")
        rng.shuffle(sents)
        sents += list(rng.choice(FILLER, size=int(rng.integers(0, 3)), replace=False))
        narratives.append(" ".join(sents))
        topics.append(";".join(TOPICS[k] for k in on))

    df = pd.DataFrame({
        "complaint_id": [f"CMP{700000 + i}" for i in range(n)],
        "received_date": date.strftime("%Y-%m-%d"),
        "product": product, "channel": channel,
        "customer_tenure_years": tenure, "amount_disputed": amount,
        "narrative": narratives,
        "topics": topics,
    })
    sidecar = {
        "label_column": "topics", "delimiter": ";", "labels": TOPICS, "n": n,
        "prevalence": {t: float(Y[:, k].mean()) for k, t in enumerate(TOPICS)},
        "multi_share": float((Y.sum(axis=1) >= 2).mean()), "mean_labels": float(Y.sum(axis=1).mean()),
        "generator": "samples_g8.generate_complaints (seeded)",
    }
    return df, sidecar


SAMPLES_G8 = {
    "complaint-topics": {
        "name": "Complaint topics (sample)",
        "filename": "complaint_topics_sample.csv",
        "note": "Generated sample · 6,000 customer complaints, each with one or more of 6 overlapping topics (G8)",
        "generate": generate_complaints,
        "sidecar": "multilabel_truth.json",
    },
}
