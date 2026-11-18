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
    """6,000 card transactions from 420 cards over 90 days. Each card has its own spending level (a log-normal
    multiplier), so an amount is unusual only relative to that card's history. Columns: txn_id, card_id, timestamp,
    amount_usd, avg_amount_90d (the card's mean amount, as a bank's feature store would supply it),
    merchant_category, channel, card_present, foreign, hour, distance_from_home_km, txns_last_24h (from the card's
    own timestamps), account_age_days (per card).

    Planted anomalies (1.5%, five kinds, 18 each): amount_spike (15–40× the card's own level: in the global range of
    ordinary travel or electronics purchases, so only a group-relative feature sees it), night_foreign, velocity (a
    burst minutes after the card's previous transaction), new_account_large (a new card's first purchase), far_atm.
    """
    rng = np.random.default_rng(seed + 9)
    n_cards = 420
    level = np.exp(rng.normal(0, 0.75, size=n_cards))  # the card's spending multiplier
    rate = rng.gamma(2.0, 1.0, size=n_cards)
    card = rng.choice(n_cards, size=n, p=rate / rate.sum())
    age_card = np.round(rng.gamma(3.0, 500, size=n_cards)).astype(int) + 30
    home_dist = rng.gamma(1.4, 6, size=n_cards)
    mcc = rng.choice(MCC, size=n, p=MCC_P)
    channel = np.where(mcc == "cash", "atm", np.where(np.isin(mcc, ["online retail", "utilities"]), "online",
                       rng.choice(["chip", "contactless"], size=n, p=[0.4, 0.6])))
    base = {"grocery": 55, "fuel": 48, "restaurants": 38, "travel": 420, "electronics": 260, "online retail": 70,
            "utilities": 110, "pharmacy": 25, "cash": 120}
    amount = np.round(np.array([base[m] for m in mcc]) * level[card] * np.exp(rng.normal(0, 0.45, size=n)), 2)
    hour = np.clip(np.round(rng.normal(14, 4, size=n)), 6, 23).astype(int)
    day = rng.integers(0, 90, size=n)
    minute = rng.integers(0, 60, size=n)
    start = pd.Timestamp("2026-08-17")
    ts = start + pd.to_timedelta(day, unit="D") + pd.to_timedelta(hour, unit="h") + pd.to_timedelta(minute, unit="m")
    ts = pd.Series(ts)
    distance_km = np.round(np.abs(home_dist[card] * np.exp(rng.normal(0, 0.6, size=n))), 1)
    foreign = (rng.random(n) < 0.04).astype(int)
    card_present = np.isin(channel, ["chip", "contactless", "atm"]).astype(int)
    account_age_days = age_card[card].copy()
    card_id = np.array([f"C-{1000 + c}" for c in card], dtype=object)

    k = int(round(share * n))
    idx = rng.choice(n, size=k, replace=False)
    kinds = np.array(["amount_spike", "night_foreign", "velocity", "new_account_large", "far_atm"])[np.arange(k) % 5]
    # the card's typical amount, before any anomaly is planted
    typical = pd.Series(amount).groupby(card).median()
    new_card = 0
    for i, kind in zip(idx, kinds):
        c = card[i]
        if kind == "amount_spike":
            amount[i] = round(float(typical[c]) * rng.uniform(15, 40), 2)
        elif kind == "night_foreign":
            hour[i] = int(rng.integers(1, 5))
            ts.iloc[i] = ts.iloc[i].normalize() + pd.Timedelta(hours=int(hour[i]), minutes=int(minute[i]))
            foreign[i] = 1
            distance_km[i] = round(rng.uniform(800, 6000), 1)
            channel[i] = "online"
            card_present[i] = 0
        elif kind == "velocity":
            same = np.where((card == c) & (np.arange(n) != i))[0]
            if len(same):
                prev = ts.iloc[same].min()
                ts.iloc[i] = prev + pd.Timedelta(minutes=int(rng.integers(1, 6)))
                hour[i] = ts.iloc[i].hour
            amount[i] = round(amount[i] * rng.uniform(1.5, 3), 2)
        elif kind == "new_account_large":
            card_id[i] = f"C-N{new_card:03d}"
            new_card += 1
            account_age_days[i] = int(rng.integers(1, 10))
            amount[i] = round(rng.uniform(2500, 9000), 2)
            mcc[i] = "electronics"
            channel[i] = "online"
            card_present[i] = 0
        elif kind == "far_atm":
            mcc[i] = "cash"
            channel[i] = "atm"
            card_present[i] = 1
            distance_km[i] = round(rng.uniform(300, 1500), 1)
            amount[i] = round(rng.uniform(800, 2000), 2)
            hour[i] = int(rng.integers(0, 5))
            ts.iloc[i] = ts.iloc[i].normalize() + pd.Timedelta(hours=int(hour[i]), minutes=int(minute[i]))
    df = pd.DataFrame({
        "txn_id": [f"T-{900000 + i}" for i in range(n)], "card_id": card_id,
        "timestamp": ts.dt.strftime("%Y-%m-%dT%H:%M:%S").to_numpy(), "amount_usd": amount,
        "merchant_category": mcc, "channel": channel, "card_present": card_present, "foreign": foreign,
        "hour": hour, "distance_from_home_km": distance_km, "account_age_days": account_age_days,
    })
    # features a card system computes from the card's own history (normal behaviour; anomalies are not averaged in)
    planted = np.zeros(n, dtype=bool)
    planted[idx] = True
    normal_mean = df.loc[~planted].groupby("card_id")["amount_usd"].mean()
    df["avg_amount_90d"] = df["card_id"].map(normal_mean).fillna(0.0).round(2)
    t = pd.to_datetime(df["timestamp"])
    order = np.lexsort((t.to_numpy(), df["card_id"].to_numpy()))
    cnt = np.zeros(n, dtype=int)
    for cid, rows in pd.Series(order).groupby(df["card_id"].to_numpy()[order]):
        rr = rows.to_numpy()
        tt = t.to_numpy()[rr]
        lo = np.searchsorted(tt, tt - np.timedelta64(24, "h"), side="left")
        cnt[rr] = np.arange(len(rr)) - lo + 1
    df["txns_last_24h"] = cnt
    for i, kind in zip(idx, kinds):
        if kind == "velocity":
            df.loc[i, "txns_last_24h"] = int(rng.integers(25, 60))
    # a bank export: sorted by time; keep track of where the planted rows went
    perm = np.argsort(t.to_numpy(), kind="stable")
    df = df.iloc[perm].reset_index(drop=True)
    df["txn_id"] = [f"T-{900000 + i}" for i in range(n)]
    new_pos = np.empty(n, dtype=int)
    new_pos[perm] = np.arange(n)
    cols = ["txn_id", "card_id", "timestamp", "amount_usd", "avg_amount_90d", "merchant_category", "channel",
            "card_present", "foreign", "hour", "distance_from_home_km", "txns_last_24h", "account_age_days"]
    df = df[cols]
    rows = new_pos[idx]
    o = np.argsort(rows)
    sidecar = {
        "dataset": "card-transactions", "seed": seed, "rows": n, "label_column": None,
        "entity_column": "card_id", "time_column": "timestamp", "relative_columns": ["amount_usd"],
        "anomalies": {"rows": [int(r) for r in rows[o]], "kinds": [str(x) for x in kinds[o]], "share": k / n,
                      "note": "ground truth for the sample only; never given to a model"},
    }
    return df, sidecar
