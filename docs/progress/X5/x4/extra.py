"""X5 (lane x4): adversarial extras not in X4's or the fix lanes' scripts. Run right after X4 setup.py.
M1: decider names on the other fields (query actor, lookalikes); C1: the agent's `user` variants; M5: /registry/audit with
another team's model name; C2: share-grantee and outsider on run pages with unmask.

    PYTHONPATH=…/F-X4c python extra.py <base> <state.json>"""
import httpx

from x4lib import B, get, h, load, post, show

st = load()
mv, cr, run = st["mv"], st["cr"], st["run"]
print(f"# mode {st['mode']}")


def line(n, r):
    hdr = f" | {r.headers['x-ensemble-authz']}" if "x-ensemble-authz" in r.headers else ""
    print(f"## {n} → HTTP {r.status_code}{hdr} | {r.text[:220]}")


print("\n# M1. decider names outside the body `by`")
line("1a Ana DELETE /registry/versions/{mv}/credit-report/tape?actor=Bo",
     httpx.delete(B + f"/registry/versions/{mv}/credit-report/tape", params={"actor": "Bo"}, headers=h("Ana"), timeout=60))
line("1b Ana DELETE …/credit-report/tape?by=Bo", httpx.delete(B + f"/registry/versions/{mv}/credit-report/tape", params={"by": "Bo"},
                                                             headers=h("Ana"), timeout=60))
for nm in ("bo", "Bo ", "Bо", "Bo​", "BO"):
    line(f"1c Rita review reviewer={nm!r}", post("Rita", f"/registry/versions/{mv}/review", {"decision": "approve", "reviewer": nm, "comment": "x"}))
line("1d Rita review created_by='Bo' (another name field)", post("Rita", f"/registry/versions/{mv}/review",
                                                               {"decision": "approve", "created_by": "Bo", "comment": "x"}))

print("\n# C1. the agent's user field")
for nm in ("fay", " Fay ", "Fаy", "Ana", "Vela agent on behalf of Ana", None):
    body = {"message": "list my datasets", "decider": "stub"}
    if nm is not None:
        body["user"] = nm
    r = post("Fay", "/agent/sessions", body)
    j = r.json() if r.headers.get("content-type", "").startswith("application/json") else {}
    print(f"## 2 Fay POST /agent/sessions user={nm!r} → HTTP {r.status_code} | user {j.get('user')} owner {j.get('owner_id')} | "
          f"{str(j.get('detail'))[:150]}")
line("2b X-Ensemble-Actor 'Vela agent' (the service principal's name) POST /agent/sessions",
     post("Vela agent", "/agent/sessions", {"message": "list datasets", "decider": "stub"}))
line("2c forged internal header (garbage signature)", httpx.get(B + "/me", headers={"X-Ensemble-Internal": "41.00", **h("Fay")}, timeout=30))

print("\n# M5. /registry/audit with another team's model / version")
line("3a Fay /registry/audit?model=pd-x4", get("Fay", "/registry/audit", params={"model": "pd-x4"}))
line("3b Fay /registry/audit?version_id=<cr mv>", get("Fay", "/registry/audit", params={"version_id": mv}))
line("3c Carl /registry/audit?model=pd-x4", get("Carl", "/registry/audit", params={"model": "pd-x4"}))

print("\n# C2. a masked-share grantee (Carl) and an outsider (Fay) on run pages, with the unmask path")
for who in ("Carl", "Fay"):
    for n, url, prm in (("fairness ssn unmasked", f"/runs/{run}/fairness", {"group": "ssn", "unmasked": 1, "reason": "probe-1"}),
                        ("scorecard unmasked", f"/runs/{run}/scorecard", {"unmasked": 1, "reason": "probe-1"}),
                        ("batch preview unmasked", f"/registry/batches/{st['batch']}", {"unmasked": 1, "reason": "probe-1"}),
                        ("anomaly top unmasked", f"/anomaly/runs/{st['anomaly']}/top", {"unmasked": 1, "reason": "probe-1", "by": who})):
        r = get(who, url, params=prm)
        from x4lib import leak
        hdr = f" | {r.headers['x-ensemble-authz']}" if "x-ensemble-authz" in r.headers else ""
        print(f"## 4 {who} {n} → HTTP {r.status_code}{hdr} | pii {leak(r)}")
show("4b Carl GET /datasets/{v1}/rows?unmasked=1&reason (masked share)", get("Carl", f"/datasets/{st['v1']}/rows",
     params={"by": "Carl", "unmasked": 1, "reason": "probe-1", "limit": 5}), ["masked_columns", "unmasked"], cut=200)
