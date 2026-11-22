"""X4: the Vela agent's principal. The agent's tool calls carry a signed internal header naming the session's `user`; that
`user` comes from the POST /agent/sessions body. Run in enforce mode (stub decider).

    python agent.py <base> <state.json>"""
import time

from x4lib import audit_rows, get, leak, load, post, show

st = load()
v1, mv, cr = st["v1"], st["mv"], st["cr"]


def follow(sid, who, limit=180):
    t0 = time.time()
    s = {}
    while time.time() - t0 < limit:
        s = get(who, f"/agent/sessions/{sid}").json()
        if s.get("status") in ("done", "failed", "stopped", "waiting", "answered", "error", "idle") or \
                any(i.get("status") == "awaiting" for i in s.get("timeline", []) if i.get("type") == "step"):
            if s.get("status") != "thinking":
                return s
        time.sleep(2)
    return s


def steps(s):
    return [(i.get("tool"), i.get("status"), str(i.get("error") or "")[:120]) for i in s.get("timeline", []) if i.get("type") == "step"]


print(f"# mode {st['mode']}")
print("\n# 1. Fay (Fraud member, no role in Credit Risk, no share) reads Credit Risk rows directly")
show("1a Fay GET /datasets/v1/rows", get("Fay", f"/datasets/{v1}/rows", params={"by": "Fay", "limit": 3}), cut=200)
show("1b Fay POST /registry/versions/{mv}/predict", post("Fay", f"/registry/versions/{mv}/predict", {"rows": [{"income": 1}], "by": "Fay"}), cut=200)

print("\n# 2. Fay starts an agent conversation with user=Ben (an owner of Credit Risk)")
j = show("2a Fay POST /agent/sessions {user: Ben, 'why was it declined? mv'}",
         post("Fay", "/agent/sessions", {"message": f"why was the applicant declined, and what would change the reason? {mv}",
                                         "user": "Ben", "decider": "stub"}), ["id", "user", "status"])
sid = j["id"]
s = follow(sid, "Fay")
print("    steps:", steps(s))
fr = next((i for i in s.get("timeline", []) if i.get("tool") == "find_rows"), {})
r = get("Fay", f"/agent/sessions/{sid}")
print(f"    find_rows result (seen by Fay through GET /agent/sessions/{{sid}}): rows {len((fr.get('result') or {}).get('rows') or [])},"
      f" columns {list(((fr.get('result') or {}).get('rows') or [{}])[0].keys())[:9]}, masked {(fr.get('result') or {}).get('masked_columns')}, "
      f"session body pii {leak(r)}")
print("    answer:", next((i.get("text") for i in s.get("timeline", []) if i.get("type") == "answer"), None))
rd = [x for x in audit_rows(cr, "dataset_read") if "on behalf of Ben" in str(x.get("actor")) or x.get("actor") == "Ben"]
print("    credit-risk dataset_read rows naming Ben:", [(x["actor"], x.get("auth"), (x.get("details") or {}).get("read")) for x in rd][-3:])

print("\n# 3. a write: Fay asks the agent (as Ben) to score a batch with Ben's model, then confirms the step herself")
j = show("3a Fay POST /agent/sessions {user: Ben, 'score credit-default with mv'}",
         post("Fay", "/agent/sessions", {"message": f"score credit-default with {mv} and check drift", "user": "Ben", "decider": "stub"}),
         ["id", "user"])
sid2 = j["id"]
s = follow(sid2, "Fay")
pend = [i for i in s.get("timeline", []) if i.get("type") == "step" and i.get("status") == "awaiting"]
print("    steps:", steps(s))
if pend:
    show("3b Fay POST /agent/sessions/{sid}/confirm {approve}", post("Fay", f"/agent/sessions/{sid2}/confirm",
                                                                     {"step_id": pend[0]["id"], "approve": True}), ["status"], cut=150)
    s = follow(sid2, "Fay", 300)
    print("    steps:", steps(s))
    print("    answer:", next((i.get("text") for i in s.get("timeline", []) if i.get("type") == "answer"), None))
b = get("Ada", f"/registry/versions/{mv}/batches").json().get("batches") or []
print("    batches on Ben's team's model:", [(x["id"], x["created_by"], x["status"]) for x in b])
print("    Fay's direct POST /registry/versions/{mv}/batches →",
      post("Fay", f"/registry/versions/{mv}/batches", None).status_code)

print("\n# 4. Ana's own conversation: Fay reads it and confirms Ana's pending write")
j = show("4a Ana POST /agent/sessions {user: Ana, 'score credit-default with mv'}",
         post("Ana", "/agent/sessions", {"message": f"score credit-default with {mv}", "user": "Ana", "decider": "stub"}), ["id"])
sid3 = j["id"]
s = follow(sid3, "Ana")
pend = [i for i in s.get("timeline", []) if i.get("type") == "step" and i.get("status") == "awaiting"]
show("4b Fay GET /agent/sessions (every user's conversations)", get("Fay", "/agent/sessions"), cut=0)
print("    users listed to Fay:", sorted({x["user"] for x in get("Fay", "/agent/sessions").json()["sessions"]}))
if pend:
    show("4c Fay POST /agent/sessions/{Ana's sid}/confirm", post("Fay", f"/agent/sessions/{sid3}/confirm",
                                                               {"step_id": pend[0]["id"], "approve": True}), ["status"], cut=150)
    s = follow(sid3, "Ana", 300)
    print("    steps:", steps(s))
show("4d Fay POST /agent/sessions/{Ana's sid}/stop", post("Fay", f"/agent/sessions/{sid3}/stop", {}), ["status"], cut=100)
