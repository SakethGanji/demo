"""D5: the two new public datasets and the planted-issue copies of real data, written as CSVs under
backend/data/realdata/ (not committed). Each copy carries a known answer; `PLANTED` documents it.

Used by scripts/realdata.py; the four round-4 datasets come from scripts/realdata_d1.py.
"""

from __future__ import annotations

import json
import sys
import urllib.request
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
import realdata_d1 as rd  # noqa: E402

ROOT = rd.ROOT
PLANT = ROOT / "planted"
BACKEND = Path(__file__).resolve().parent.parent / "backend"

# OpenML (UCI's host has an expired certificate): file ids of the ARFF downloads.
NEW = {
    # Give Me Some Credit (Kaggle 2011, curated by TabArena, OpenML 46929): 150,000 borrowers, 10 features, 6.7 % distress.
    "gmsc": ("https://api.openml.org/data/v1/download/22125240", "gmsc.arff", "FinancialDistressNextTwoYears"),
    # Credit approval (Quinlan, OpenML 29): 690 card applications, 15 anonymised attributes.
    "credit_approval": ("https://api.openml.org/data/v1/download/29", "credit_approval.arff", "class"),
}
GMSC_ROWS = 40_000  # a stratified sample of the 150,000 (seed 0), so a run fits the time budget


def read_arff(path: Path) -> pd.DataFrame:
    """D1's reader, with ARFF's own missing marker '?' read as missing."""
    df = rd.read_arff(path)
    df = df.replace("?", np.nan)
    for c in df.columns:
        if df[c].dtype == object:
            num = pd.to_numeric(df[c], errors="coerce")
            if num.notna().sum() == df[c].notna().sum():
                df[c] = num
    return df


def new_datasets() -> dict[str, tuple[Path, str]]:
    raw = ROOT / "raw"
    raw.mkdir(parents=True, exist_ok=True)
    out = {}
    for name, (url, fn, target) in NEW.items():
        if not (raw / fn).exists():
            print(f"downloading {url}", flush=True)
            with urllib.request.urlopen(url, timeout=300) as r:  # noqa: S310 - fixed public URLs
                (raw / fn).write_bytes(r.read())
        dest = ROOT / f"{name}.csv"
        if not dest.exists():
            df = read_arff(raw / fn)
            if name == "gmsc":
                rng = np.random.default_rng(0)
                keep = []
                for _, idx in df.groupby(target).groups.items():
                    idx = np.asarray(list(idx))
                    keep += list(rng.choice(idx, int(round(len(idx) * GMSC_ROWS / len(df))), replace=False))
                df = df.loc[sorted(keep)].reset_index(drop=True)
                df.insert(0, "borrower_id", [f"B{i:06d}" for i in range(len(df))])
            df.to_csv(dest, index=False)
        out[name] = (dest, target)
    return out


def _holdout(y: pd.Series) -> set[int]:
    """The holdout the platform will draw for this label column (its own split code and seed)."""
    sys.path.insert(0, str(BACKEND))
    from app import config
    from app.splits import make_splits

    return set(make_splits(y, len(y), config.SEED, config.HOLDOUT_SHARE, config.N_FOLDS)["holdout"])


def _sig(x):
    return 1 / (1 + np.exp(-x))


# name → (base, target, what is planted, the known answer)
PLANTED = {
    "bank_leak": ("bank marketing without duration", "y",
                  "deposit_amount: the amount placed on deposit, filled after the call (> 0 for 96 % of subscribers, for 2 % of the others)",
                  "Data Doctor: leakage suspect deposit_amount · Model Doctor: leakage · ablation Δ < 0 (the honest score)"),
    "taiwan_flip": ("Taiwan", "default.payment.next.month",
                    "10 % of the TRAINING rows carry the other class's label (features of a 'yes' row swapped with a 'no' row's, so the "
                    "label column and the platform's stratified holdout are unchanged and the holdout stays clean)",
                    "Data Doctor: label quality flags · review: clean_labels kept (Δ CI above 0)"),
    "banking77_flip": ("Banking77", "category",
                       "10 % of the TRAINING rows carry another intent's label (the texts of two training rows with different intents "
                       "swapped, so the label column and the stratified holdout are unchanged)",
                       "Data Doctor: label quality flags labels that look wrong · review: clean_labels kept (Δ CI above 0)"),
    "taiwan_ratio": ("Taiwan, a 6,000-row sample", "default.payment.next.month",
                     "for 60 % of the rows the label is redrawn from utilisation u = BILL_AMT1 ÷ LIMIT_BAL: p = σ(20 (u − 0.8)). A "
                     "sample, because on all 30,000 rows the trees learn the ratio from the two raw columns (run 1: Feature Lab "
                     "+0.0019, within noise: a correct 'no change')",
                     "review: add_features kept (Feature Lab finds BILL_AMT1 ÷ LIMIT_BAL)"),
    "taiwan_drift": ("Taiwan", "default.payment.next.month",
                     "application_month (24 months, random); in the newest 30 % the bills are 1.3× (inflation) and half the labels are "
                     "redrawn from utilisation: p = σ(4 (u − 0.4))",
                     "Data Doctor: outcome drift over time · Model Doctor: shift · post-deployment: retrain_window better"),
    "bank_999": ("bank marketing without duration", "y",
                 "-999 written into 8 % of `age` and 6 % of `campaign`",
                 "Data Doctor: disguised missing in age and campaign · the fix applied (values → missing + flags)"),
    "bank_dupes": ("bank marketing without duration", "y",
                   "a block of 1,500 rows copied (3.6 %), and 300 of the copies with the label flipped",
                   "Data Doctor: duplicates (≥ 1,200 exact) and conflicting labels (≥ 300 groups)"),
    "gmsc_mnar": ("Give Me Some Credit", "FinancialDistressNextTwoYears",
                  "MonthlyIncome removed for borrowers aged under 35: 90 % of those below the segment's median income, 40 % above",
                  "What's missing: the young-borrower segment named, with income missing inside it"),
    "taiwan_nopay0": ("Taiwan", "default.payment.next.month", "PAY_0 (the latest repayment status) dropped from the run",
                      "relative to Taiwan: model error, irreducible error (R_NN) and the twins oracle all rise"),
}


def planted() -> dict[str, tuple[Path, str]]:
    PLANT.mkdir(parents=True, exist_ok=True)
    files = rd.prepare()
    out: dict[str, tuple[Path, str]] = {}
    bank = pd.read_csv(files["bank"]).drop(columns=["duration"])
    tw = pd.read_csv(files["taiwan"])
    ty = "default.payment.next.month"

    def write(name: str, df: pd.DataFrame, target: str) -> None:
        p = PLANT / f"{name}.csv"
        if not p.exists():
            df.to_csv(p, index=False)
        out[name] = (p, target)

    # 1. a leak column
    if not (PLANT / "bank_leak.csv").exists():
        rng = np.random.default_rng(11)
        d = bank.copy()
        yes = (d["y"] == "yes").to_numpy()
        amt = np.round(rng.lognormal(8.5, 0.9, len(d)), 2)
        fill = np.where(yes, rng.random(len(d)) < 0.96, rng.random(len(d)) < 0.02)
        d.insert(len(d.columns) - 1, "deposit_amount", np.where(fill, amt, 0.0))
        write("bank_leak", d, "y")
    else:
        out["bank_leak"] = (PLANT / "bank_leak.csv", "y")

    # 2. 10 % flipped training labels, holdout untouched: swap the features of a 'yes' and a 'no' training row
    if not (PLANT / "taiwan_flip.csv").exists():
        rng = np.random.default_rng(12)
        d = tw.copy()
        h = _holdout(d[ty])
        tr = np.array([i for i in range(len(d)) if i not in h])
        pos, neg = tr[d[ty].to_numpy()[tr] == 1], tr[d[ty].to_numpy()[tr] == 0]
        k = int(0.05 * len(tr))  # k pairs → 2k = 10 % of the training rows
        a, b = rng.choice(pos, k, replace=False), rng.choice(neg, k, replace=False)
        feats = [c for c in d.columns if c not in (ty, "ID")]
        A, B = d.loc[a, feats].to_numpy(), d.loc[b, feats].to_numpy()
        d.loc[a, feats], d.loc[b, feats] = B, A
        assert _holdout(d[ty]) == h
        (PLANT / "taiwan_flip_rows.json").write_text(json.dumps({"flipped": sorted(int(x) for x in np.r_[a, b])}))
        write("taiwan_flip", d, ty)
    else:
        out["taiwan_flip"] = (PLANT / "taiwan_flip.csv", ty)

    # 2b. the same on a text table, where a wrong intent label is visible in the words
    if not (PLANT / "banking77_flip.csv").exists():
        rng = np.random.default_rng(18)
        d = pd.read_csv(files["banking77"])
        h = _holdout(d["category"])
        tr = np.array([i for i in range(len(d)) if i not in h])
        k = int(0.05 * len(tr))
        a = rng.choice(tr, k, replace=False)
        rest = rng.permutation(np.setdiff1d(tr, a))
        b, used = [], set()
        for i in a:  # a partner with a different intent, each row used once
            j = next(j for j in rest if j not in used and d.at[j, "category"] != d.at[i, "category"])
            used.add(j)
            b.append(j)
        b = np.array(b)
        ta, tb = d.loc[a, "text"].to_numpy(), d.loc[b, "text"].to_numpy()
        d.loc[a, "text"], d.loc[b, "text"] = tb, ta
        assert _holdout(d["category"]) == h
        (PLANT / "banking77_flip_rows.json").write_text(json.dumps({"flipped": sorted(int(x) for x in np.r_[a, b])}))
        write("banking77_flip", d, "category")
    else:
        out["banking77_flip"] = (PLANT / "banking77_flip.csv", "category")

    # 3. a planted ratio signal, on a sample: with 24,000 training rows the trees learn u from BILL_AMT1 and LIMIT_BAL
    if not (PLANT / "taiwan_ratio.csv").exists():
        rng = np.random.default_rng(13)
        d = tw.sample(n=6000, random_state=13).reset_index(drop=True)
        u = np.clip(d["BILL_AMT1"] / d["LIMIT_BAL"], -0.5, 1.5)
        redraw = rng.random(len(d)) < 0.6
        d[ty] = np.where(redraw, (rng.random(len(d)) < _sig(20 * (u - 0.8))).astype(int), d[ty])
        write("taiwan_ratio", d, ty)
    else:
        out["taiwan_ratio"] = (PLANT / "taiwan_ratio.csv", ty)

    # 4. drift in the newest 30 % (an application month; the newest months change)
    if not (PLANT / "taiwan_drift.csv").exists():
        rng = np.random.default_rng(14)
        d = tw.copy()
        months = pd.period_range("2024-01", periods=24, freq="M")
        order = rng.permutation(len(d))
        m_idx = (np.arange(len(d)) * 24 // len(d))[np.argsort(order)]  # equal rows per month, random rows
        d.insert(1, "application_month", [str(months[i].to_timestamp().date()) for i in m_idx])
        new = m_idx >= 24 - round(24 * 0.3)  # the newest 7 months ≈ 30 %
        u = np.clip(d["BILL_AMT1"] / d["LIMIT_BAL"], -0.5, 1.5)
        redraw = new & (rng.random(len(d)) < 0.5)
        d[ty] = np.where(redraw, (rng.random(len(d)) < _sig(4 * (u - 0.4))).astype(int), d[ty])
        for i in range(1, 7):
            d[f"BILL_AMT{i}"] = np.where(new, np.round(d[f"BILL_AMT{i}"] * 1.3), d[f"BILL_AMT{i}"])
        write("taiwan_drift", d, ty)
    else:
        out["taiwan_drift"] = (PLANT / "taiwan_drift.csv", ty)

    # 5. -999 disguised missing
    if not (PLANT / "bank_999.csv").exists():
        rng = np.random.default_rng(15)
        d = bank.copy()
        d.loc[rng.random(len(d)) < 0.08, "age"] = -999
        d.loc[rng.random(len(d)) < 0.06, "campaign"] = -999
        write("bank_999", d, "y")
    else:
        out["bank_999"] = (PLANT / "bank_999.csv", "y")

    # 6. a duplicated block, with conflicting labels on part of it
    if not (PLANT / "bank_dupes.csv").exists():
        rng = np.random.default_rng(16)
        d = bank.copy()
        start = int(rng.integers(0, len(d) - 1500))
        blk = d.iloc[start:start + 1500].copy()
        flip = rng.choice(len(blk), 300, replace=False)
        blk.iloc[flip, blk.columns.get_loc("y")] = np.where(blk.iloc[flip]["y"] == "yes", "no", "yes")
        d = pd.concat([d, blk], ignore_index=True)
        write("bank_dupes", d, "y")
    else:
        out["bank_dupes"] = (PLANT / "bank_dupes.csv", "y")

    # 7. missing-not-at-random income in one segment
    if not (PLANT / "gmsc_mnar.csv").exists():
        rng = np.random.default_rng(17)
        g, gy = new_datasets()["gmsc"]
        d = pd.read_csv(g)
        inc = d["MonthlyIncome"]
        young = d["age"] < 35
        med = inc[young].median()
        p = np.where(young, np.where(inc < med, 0.9, 0.4), 0.0)
        d.loc[rng.random(len(d)) < p, "MonthlyIncome"] = np.nan
        write("gmsc_mnar", d, gy)
    else:
        out["gmsc_mnar"] = (PLANT / "gmsc_mnar.csv", NEW["gmsc"][2])
    return out


if __name__ == "__main__":
    for k, (p, t) in {**new_datasets(), **planted()}.items():
        df = pd.read_csv(p)
        print(f"{k:16s} {len(df):>6,} × {df.shape[1]:>2} · y = {t} · {df[t].value_counts(normalize=True).round(3).to_dict() if df[t].nunique() < 5 else ''}")
