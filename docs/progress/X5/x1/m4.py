"""M4: Data Doctor apply fuzz on a v1 with target y (acct, x1, x2, y; 3,000 rows). usage: m4.py <version_id>"""
import sys
from c import *
v = sys.argv[1]
B = 2 ** 63
cases = [
    ("rescale from=2^63 factor=2^63", [{"op": "rescale", "column": "x1", "date_column": "x2", "from": B, "factor": B}]),
    ("rescale factor=2^64", [{"op": "rescale", "column": "x1", "date_column": "x2", "from": 0, "factor": 2 ** 64}]),
    ("relabel label 2^63", [{"op": "relabel", "rows": [0], "labels": [B]}]),
    ("drop_rows [1.5]", [{"op": "drop_rows", "rows": [1.5]}]),
    ("drop_rows [true]", [{"op": "drop_rows", "rows": [True]}]),
    ("drop_rows [-1]", [{"op": "drop_rows", "rows": [-1]}]),
    ("drop_rows [1e30]", [{"op": "drop_rows", "rows": [1e30]}]),
    ("drop_rows [10**12]", [{"op": "drop_rows", "rows": [10 ** 12]}]),
    ("drop_rows null", [{"op": "drop_rows", "rows": None}]),
    ("drop_rows ['3']", [{"op": "drop_rows", "rows": ["3"]}]),
]
for name, tr in cases:
    st, r = c("POST", f"/api/datasets/{v}/data-doctor/apply", {"transforms": tr, "by": "fx1"}, quiet=True)
    d = r.get("detail") if isinstance(r, dict) else r
    print(f"{name:32s} -> {st} " + (f"new {r['dataset']['id']} rows {r['rows_before']}->{r['rows_after']} effects {r['effects']}"[:160] if st in (200, 201) else str(d)[:150]))
print("-- a real fix, twice (dedupe):")
for k in range(2):
    st, r = c("POST", f"/api/datasets/{v}/data-doctor/apply", {"transforms": [{"op": "drop_rows", "rows": [1, 2]}], "by": "fx1"}, quiet=True)
    print(f"drop_rows [1,2] #{k + 1}: {st} {r['dataset']['id']} rows {r['rows_before']}->{r['rows_after']} created={r.get('created')} dedup={r.get('deduplicated')}")
print("-- a no-op fix (relabel row 5 to its own label):")
st, rows = c("GET", f"/api/datasets/{v}/rows?limit=6", quiet=True)
y5 = rows["rows"][5]["y"]
st, r = c("POST", f"/api/datasets/{v}/data-doctor/apply", {"transforms": [{"op": "relabel", "rows": [5], "labels": [y5]}], "by": "fx1"}, quiet=True)
print(f"relabel [5] -> {y5}: {st} {r['dataset']['id']} created={r.get('created')} dedup={r.get('deduplicated')}")
