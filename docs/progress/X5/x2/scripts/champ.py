"""X5/x2: retry gen.py's champion step once the registered model is ready. usage: champ.py <state> <port> <ds key> <mv key> <ch key>"""
import json, sys, time
from pathlib import Path
import httpx
st_p = Path(sys.argv[1]); st = json.loads(st_p.read_text()); port = sys.argv[2]
c = httpx.Client(base_url=f"http://localhost:{port}/api", timeout=300, headers={"X-Ensemble-Actor": "Ana"})
ds, mv = st[sys.argv[3]], st[sys.argv[4]]
for _ in range(60):
    v = c.get(f"/registry/versions/{mv}").json()
    s = v.get("status") or (v.get("version") or {}).get("status")
    if s != "building": break
    time.sleep(5)
print("model version status", s)
c.post(f"/registry/versions/{mv}/review", json={"decision": "approve", "reviewer": "Ben", "comment": "ok"}, headers={"X-Ensemble-Actor": "Ben"})
pid = "prj_" + st["root_of"][ds].split("_", 1)[1]
r = c.post(f"/projects/{pid}/champion", json={"version_id": mv, "by": "Ana", "comment": "x2", "override_reason": "x2 test data"})
print("champion", r.status_code, r.text[:300]); j = r.json()
rq = j.get("id") or (j.get("request") or {}).get("id")
if r.status_code < 300 and rq:
    r = c.post(f"/champions/{rq}/decide", json={"approve": True, "by": "Ben", "comment": "ok", "override_reason": "x2 test data"}, headers={"X-Ensemble-Actor": "Ben"})
    print("decide", r.status_code, r.text[:300])
st[sys.argv[5]] = {"project": pid, "status": r.json().get("status") if r.status_code < 300 else r.status_code}; st_p.write_text(json.dumps(st, indent=1))
