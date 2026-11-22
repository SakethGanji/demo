"""make_batch.py <files dir>: batch files for the join model (apps rows 0-299 without y, their cust and pays) and
variants: reordered + extra columns; type-coercion failures (x1 'abc', applied_on dd/mm/yyyy, amount 'n/a');
ext_delta.csv (a delta of the ext dataset: 50 scores changed)."""
import sys
from pathlib import Path
import numpy as np, pandas as pd
F = Path(sys.argv[1]); B = F / "batch"; B.mkdir(exist_ok=True)
apps = pd.read_csv(F / "apps.csv").iloc[:300].drop(columns=["y"]); cust = pd.read_csv(F / "cust.csv"); pays = pd.read_csv(F / "pays.csv")
c = cust[cust.cust.isin(apps.cust)]; p = pays[pays.app_id.isin(apps.app_id)]
for d, name in ((apps, "apps"), (c, "cust"), (p, "pays")):
    (B / "clean").mkdir(exist_ok=True); d.to_csv(B / "clean" / f"{name}.csv", index=False)
(B / "reorder").mkdir(exist_ok=True)
apps[list(reversed(apps.columns))].assign(extra_col="zz", y=1).to_csv(B / "reorder" / "apps.csv", index=False)
c.to_csv(B / "reorder" / "cust.csv", index=False); p[list(reversed(p.columns))].to_csv(B / "reorder" / "pays.csv", index=False)
(B / "coerce").mkdir(exist_ok=True)
a2 = apps.copy(); a2["x1"] = a2["x1"].astype(object); a2.loc[:9, "x1"] = "abc"
a2.loc[10:19, "applied_on"] = pd.to_datetime(a2.loc[10:19, "applied_on"]).dt.strftime("%d/%m/%Y")
a2.to_csv(B / "coerce" / "apps.csv", index=False); c.to_csv(B / "coerce" / "cust.csv", index=False)
p2 = p.copy(); p2["amount"] = p2["amount"].astype(object); p2.iloc[:50, p2.columns.get_loc("amount")] = "n/a"; p2.to_csv(B / "coerce" / "pays.csv", index=False)
d = cust.iloc[:60].copy(); d["score"] = d["score"] + 100; d.insert(0, "_op", "U"); d.to_csv(F / "ext_delta.csv", index=False)
print("batch", len(apps), len(c), len(p))
