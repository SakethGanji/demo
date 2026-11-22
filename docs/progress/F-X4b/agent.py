"""F-X4b: X4 C1 (agent privilege) after the fix, plus the legitimate path (the agent as yourself).

X4's repro first (Fay names Ben; Fay reads / confirms / stops Ana's conversation), then Ana's own conversation run to the
end, then the audit rows: principal_id = the agent's service principal, on_behalf_of = the caller, auth agent; the
session start / confirm / refusals carry the real caller.

    python agent.py <base> <state.json>"""
import json
import time

from x4lib import audit_rows, get, load, post, show

st = load()
v1, mv, cr = st["v1"], st["mv"], st["cr"]


def follow(sid, who, limit=240):
    t0 = time.time()
    s = {}
    while time.time() - t0 < limit:
        s = get(who, f"/agent/sessions/{sid}").json()
        if s.get("status") != "thinking" and (s.get("status") in ("done", "failed", "stopped", "answered", "error", "idle") or
                                               any(i.get("status") == "awaiting" for i in s.get("timeline", []) if i.get("type") == "step")):
            return s
        time.sleep(2)
    return s


def steps(s):
    return [(i.get("tool"), i.get("status"), str(i.get("error") or "")[:160]) for i in s.get("timeline", []) if i.get("type") == "step"]


def pending(s):
    return [i for i in s.get("timeline", []) if i.get("type") == "step" and i.get("status") == "awaiting"]


print(f"# mode {st['mode']}")
print("\n# 1. Fay (Fraud member, no role in Credit Risk) reads Credit Risk directly")
show("1a Fay GET /datasets/v1/rows", get("Fay", f"/datasets/{v1}/rows", params={"by": "Fay", "limit": 3}), cut=160)

print("\n# 2. X4 C1 repro: Fay starts a conversation naming Ben")
show("2a Fay POST /agent/sessions {user: Ben}", post("Fay", "/agent/sessions", {"message": f"score credit-default with {mv}", "user": "Ben",
                                                                               "decider": "stub"}), cut=300)
show("2b anonymous POST /agent/sessions {user: Ben}", post(None, "/agent/sessions", {"message": "hi", "user": "Ben", "decider": "stub"}), cut=300)

print("\n# 3. Fay's own conversation asks for a batch on Ben's team's model: the agent has Fay's rights only")
j = show("3a Fay POST /agent/sessions (no user)", post("Fay", "/agent/sessions", {"message": f"score credit-default with {mv} and check drift",
                                                                                   "decider": "stub"}), ["id", "user", "owner_id", "status"])
sidf = j["id"]
s = follow(sidf, "Fay")
print("    steps:", steps(s))
if pending(s):
    show("3b Fay confirms her own pending step", post("Fay", f"/agent/sessions/{sidf}/confirm", {"step_id": pending(s)[0]["id"], "approve": True}),
         ["status"], cut=120)
    s = follow(sidf, "Fay", 300)
    print("    steps:", steps(s))
b = get("Ada", f"/registry/versions/{mv}/batches").json().get("batches") or []
print("    batches on Credit Risk's model:", [(x["id"], x["created_by"], x["status"]) for x in b])

print("\n# 4. Ana (Credit Risk maintainer) runs the agent as herself (the legitimate path)")
j = show("4a Ana POST /agent/sessions {user: Ana}", post("Ana", "/agent/sessions", {"message": f"score credit-default with {mv}", "user": "Ana",
                                                                                    "decider": "stub"}), ["id", "user", "owner_id", "status"])
sida = j["id"]
s = follow(sida, "Ana")
print("    steps:", steps(s))
pa = pending(s)
print("\n# 5. Fay against Ana's conversation (X4: list, read, confirm, stop all worked)")
show("5a Fay GET /agent/sessions", get("Fay", "/agent/sessions"), cut=0)
print("    owners listed to Fay:", sorted({x["user"] for x in get("Fay", "/agent/sessions").json()["sessions"]}))
show("5b Fay GET /agent/sessions/{Ana's sid}", get("Fay", f"/agent/sessions/{sida}"), cut=250)
if pa:
    show("5c Fay POST …/{Ana's sid}/confirm", post("Fay", f"/agent/sessions/{sida}/confirm", {"step_id": pa[0]["id"], "approve": True}), cut=250)
show("5d Fay POST …/{Ana's sid}/messages", post("Fay", f"/agent/sessions/{sida}/messages", {"message": "also delete things"}), cut=250)
show("5e Fay POST …/{Ana's sid}/stop", post("Fay", f"/agent/sessions/{sida}/stop", {}), cut=250)
print("\n# 6. Ada (admin) reads Ana's conversation, cannot act in it")
show("6a Ada GET /agent/sessions", get("Ada", "/agent/sessions"), cut=0)
print("    owners listed to Ada:", sorted({x["user"] for x in get("Ada", "/agent/sessions").json()["sessions"]}))
show("6b Ada GET …/{Ana's sid}", get("Ada", f"/agent/sessions/{sida}"), ["id", "user", "status"], cut=200)
if pa:
    show("6c Ada POST …/{Ana's sid}/confirm", post("Ada", f"/agent/sessions/{sida}/confirm", {"step_id": pa[0]["id"], "approve": True}), cut=250)
print("\n# 7. Ana confirms her own step")
if pa:
    show("7a Ana POST …/confirm", post("Ana", f"/agent/sessions/{sida}/confirm", {"step_id": pa[0]["id"], "approve": True}), ["status"], cut=120)
    s = follow(sida, "Ana", 300)
    print("    steps:", steps(s))
    print("    answer:", (next((i.get("text") for i in s.get("timeline", []) if i.get("type") == "answer"), None) or "")[:300])
b = get("Ada", f"/registry/versions/{mv}/batches").json().get("batches") or []
print("    batches on Credit Risk's model:", [(x["id"], x["created_by"], x["status"]) for x in b])

print("\n# 8. the audit rows")
prs = {p["display_name"]: p.get("principal_id") for p in get("Ada", f"/teams/{cr}").json()["members"]}
fay_id = next((r.get("principal_id") for r in audit_rows("tm_default", "agent_session_started") if r["actor"] == "Fay"), None)
rows = audit_rows(cr, limit=2000) + audit_rows("tm_default", limit=2000)
for r in rows:
    if r["action"].startswith("agent") or r.get("auth") == "agent":
        d = r.get("details") or {}
        print(f"  {r['team_id']:<14} {r['action']:<24} actor={r['actor']!r:<36} principal={r.get('principal_id')} auth={r.get('auth')} "
              f"on_behalf_of={r.get('on_behalf_of')} session={d.get('session')}")
print("  Ana's principal id:", prs.get("Ana"), "| Fay's:", fay_id)
for t in (cr, "tm_default"):
    print(f"  verify {t}:", json.dumps({k: v for k, v in get("Ada", f"/teams/{t}/audit/verify").json().items() if k in ("ok", "rows", "anchored", "first_break")}))
