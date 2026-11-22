"""X4: decider-name spoofs on the decide paths. In enforce mode a body name ≠ the principal must be refused (409 "you are
signed in as …"); four eyes must hold for the recorded name AND the principal.

    python decide.py <base> <state.json>"""
from x4lib import audit_rows, get, load, post, show

st = load()
mv, cr = st["mv"], st["cr"]
print(f"# mode {st['mode']}")
print("\n# 1. registry review: the reviewer's name is body.reviewer (BY_FIELDS = by, actor, created_by, requested_by)")
show("1a Ana (registrant, maintainer) reviews her own version, reviewer=Rita", post("Ana", f"/registry/versions/{mv}/review",
     {"decision": "approve", "reviewer": "Rita", "comment": "x"}), cut=250)
show("1b Rita reviews, reviewer='Ana' (the registrant)", post("Rita", f"/registry/versions/{mv}/review",
     {"decision": "approve", "reviewer": "Ana", "comment": "x"}), cut=250)
show("1c Rita reviews, by='Bo' (a by field ≠ principal)", post("Rita", f"/registry/versions/{mv}/review",
     {"decision": "approve", "reviewer": "Rita", "by": "Bo", "comment": "x"}), cut=250)
j = show("1d Rita reviews, reviewer='Bo' (a real other person; no by field)", post("Rita", f"/registry/versions/{mv}/review",
         {"decision": "approve", "reviewer": "Bo", "comment": "looks fine"}), cut=0)
v = (j or {}).get("version") or {}
print("    approval:", v.get("approval"), "| audit entry actor:", ((j or {}).get("audit") or {}).get("actor"))
rows = [r for r in audit_rows(cr) if r.get("action") in ("approved", "version_approved") or "approv" in str(r.get("action"))]
print("    credit-risk audit rows:", [(r["action"], r["actor"], r.get("principal_id"), r.get("auth")) for r in rows][-3:])
reg = get("Ada", "/registry/audit", params={"version_id": mv}).json()
print("    /registry/audit:", [(e.get("action"), e.get("actor")) for e in (reg.get("entries") or reg.get("audit") or [])][-3:])
