"""check_j4.py <db> <lake> <frame id> <files dir>: recompute the J4 frame from the CSVs with pandas, every cell."""
import sqlite3, sys
from pathlib import Path
import numpy as np, pandas as pd
db, lake, fid, F = sys.argv[1:5]
pg = db.startswith("postgres")
con = __import__("psycopg").connect(db) if pg else sqlite3.connect(db)
sha = con.execute(f"SELECT cache_blob FROM join_frames WHERE id = {'%s' if pg else '?'}", (fid,)).fetchone()[0]
fr = pd.read_parquet(next(Path(lake).rglob(f"{sha}.parquet")))
apps = pd.read_csv(f"{F}/apps.csv"); cust = pd.read_csv(f"{F}/cust.csv"); pays = pd.read_csv(f"{F}/pays.csv")
apps["d"] = pd.to_datetime(apps.applied_on); pays["p"] = pd.to_datetime(pays.paid_on)
e = apps.merge(cust.rename(columns={"score": "c__score"}), on="cust", how="left")
m = apps[["app_id", "d"]].merge(pays.dropna(subset=["app_id"]), on="app_id")
g = m.groupby("app_id")
agg = pd.DataFrame({"p__n": g.size(), "p__amt": g.amount.sum(min_count=1)})
w = m[(m.p >= m.d - pd.Timedelta(days=90)) & (m.p < m.d)]
agg["p__amt90"] = w.groupby("app_id").amount.sum(); agg["p__n90"] = w.groupby("app_id").size()
f = m[(m.p > m.d) & (m.p <= m.d + pd.Timedelta(days=30))]
agg["p__n30f"] = f.groupby("app_id").size(); agg["p__last"] = g.p.max()
e = e.merge(agg, left_on="app_id", right_index=True, how="left")
for c in ("p__n", "p__n90", "p__n30f"):
    e[c] = e[c].fillna(0)
fr = fr.sort_values("app_id").reset_index(drop=True); e = e.sort_values("app_id").reset_index(drop=True)
bad = {}
for c in ["c__score", "p__n", "p__amt", "p__amt90", "p__n90", "p__n30f", "p__last"]:
    a, b = fr[c], e[c]
    if c == "p__last":
        a, b = pd.to_datetime(a), b
    eq = (a.isna() & b.isna()) | (np.isclose(a.astype(float), b.astype(float)) if c != "p__last" else (a == b))
    bad[c] = int((~eq).sum())
print("   cells differing from pandas:", bad, "| rows", len(fr), "| A00001 n", int(fr.loc[fr.app_id == "A00001", "p__n"].iloc[0]))
