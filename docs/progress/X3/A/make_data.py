"""X3 lane A data: a 3-table dataset with join edge cases. Usage: make_data.py <out dir>
apps (grain, 3000 rows): app_id, cust (40 NULL), applied_on (ISO date), applied_day (same day as 'YYYY-MM-DD 00:00:00'), x1, y
cust (n:1 side): cust unique, score;  cust_dup.csv: the same plus one repeated cust (a fan-out)
pays (aggregate side, 1:n): pay_id, app_id (app A00001 has 30,000 rows: skew; 25 NULL app_id), paid_on (ISO; 10 % NULL;
  many exactly at applied_on-90 and at applied_on), paid_serial (Excel serial number of paid_on), paid_dmy ('31/01/2024'), amount
daily (n:1 on a timestamp): day ('YYYY-MM-DD 00:00:00' unique), rate"""
import sys
from pathlib import Path
import numpy as np, pandas as pd

out = Path(sys.argv[1]); out.mkdir(parents=True, exist_ok=True)
rng = np.random.default_rng(7)
n = 3000
base = pd.Timestamp("2024-01-01")
applied = base + pd.to_timedelta(rng.integers(0, 365, n), unit="D")
cust = np.array([f"C{i:04d}" for i in rng.integers(0, 1200, n)], dtype=object)
cust[rng.choice(n, 40, replace=False)] = None
x1 = rng.normal(size=n)
apps = pd.DataFrame({"app_id": [f"A{i:05d}" for i in range(1, n + 1)], "cust": cust, "applied_on": applied.strftime("%Y-%m-%d"),
                     "applied_day": applied.strftime("%Y-%m-%d 00:00:00"), "x1": x1.round(4)})
apps["y"] = (x1 + rng.normal(size=n) > 0.8).astype(int)
apps.to_csv(out / "apps.csv", index=False)
cu = pd.DataFrame({"cust": [f"C{i:04d}" for i in range(1100)], "score": rng.integers(300, 850, 1100)})
cu.to_csv(out / "cust.csv", index=False)
pd.concat([cu, cu.iloc[[5]].assign(score=1)]).to_csv(out / "cust_dup.csv", index=False)
rows = []
k = 0
for i, (a, d) in enumerate(zip(apps.app_id, applied)):
    m = 30000 if i == 0 else int(rng.integers(0, 6))
    offs = rng.integers(-200, 60, m)
    if i % 10 == 1:
        offs = np.r_[offs, [-90, 0, -91, -89]]  # window boundaries
    for o in offs:
        k += 1
        rows.append((f"P{k:07d}", a, d + pd.Timedelta(days=int(o)), float(rng.integers(1, 500))))
pays = pd.DataFrame(rows, columns=["pay_id", "app_id", "paid", "amount"])
pays.loc[pays.sample(frac=0.10, random_state=1).index, "paid"] = pd.NaT
pays.loc[pays.sample(n=25, random_state=2).index, "app_id"] = None
pays["paid_on"] = pays.paid.dt.strftime("%Y-%m-%d")
pays["paid_serial"] = ((pays.paid - pd.Timestamp("1899-12-30")).dt.days).astype("Int64")
pays["paid_dmy"] = pays.paid.dt.strftime("%d/%m/%Y")
pays.drop(columns="paid").to_csv(out / "pays.csv", index=False)
days = pd.date_range("2023-06-01", "2025-01-31")
pd.DataFrame({"day": days.strftime("%Y-%m-%d 00:00:00"), "rate": rng.normal(5, 1, len(days)).round(3)}).to_csv(out / "daily.csv", index=False)
print({"apps": len(apps), "cust": len(cu), "pays": len(pays), "daily": len(days)})
