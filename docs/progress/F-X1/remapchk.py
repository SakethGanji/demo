"""C2: on a sampled report, every evidence row number is the version's position: GET /rows?offset=<row> shows the same values."""
import sys
from c import *
v = sys.argv[1]
st, r = c("GET", f"/api/datasets/{v}/data-doctor", quiet=True)
print("report", st, "rows", r.get("rows"), "sample", {k: r["sample"][k] for k in ("rows", "of", "rate", "ids_sha256")} if r.get("sample") else None)
ok = bad = 0
for f in r["findings"]:
    ev = f.get("evidence") or {}
    if (ev.get("columns") or [None])[0] != "row": continue
    for row in ev["rows"][:5]:
        st, x = c("GET", f"/api/datasets/{v}/rows?offset={row[0]}&limit=1", quiet=True)
        got = x["rows"][0]
        def eq(a, b):
            try: return float(a) == float(b)
            except (TypeError, ValueError): return str(a) == str(b)
        same = all(eq(got.get(col), val) or (val is None and got.get(col) in (None, "")) for col, val in zip(ev["columns"][1:], row[1:]) if col in got)
        ok += same; bad += not same
        if not same: print("  MISMATCH", f["id"], row, {c_: got.get(c_) for c_ in ev["columns"][1:]})
print(f"evidence rows checked against the version: {ok} same, {bad} different; findings: {[(f['id'], f['severity']) for f in r['findings']]}")
