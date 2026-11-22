"""X4: the audit chain. Which actions of the X4 runs are in the team chains (and which are not); tamper with the scratch
database (edit a middle row, delete a middle row, delete the tail and move the head) and see whether
GET /teams/{t}/audit/verify catches it. Last: a legal hold, then the admin reset (four eyes, demo mode) under it.

    X4_DB=<sqlite file | postgresql://…> python audit.py <base> <state.json>"""
import collections
import os

from x4lib import get, load, post, show

st = load()
cr, co, fr = st["cr"], st["co"], st["fr"]
DB = os.environ["X4_DB"]


def q(sql, a=(), fetch=False):
    if DB.startswith("postgres"):
        import psycopg
        with psycopg.connect(DB, autocommit=True) as con:
            cur = con.execute(sql.replace("?", "%s"), a)
            return cur.fetchall() if fetch else None
    import sqlite3
    con = sqlite3.connect(DB, timeout=60)
    cur = con.execute(sql, a)
    out = cur.fetchall() if fetch else None
    con.commit()
    con.close()
    return out


print(f"# mode {st['mode']}")
print("\n# 1. actions in the chains")
for t in (cr, co, fr, "tm_default"):
    rows = get("Ada", f"/teams/{t}/audit", params={"limit": 5000}).json().get("entries") or []
    cnt = collections.Counter(r["action"] for r in rows)
    print(f"  {t}: {len(rows)} rows:", dict(sorted(cnt.items())))
allrows = [r for t in (cr, co, fr, "tm_default") for r in get("Ada", f"/teams/{t}/audit", params={"limit": 5000}).json().get("entries") or []]
acts = {r["action"] for r in allrows}
for want in ("agent_session_started", "fairness", "pii_tags_set", "pii_tags_confirmed", "team_member_role_changed", "team_member_removed",
             "legal_hold_placed", "legal_hold_released", "purge_requested", "purge_executed", "share_approved", "share_revoked",
             "certification_approved", "batch_scored", "approved", "dataset_rows_read", "upload_discarded", "sql_preview",
             "run_cancelled", "dataset_exported"):
    hit = sorted(a for a in acts if want in a)
    print(f"    {want!r}: {hit or 'MISSING'}")
print("  agent rows (auth agent):", [(r["actor"], r["action"]) for r in allrows if r.get("auth") == "agent"][:6])
print("  rows naming Fay's fairness GET / agent session ids:",
      [r["action"] for r in allrows if "fairness" in str(r.get("details")) or "as_" in str(r.get("details"))][:6])

print("\n# 3. admin reset under a legal hold (ENSEMBLE_DEMO_MODE=1)")
hold = show("3a Ada POST /legal-holds {team credit-risk}", post("Ada", "/legal-holds", {"scope_kind": "team", "scope_id": cr, "case_ref": "CASE-RESET",
                                                                                     "reason": "regulator inquiry", "by": "Ada"}), ["id", "active"])
rq = show("3b Ada POST /admin/reset-requests", post("Ada", "/admin/reset-requests", {"reason": "demo environment refresh", "by": "Ada"}),
          ["id", "status"], cut=300)
show("3c Adam approves", post("Adam", f"/admin/reset-requests/{rq.get('id')}/decide", {"approve": True, "comment": "ok", "by": "Adam"}),
     ["status"], cut=200)
for s_ in get("Ada", "/agent/sessions").json().get("sessions") or []:  # the reset refuses while a conversation waits
    if s_["status"] not in ("idle", "stopped"):
        post("Ada", f"/agent/sessions/{s_['id']}/stop", {})
r = show("3d Ada POST /admin/reset {request_id}", post("Ada", "/admin/reset", {"confirm": "reset", "by": "Ada", "request_id": rq.get("id"),
                                                                              "reason": "refresh"}), ["deleted", "four_eyes"], cut=600)
show("3e GET /legal-holds?team=cr", get("Ada", "/legal-holds", params={"team": cr}), None, cut=300)
show("3f GET /datasets/{v1}", get("Ada", f"/datasets/{st.get('new_v') or st['v1']}"), None, cut=150)
show("3g GET /runs/{run}", get("Ada", f"/runs/{st['run']}"), None, cut=150)
show("3h verify credit-risk chain", get("Ada", f"/teams/{cr}/audit/verify"), ["ok", "rows"], cut=200)
