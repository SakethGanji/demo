"""P10: the CSV files the generator uploads (seeded). Columns cover the dtype cases the migration must keep exactly:
codes with leading zeros (zip, acct), ints with missing cells, floats with NaN, booleans, text with NaN, a target."""
import sys
from pathlib import Path

import numpy as np
import pandas as pd

out = Path(sys.argv[1])
out.mkdir(parents=True, exist_ok=True)
rng = np.random.default_rng(10)


def loans(n, start=1):
    r = np.random.default_rng(start)
    inc = r.normal(52000, 15000, n).round(2)
    inc[r.random(n) < 0.05] = np.nan
    late = r.poisson(1.2, n).astype(float)
    late[r.random(n) < 0.08] = np.nan
    seg = r.choice(["retail", "sme", "private"], n).astype(object)
    seg[r.random(n) < 0.04] = np.nan
    age = r.integers(21, 75, n)
    z = 0.00004 * (inc - 52000) - 0.5 * np.nan_to_num(late) + 0.02 * (age - 45)
    y = (z + r.normal(0, 1, n) > 0).astype(int)
    return pd.DataFrame({"loan_id": [f"L{i:05d}" for i in range(start, start + n)],
                         "zip": [f"{v:05d}" for v in r.integers(500, 99000, n)],
                         "income": inc, "age": age, "n_late": late, "segment": seg,
                         "approved": r.random(n) < 0.7, "y": y})


L = loans(600)
L.to_csv(out / "loans.csv", index=False)
# the next file of the same loans: shuffled, 12 incomes changed, 15 loans gone, 25 new loans
L3 = L.drop(index=range(100, 115)).copy()
L3.loc[L3.index[:12], "income"] = L3.loc[L3.index[:12], "income"].fillna(0) + 1000
L3 = pd.concat([L3, loans(25, start=601)]).sample(frac=1, random_state=3)
L3.to_csv(out / "loans_v3.csv", index=False)

r = np.random.default_rng(20)
n = 400
pd.DataFrame({"cust": [f"C{i:04d}" for i in range(n)], "tenure": r.integers(1, 60, n), "spend": r.gamma(2, 50, n).round(2),
              "plan": r.choice(["basic", "plus", "pro"], n), "churn": (r.random(n) < 0.25).astype(int)}).to_csv(out / "churn.csv", index=False)

r = np.random.default_rng(30)
n = 800
bal = r.lognormal(8, 1, n).round(2)
bal[r.random(n) < 0.06] = np.nan
ten = r.integers(0, 120, n).astype(float)
ten[r.random(n) < 0.05] = np.nan
A = pd.DataFrame({"acct": [f"{i:08d}" for i in range(1, n + 1)], "zip": [f"{v:05d}" for v in r.integers(500, 99000, n)],
                  "balance": bal, "tenure": ten, "active": r.random(n) < 0.8,
                  "region": r.choice(["north", "south", "east", "west"], n), "y": (r.random(n) < 0.3).astype(int)})
A.to_csv(out / "accounts.csv", index=False)
# the next file of the accounts (a P06 upload as the next version of a CSV-era version, made by the pre-P10 head)
A2 = A.iloc[50:].copy()
A2.loc[A2.index[:10], "region"] = "central"
A2.to_csv(out / "accounts_next.csv", index=False)

r = np.random.default_rng(40)
n = 300
pd.DataFrame({"k": [f"K{i}" for i in range(n)], "x1": r.normal(0, 1, n).round(4), "x2": r.integers(0, 9, n),
              "y": (r.random(n) < 0.4).astype(int)}).to_csv(out / "scratch.csv", index=False)
print("files in", out)
