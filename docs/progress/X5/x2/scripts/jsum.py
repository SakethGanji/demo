"""X5/x2: per-call-kind summary of two recordings (X2 record.py). Ignores keys `storage` (X2's rule) and `access`
(a key added to GET /datasets/{id} after X2, absent before = null after). Diffs are judged on their row results
(identical + rows minus column stats). usage: jsum.py <before> <after>"""
import json, sys
from pathlib import Path
A, B = Path(sys.argv[1]), Path(sys.argv[2])
IGN = {"storage", "access"}
strip = lambda x: {k: strip(v) for k, v in x.items() if k not in IGN} if isinstance(x, dict) else [strip(v) for v in x] if isinstance(x, list) else x
kinds = {}
for f in sorted(A.glob("*.json")):
    if f.name.startswith("_"): continue
    k = f.stem.split("_ds_")[0].split("_run_")[0].split("_")[0] if not f.stem.startswith("verify_post") else "verify_post"
    a = json.loads(f.read_text()); g = B / f.name
    b = json.loads(g.read_text()) if g.exists() else None
    same = b is not None and strip(a) == strip(b)
    if k == "diff" and b is not None and not same:
        ra, rb = a.get("body"), b.get("body")
        pick = lambda r: (r.get("identical"), {x: y for x, y in (r.get("rows") or {}).items() if x not in ("matched_by", "note")}) if isinstance(r, dict) else r
        same_rows = pick(ra) == pick(rb)
        kinds.setdefault(k, [0, 0, []]); kinds[k][0] += same_rows; kinds[k][1] += 1
        if not same_rows: kinds[k][2].append(f"{f.stem}: {a['status']}→{b['status']} {json.dumps(pick(ra), default=str)[:160]} → {json.dumps(pick(rb), default=str)[:160]}")
        continue
    kinds.setdefault(k, [0, 0, []]); kinds[k][0] += same; kinds[k][1] += 1
    if not same: kinds[k][2].append(f.stem)
for k, (s, n, bad) in kinds.items():
    print(f"{k:<12} {s}/{n} identical" + (" (diff: row results)" if k == "diff" else ""))
    for x in bad: print("     differs:", x)
