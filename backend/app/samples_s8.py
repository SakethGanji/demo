"""S8 samples: daily transaction volume per branch/product (forecasting) and card transactions with planted
anomalies (unsupervised anomaly detection). Seeded, so every start generates the same files.

Forecasting: long format (item_id, timestamp, target) with known covariates (holiday, payday), a static feature per
series (segment, constant within each item) and 28 future rows per series whose target is empty: the known covariates
of the forecast horizon. Their signal (trend, weekly and yearly seasonality, holiday dips, payday lifts) is in the
sidecar.

Anomalies: card transactions with no label. 1.5% of rows are planted anomalies of five kinds; which rows they are is
only in the sidecar (`anomaly_truth.json`), which no model reads: the UI uses it for an honest precision@k check on
the sample only.
"""

from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd

FORECAST_START = "2024-11-18"
FORECAST_DAYS = 730
FORECAST_FUTURE = 28

SERIES = [
    # item_id, segment, base level, trend per year (share), weekly amplitude, yearly amplitude, weekend factor
    ("branch-north", "retail", 1800.0, 0.08, 0.18, 0.10, 0.45),
    ("branch-south", "retail", 1350.0, 0.03, 0.20, 0.14, 0.40),
    ("branch-city", "retail", 2600.0, 0.12, 0.25, 0.06, 0.30),
    ("cards-online", "digital", 5200.0, 0.22, 0.08, 0.16, 1.05),
    ("business-desk", "business", 640.0, 0.05, 0.10, 0.08, 0.15),
    ("payroll-ach", "business", 980.0, 0.06, 0.05, 0.04, 0.10),
]


def _holidays(dates: pd.DatetimeIndex) -> np.ndarray:
    """Fixed-date and rule-based bank holidays (a generic calendar, not any one country's)."""
    md = set()
    for y in sorted(set(dates.year)):
        md |= {pd.Timestamp(y, 1, 1), pd.Timestamp(y, 7, 4), pd.Timestamp(y, 12, 25), pd.Timestamp(y, 12, 26),
               pd.Timestamp(y, 11, 11)}
        # last Monday of May, first Monday of September, fourth Thursday of November
        may = pd.date_range(f"{y}-05-01", f"{y}-05-31")
        md.add(may[may.dayofweek == 0][-1])
        sep = pd.date_range(f"{y}-09-01", f"{y}-09-30")
        md.add(sep[sep.dayofweek == 0][0])
        nov = pd.date_range(f"{y}-11-01", f"{y}-11-30")
        md.add(nov[nov.dayofweek == 3][3])
        # Good Friday (anonymous Gregorian algorithm)
        a, b, c = y % 19, y // 100, y % 100
        d, e = b // 4, b % 4
        f = (b + 8) // 25
        g = (b - f + 1) // 3
        h = (19 * a + b - d - g + 15) % 30
        i, k = c // 4, c % 4
        l_ = (32 + 2 * e + 2 * i - h - k) % 7
        m = (a + 11 * h + 22 * l_) // 451
        month = (h + l_ - 7 * m + 114) // 31
        day = ((h + l_ - 7 * m + 114) % 31) + 1
        md.add(pd.Timestamp(y, month, day) - pd.Timedelta(days=2))
    return np.array([t in md for t in dates], dtype=int)


def _paydays(dates: pd.DatetimeIndex) -> np.ndarray:
    """The 15th and the last business day of each month."""
    s = pd.Series(dates)
    last_bd = s.groupby([s.dt.year, s.dt.month]).transform(lambda x: x[x.dt.dayofweek < 5].max())
    return ((s.dt.day == 15) | (s == last_bd)).to_numpy().astype(int)


def generate_transaction_volume(seed: int = 42) -> tuple[pd.DataFrame, dict[str, Any]]:
    rng = np.random.default_rng(seed + 8)
    dates = pd.date_range(FORECAST_START, periods=FORECAST_DAYS + FORECAST_FUTURE, freq="D")
    hol = _holidays(dates)
    pay = _paydays(dates)
    t = np.arange(len(dates))
    doy = dates.dayofyear.to_numpy()
    dow = dates.dayofweek.to_numpy()
    frames = []
    for item, segment, base, trend, wk, yr, weekend in SERIES:
        level = base * (1 + trend * t / 365.0)
        weekly = 1 + wk * np.sin(2 * np.pi * (dow + 1) / 7)
        weekly = np.where(dow >= 5, weekend, weekly)
        yearly = 1 + yr * np.sin(2 * np.pi * (doy - 80) / 365.25) + 0.5 * yr * np.cos(4 * np.pi * doy / 365.25)
        holiday = np.where(hol == 1, 0.35 if segment != "digital" else 1.25, 1.0)
        payday = np.where(pay == 1, 1.35 if segment != "digital" else 1.15, 1.0)
        # December peak for card spend
        dec = 1 + (0.25 if segment == "digital" else 0.08) * np.exp(-((doy - 352) / 9.0) ** 2)
        mean = level * weekly * yearly * holiday * payday * dec
        noise = rng.normal(0, 0.05, size=len(t)) + np.convolve(rng.normal(0, 0.02, size=len(t)), np.ones(5) / 5, "same")
        y = np.round(np.maximum(mean * (1 + noise), 0)).astype(float)
        y[FORECAST_DAYS:] = np.nan  # the horizon: covariates known, target not
        frames.append(pd.DataFrame({"item_id": item, "timestamp": dates.strftime("%Y-%m-%d"), "volume": y,
                                    "holiday": hol, "payday": pay, "segment": segment}))
    df = pd.concat(frames, ignore_index=True)
    sidecar = {
        "dataset": "transaction-volume", "seed": seed, "rows": len(df), "label_column": None,
        "forecast": {"item_id": "item_id", "timestamp": "timestamp", "target": "volume",
                     "known_covariates": ["holiday", "payday"], "static_features": ["segment"],
                     "freq": "D", "history_days": FORECAST_DAYS, "future_rows_per_item": FORECAST_FUTURE,
                     "suggested_prediction_length": 28},
        "signal": "y = level·(1 + trend·t/365) · weekly(dow) · yearly(doy) · holiday · payday · December peak · (1 + ε)",
        "series": [{"item_id": s[0], "segment": s[1], "base": s[2], "trend_per_year": s[3]} for s in SERIES],
    }
    return df, sidecar


# --------------------------------------------------------------------------- anomalies

MCC = ["grocery", "fuel", "restaurants", "travel", "electronics", "online retail", "utilities", "pharmacy", "cash"]
MCC_P = [0.24, 0.12, 0.16, 0.04, 0.05, 0.22, 0.08, 0.06, 0.03]
CHANNELS = ["chip", "contactless", "online", "atm"]


def generate_card_transactions(n: int = 6_000, seed: int = 42, share: float = 0.015) -> tuple[pd.DataFrame, dict[str, Any]]:
    rng = np.random.default_rng(seed + 9)
    mcc = rng.choice(MCC, size=n, p=MCC_P)
    channel = np.where(mcc == "cash", "atm", np.where(np.isin(mcc, ["online retail", "utilities"]), "online",
                       rng.choice(["chip", "contactless"], size=n, p=[0.4, 0.6])))
    base = {"grocery": 55, "fuel": 48, "restaurants": 38, "travel": 420, "electronics": 260, "online retail": 70,
            "utilities": 110, "pharmacy": 25, "cash": 120}
    amount = np.round(np.array([base[m] for m in mcc]) * np.exp(rng.normal(0, 0.55, size=n)), 2)
    hour = np.clip(np.round(rng.normal(14, 4, size=n)), 6, 23).astype(int)
    distance_km = np.round(np.abs(rng.gamma(1.4, 6, size=n)), 1)
    txn_24h = rng.poisson(2.2, size=n) + 1
    account_age_days = np.round(rng.gamma(3.0, 500, size=n)).astype(int) + 30
    avg_amount_90d = np.round(amount * np.exp(rng.normal(0, 0.3, size=n)), 2)
    foreign = (rng.random(n) < 0.04).astype(int)
    card_present = np.isin(channel, ["chip", "contactless", "atm"]).astype(int)

    k = int(round(share * n))
    idx = rng.choice(n, size=k, replace=False)
    kinds = np.array(["amount_spike", "night_foreign", "velocity", "new_account_large", "far_atm"])[np.arange(k) % 5]
    for i, kind in zip(idx, kinds):
        if kind == "amount_spike":
            amount[i] = round(avg_amount_90d[i] * rng.uniform(15, 40), 2)
        elif kind == "night_foreign":
            hour[i] = int(rng.integers(1, 5))
            foreign[i] = 1
            distance_km[i] = round(rng.uniform(800, 6000), 1)
            channel[i] = "online"
            card_present[i] = 0
        elif kind == "velocity":
            txn_24h[i] = int(rng.integers(25, 60))
            amount[i] = round(amount[i] * rng.uniform(1.5, 3), 2)
        elif kind == "new_account_large":
            account_age_days[i] = int(rng.integers(1, 10))
            amount[i] = round(rng.uniform(2500, 9000), 2)
            mcc[i] = "electronics"
        elif kind == "far_atm":
            mcc[i] = "cash"
            channel[i] = "atm"
            card_present[i] = 1
            distance_km[i] = round(rng.uniform(300, 1500), 1)
            amount[i] = round(rng.uniform(800, 2000), 2)
            hour[i] = int(rng.integers(0, 5))
    df = pd.DataFrame({
        "txn_id": [f"T-{900000 + i}" for i in range(n)], "amount_usd": amount, "avg_amount_90d": avg_amount_90d,
        "merchant_category": mcc, "channel": channel, "card_present": card_present, "foreign": foreign,
        "hour": hour, "distance_from_home_km": distance_km, "txns_last_24h": txn_24h,
        "account_age_days": account_age_days,
    })
    order = np.argsort(idx)
    sidecar = {
        "dataset": "card-transactions", "seed": seed, "rows": n, "label_column": None,
        "anomalies": {"rows": [int(r) for r in idx[order]], "kinds": [str(x) for x in kinds[order]], "share": k / n,
                      "note": "ground truth for the sample only; never given to a model"},
    }
    return df, sidecar
