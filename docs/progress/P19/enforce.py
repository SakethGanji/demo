"""P19 enforce-mode chain (API started with ENSEMBLE_AUTHZ=enforce ENSEMBLE_AUTHZ_ALLOW_TYPED=1 on chain.py's data dir).
Usage: python enforce.py <dataset id made by chain.py>"""
import json, sys
import httpx

B, ds = "http://localhost:8000/api", sys.argv[1]
c = httpx.Client(timeout=120)
h = lambda w: {"X-Ensemble-Actor": w} if w else {}


def show(n, r):
    try:
        j = r.json()
    except ValueError:
        j = r.text[:200]
    print(f"## {n} → HTTP {r.status_code}" + (f" | X-Ensemble-Authz: {r.headers['x-ensemble-authz']}" if "x-ensemble-authz" in r.headers else ""))
    print("   ", json.dumps(j, default=str)[:500])
    return j


edit = lambda who, v, by=None: {"ops": [{"op": "set_cells", "cells": [{"row_id": 7, "column": "income", "value": v}]}],
                                "reason": "enforce-mode check", "by": by or who}
print("mode:", c.get(f"{B}/me", headers=h("Ana")).json()["authz_mode"])
show("E1 Ben (viewer) POST /datasets/{id}/edit", c.post(f"{B}/datasets/{ds}/edit", json=edit("Ben", 61000), headers=h("Ben")))
show("E2 Ana (owner) POST /datasets/{id}/edit", c.post(f"{B}/datasets/{ds}/edit", json=edit("Ana", 62000), headers=h("Ana")))
show("E3 no name POST /datasets/{id}/edit", c.post(f"{B}/datasets/{ds}/edit", json={k: v for k, v in edit("x", 63000).items() if k != "by"}))
show("E4 Ana POST …/edit with body by=Ben", c.post(f"{B}/datasets/{ds}/edit", json=edit("Ana", 64000, by="Ben"), headers=h("Ana")))
show("E5 Dee (tm_default only) GET /datasets/{id}/grid", c.get(f"{B}/datasets/{ds}/grid", params={"limit": 2}, headers=h("Dee")))
show("E6 Ben GET /datasets/{id}/rows (viewer)", c.get(f"{B}/datasets/{ds}/rows", params={"limit": 1}, headers=h("Ben")))
for who in ("Ana", "Dee", "Ada"):
    rows = c.get(f"{B}/datasets", headers=h(who)).json()["datasets"]
    runs = c.get(f"{B}/runs", headers=h(who)).json()
    reg = c.get(f"{B}/registry", headers=h(who)).json()
    print(f"## E7 {who} lists: /datasets {len(rows)} rows, teams {sorted({r['team']['slug'] for r in rows})}; /runs {len(runs['runs'])} "
          f"(total {runs['total']}); /registry models {len(reg['models'])}")
show("E8 Ben POST /teams/credit-risk/members {Cara member} (viewer)", c.post(f"{B}/teams/credit-risk/members", json={"name": "Cara", "role": "member", "by": "Ben"}, headers=h("Ben")))
show("E9 Ben GET /teams/credit-risk/audit/verify (viewer: audit.read)", c.get(f"{B}/teams/credit-risk/audit/verify", headers=h("Ben")))
show("E10 Dee GET /teams/credit-risk/audit (not a member)", c.get(f"{B}/teams/credit-risk/audit", headers=h("Dee")))
a = c.get(f"{B}/teams/credit-risk/audit", params={"action": "authz_denied"}, headers=h("Ana")).json()["entries"]
print("## E11 Ana GET /teams/credit-risk/audit?action=authz_denied →", [(r["actor"], r["details"]["action"], r["details"]["needed"], r["auth"]) for r in a])
cov = c.get(f"{B}/authz/coverage").json()
print("## E12 coverage:", cov["mode"], "uncovered", cov["n_uncovered"])
