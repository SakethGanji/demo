"""F-X4b: X4 M6 (actions missing from the chains) and M4 (admin reset under a legal hold) after the fix.

    python audit.py <base> <state.json>"""
import collections
import json

from x4lib import B, R, audit_rows, c, csv, get, h, load, post, show

st = load()
cr, co, fr, pd, v1 = st["cr"], st["co"], st["fr"], st["pd"], st["v1"]
print(f"# mode {st['mode']}")

print("\n# 1. the actions X4 found unaudited: upload discard, run cancel (agent session and SQL preview rows: agent.txt, sql.txt)")
u = c.post(B + "/uploads", params={"filename": "loans_b.csv", "project_id": pd}, files={"file": ("loans_b.csv", csv(R[:40]), "text/csv")},
           headers=h("Ana")).json()["upload_id"]
show("1a Ana DELETE /uploads/{up}", c.delete(f"{B}/uploads/{u}", headers=h("Ana")), ["id", "status"], cut=120)
rr = show("1b Ana POST /runs (60 s)", post("Ana", "/runs", {"dataset_id": v1, "label": "y", "time_limit": 60, "by": "Ana", "project_id": pd,
                                                              "drop_columns": ["app_id", "applicant_name", "email"]}), ["id"])["id"]
show("1c Ana POST /runs/{id}/cancel", post("Ana", f"/runs/{rr}/cancel", {}), cut=120)
rows = audit_rows(cr, limit=3000) + audit_rows("tm_default", limit=3000) + audit_rows(fr, limit=3000) + audit_rows(co, limit=3000)
acts = collections.Counter(r["action"] for r in rows)
for want in ("agent_session_started", "agent_step_confirmed", "agent_session_refused", "agent:batch_score", "upload_discarded",
             "sql_previewed", "sql_tables_listed", "sql_refused", "run_cancelled"):
    print(f"    {want!r}: {acts.get(want, 'MISSING')}")
for a in ("upload_discarded", "run_cancelled"):
    r = next((x for x in rows if x["action"] == a), None)
    if r:
        print(f"    {a}: team {r['team_id']} actor {r['actor']} principal {r.get('principal_id')} auth {r.get('auth')} "
              f"object {r.get('object_kind')}:{r.get('object_id')} details {json.dumps(r.get('details'))[:160]}")
print("  agent rows by auth:", collections.Counter((r["action"].split(":")[0], r.get("auth")) for r in rows if "agent" in r["action"] or r.get("auth") == "agent"))
print("  agent-auth rows without on_behalf_of:", sum(1 for r in rows if r.get("auth") == "agent" and not r.get("on_behalf_of")))

print("\n# 2. admin reset under a legal hold (ENSEMBLE_DEMO_MODE=1)")
hold = show("2a Ada POST /legal-holds {team credit-risk}", post("Ada", "/legal-holds", {"scope_kind": "team", "scope_id": cr, "case_ref": "CASE-RESET",
                                                                                     "reason": "regulator inquiry", "by": "Ada"}), ["id", "active"])
rq = show("2b Ada POST /admin/reset-requests", post("Ada", "/admin/reset-requests", {"reason": "demo environment refresh", "by": "Ada"}),
          ["id", "status"], cut=300)
show("2c Adam approves", post("Adam", f"/admin/reset-requests/{rq.get('id')}/decide", {"approve": True, "comment": "ok", "by": "Adam"}),
     ["status"], cut=200)
for s_ in get("Ada", "/agent/sessions").json().get("sessions") or []:  # the reset refuses while a conversation waits
    if s_["status"] not in ("idle", "stopped", "done", "failed"):
        post(s_["user"], f"/agent/sessions/{s_['id']}/stop", {})
show("2d Ada POST /admin/reset {request_id} (hold active)", post("Ada", "/admin/reset", {"confirm": "reset", "by": "Ada",
                                                                                         "request_id": rq.get("id"), "reason": "refresh"}), cut=700)
show("2e GET /runs/{run} (still there)", get("Ada", f"/runs/{st['run']}"), ["id", "status"], cut=150)
show("2f GET /datasets/{v1} (still there)", get("Ada", f"/datasets/{v1}"), ["id", "rows"], cut=150)
print("  admin_reset_refused rows:", [(r["actor"], (r.get("details") or {}).get("status"), str(r.get("details"))[:140])
                                      for r in audit_rows("tm_default", "admin_reset_refused")][-2:])
show("2g Adam releases the hold", post("Adam", f"/legal-holds/{hold['id']}/release", {"reason": "case closed", "by": "Adam"}), ["id", "released_at"], cut=200)
show("2h Ada POST /admin/reset {request_id} (no hold)", post("Ada", "/admin/reset", {"confirm": "reset", "by": "Ada", "request_id": rq.get("id"),
                                                                                     "reason": "refresh"}), ["four_eyes", "audit_kept"], cut=300)
show("2i GET /legal-holds?team=cr&include_released=1 (the hold row is kept, released)",
     get("Ada", "/legal-holds", params={"team": cr, "include_released": "true"}), None, cut=400)
for t in (cr, "tm_default"):
    print(f"  verify {t}:", json.dumps({k: v for k, v in get("Ada", f"/teams/{t}/audit/verify").json().items() if k in ("ok", "rows", "anchored", "first_break")}))
