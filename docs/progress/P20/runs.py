"""P20, part 2: the run pages and the agent's CSV, after chain.py (same API, same data dir). Usage:
P20_IDS=<ids.json from chain.py> ENSEMBLE_DATA_DIR=<the API's data dir> python runs.py [base]

The agent's CSV is built in-process (agent/tools._dataset_csv, what the batch_score tool uploads) against the same
data dir. Every body is scanned for the raw PII values, as in chain.py."""
import asyncio
import json
import os
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "..", "backend"))
src = open(os.path.join(os.path.dirname(__file__), "chain.py")).read().split("# 1. the team")[0]
ns: dict = {"__file__": __file__}
exec(src, ns)  # the same rows, RAW values, leaks() and show() as chain.py
c, B, h, show, leaks, rows = ns["c"], ns["B"], ns["h"], ns["show"], ns["leaks"], ns["rows"]
ids = json.load(open(os.environ.get("P20_IDS", "p20_ids.json")))
ds, v2 = ids["ds"], ids["v2"]

# the run on v1 (it trains on the stored values: training reads unmasked)
run = show("R1 Ana POST /runs {v1, label y, time_limit 40}", c.post(f"{B}/runs", json={"dataset_id": ds, "label": "y", "time_limit": 40, "by": "Ana"},
                                                                   headers=h("Ana")), ["id", "status"])
rid = run["id"]
while (st := c.get(f"{B}/runs/{rid}").json()["status"]) not in ("succeeded", "failed", "cancelled"):
    time.sleep(3)
res = c.get(f"{B}/runs/{rid}").json()
print("    run", rid, st, "| features:", (res.get("result") or {}).get("features"))

ex = show("R2 Ben GET /runs/{id}/explain (worst mistakes with their features)", c.get(f"{B}/runs/{rid}/explain", headers=h("Ben")),
          ["masked_columns"], cut=200)
print("     worst[0].features:", (ex.get("worst") or [{}])[0].get("features"))
hp = show("R3 Ben GET /runs/{id}/holdout-predictions", c.get(f"{B}/runs/{rid}/holdout-predictions", params={"by": "Ben"}, headers=h("Ben")),
          ["n_rows", "classes"], cut=200)
print("     keys:", sorted(hp))
row = hp["rows"][0]
sh = show("R4 Ben GET /runs/{id}/shap?row=…", c.get(f"{B}/runs/{rid}/shap", params={"row": row}, headers=h("Ben")),
          ["row", "masked_columns", "cached"], cut=300)
print("     contributions:", [(x["feature"], x["value"]) for x in sh.get("contributions") or []])
print("     the stored row:", {k: rows[row][k] for k in ("zip", "dob", "ssn") if k in rows[row]})
sl = show("R5 Ana POST /runs/{id}/slices {min_support 20}", c.post(f"{B}/runs/{rid}/slices", json={"min_support": 20}, headers=h("Ana")),
          ["masked_columns"], cut=200)
print("     slices:", [(s["label"], s.get("conditions")) for s in (sl.get("slices") or [])[:4]])
cols = [b["column"] for b in sl.get("columns") or []]
print("     columns binned:", cols, "| zip levels:", next((b.get("levels") for b in sl.get("columns") or [] if b["column"] == "zip"), None))
if sl.get("slices"):
    sid = sl["slices"][0]["id"]
    r = show("R5b Ben GET /runs/{id}/slices/{sid}/rows?limit=3", c.get(f"{B}/runs/{rid}/slices/{sid}/rows", params={"limit": 3}, headers=h("Ben")),
             ["total", "masked_columns"], cut=200)
    print("     rows[0].features:", (r.get("rows") or [{}])[0].get("features"))
    show("R5c Ben GET /runs/{id}/slices/{sid}/pool", c.get(f"{B}/runs/{rid}/slices/{sid}/pool", headers=h("Ben")), ["label", "conditions", "in_slice"])
show("R5d Ben GET /runs/{id}/slices", c.get(f"{B}/runs/{rid}/slices", headers=h("Ben")), ["masked_columns"], cut=150)
d = show("R6 Ben GET /runs/{id}/doctor", c.get(f"{B}/runs/{rid}/doctor", headers=h("Ben")), ["headline", "pii_features", "masked_columns"], cut=900)

# the agent's CSV: frames.scan(mask_for='masked') → DuckDB COPY
from app.agent import tools  # noqa: E402

csv = asyncio.run(tools._dataset_csv(None, ds, ["y"], 5)).decode()
print("## R7 agent/tools._dataset_csv(v1, drop [y], limit 5) | raw PII values:", leaks(csv))
for line in csv.splitlines()[:3]:
    print("    ", line)

dd = show("R8 Ben GET /datasets/{v1}/data-doctor (example rows)", c.get(f"{B}/datasets/{ds}/data-doctor", headers=h("Ben")), ["dataset_id"], cut=120)
print("     checks with examples:", [x.get("id") or x.get("check") for x in dd.get("checks") or [] if x.get("examples")][:6])
