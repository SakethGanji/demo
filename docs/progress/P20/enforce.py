"""P20 in enforce mode (ENSEMBLE_AUTHZ=enforce ENSEMBLE_AUTHZ_ALLOW_TYPED=1), after chain.py on the same data dir.
Usage: P20_IDS=<ids.json> python enforce.py [base]"""
import json
import os
import sys

sys.argv = sys.argv[:2]
src = open(os.path.join(os.path.dirname(__file__), "chain.py")).read().split("# 1. the team")[0]
ns: dict = {"__file__": __file__}
exec(src, ns)
c, B, h, show = ns["c"], ns["B"], ns["h"], ns["show"]
ds = json.load(open(os.environ.get("P20_IDS", "p20_ids.json")))["ds"]

show("E1 Ben GET …/rows?limit=1 (masked view: viewer allowed)", c.get(f"{B}/datasets/{ds}/rows", params={"limit": 1}, headers=h("Ben")),
     ["masked_columns", "unmasked"])
show("E2 Ben GET …/rows?unmasked=1&reason=QA", c.get(f"{B}/datasets/{ds}/rows", params={"limit": 1, "unmasked": 1, "reason": "QA"}, headers=h("Ben")))
show("E3 Ana GET …/rows?unmasked=1&reason=QA", c.get(f"{B}/datasets/{ds}/rows", params={"limit": 1, "unmasked": 1, "reason": "QA"}, headers=h("Ana")),
     ["masked_columns", "unmasked"])
show("E4 anonymous GET …/rows?unmasked=1&reason=QA", c.get(f"{B}/datasets/{ds}/rows", params={"limit": 1, "unmasked": 1, "reason": "QA", "by": ""}))
show("E5 Ben GET …/export?unmasked=1&reason=QA", c.get(f"{B}/datasets/{ds}/export", params={"by": "Ben", "unmasked": 1, "reason": "QA"}, headers=h("Ben")))
show("E6 Ben PUT …/pii", c.put(f"{B}/datasets/{ds}/pii", json={"columns": [{"column": "ssn", "tag": "none"}], "by": "Ben"}, headers=h("Ben")))
show("E7 Ben GET …/grid?unmasked=1&reason=QA", c.get(f"{B}/datasets/{ds}/grid", params={"limit": 1, "unmasked": 1, "reason": "QA"}, headers=h("Ben")))
a = c.get(f"{B}/teams/credit-risk/audit", params={"action": "authz_denied"}, headers=h("Ana")).json()
print("## E8 authz_denied rows:", [(e["actor"], e["auth"], (e["decision"] if isinstance(e["decision"], dict) else json.loads(e["decision"] or "{}")).get("action"))
                                   for e in a["entries"]])
cov = c.get(f"{B}/authz/coverage", params={"all": "true"}, headers=h("Ana")).json()
print(f"## E9 GET /authz/coverage → mode {cov['mode']}, routes {cov['n_routes']}, must {cov['n_must']}, covered {cov['n_covered']}, uncovered {cov['n_uncovered']}")
print("    P20 routes:", [(r["method"], r["path"], r.get("action")) for r in cov["routes"] if r["path"].endswith("/pii")])
for team in ("credit-risk", "tm_default"):
    show(f"E10 GET /teams/{team}/audit/verify", c.get(f"{B}/teams/{team}/audit/verify", headers=h("Ana")))
