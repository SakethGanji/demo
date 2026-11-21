"""P11 test data: N rows x 27 columns like R12's benchmark (5 text-like: an ID, 3 categories, a date), a 3-class label
`y` with a rare class (~0.05 %, so it is kept whole by the stratified sample), an int column with nulls, a bool column.
    python make_data.py <rows> <out.csv> [seed]"""
import sys

import numpy as np
import pyarrow as pa
import pyarrow.csv as pacsv

n = int(sys.argv[1]); out = sys.argv[2]; seed = int(sys.argv[3]) if len(sys.argv) > 3 else 7
rng = np.random.default_rng(seed)
cols = {"acct": pa.array([f"A{i:09d}" for i in range(n)])}
X = rng.normal(size=(n, 18)).astype("float64")
for j in range(18):
    cols[f"x{j:02d}"] = X[:, j].round(5)
seg = rng.choice(["retail", "sme", "corp", "private", "public"], n, p=[.5, .2, .15, .1, .05])
reg = rng.choice([f"region_{k:02d}" for k in range(40)], n)
chan = rng.choice(["branch", "online", "broker", "partner"], n)
cols["segment"], cols["region"], cols["channel"] = pa.array(seg), pa.array(reg), pa.array(chan)
cols["opened"] = pa.array((np.datetime64("2015-01-01") + rng.integers(0, 3650, n).astype("timedelta64[D]")).astype("datetime64[D]"))
tenure = rng.integers(0, 30, n).astype("float64"); tenure[rng.random(n) < 0.03] = np.nan
cols["tenure"] = pa.array(tenure, mask=np.isnan(tenure)).cast(pa.int64())
cols["flag"] = pa.array(rng.random(n) < 0.3)
cols["n_products"] = pa.array(rng.integers(1, 6, n))
z = X[:, 0] + 0.8 * X[:, 1] - 0.6 * X[:, 2] + 0.5 * (seg == "sme") + 0.3 * rng.normal(size=n)
y = np.where(z > 0.9, "b", "a").astype(object)
y[rng.random(n) < 0.0005] = "c"
cols["y"] = pa.array(y.astype(str))
t = pa.table(cols)
assert t.num_columns == 27, t.num_columns
pacsv.write_csv(t, out)
print(out, n, "rows", t.num_columns, "columns", {k: int((y == k).sum()) for k in ("a", "b", "c")})
