"""P21: every decide path refuses the requester and their lookalike names (punctuation, accents, Cyrillic / Greek /
full-width confusables, zero-width), and `require_certified_inputs` at Production. Runs after chain.py on the same API.

    python foureyes.py <base> <state.json>"""
import json
import sys
import time

import httpx

B = (sys.argv[1] if len(sys.argv) > 1 else "http://localhost:8000") + "/api"
ST = json.load(open(sys.argv[2] if len(sys.argv) > 2 else "state.json"))
c = httpx.Client(timeout=600)
ANA = ["Ana", "A.n.a", "Anà", "Аna", "ANA", "A n a", "An​a", "Ａna"]
ADA = ["Ada", "A.d.a", "Аda", "Adá", "ADA"]


def h(who):
    return {"X-Ensemble-Actor": who} if who and who.isascii() else {}


def show(n, r, keys=None, cut=400):
    try:
        j = r.json()
    except ValueError:
        j = r.text[:200]
    hdr = f" | {r.headers['x-ensemble-authz']}" if "x-ensemble-authz" in r.headers else ""
    print(f"## {n} → HTTP {r.status_code}{hdr}")
    print("   ", json.dumps({k: j.get(k) for k in keys} if keys and isinstance(j, dict) else j, default=str, ensure_ascii=False)[:cut])
    return j


def spoofs(path, names, body_of):
    out = []
    for s in names:
        r = c.post(B + path, json=body_of(s), headers=h(s))
        d = r.json().get("detail")
        out.append(f"{s!r}→{r.status_code}")
        last = d
    print("    spoofs:", " ".join(out))
    print("    last detail:", str(last)[:200])


pd, v1, cr = ST["pd"], ST["v1"], ST["cr"]
po = {"override_reason": "P21 chain: the leak check was reviewed by hand"}  # the toy data trips the Doctor's leak question
print("# authz mode:", c.get(B + "/me", headers=h("Ada")).json().get("authz_mode"))
c.post(f"{B}/teams/{cr}/members", json={"name": "Rita", "role": "risk_reviewer", "by": "Ada"}, headers=h("Ada"))

# a model of Ana's: run on v1 in PD, registered by Ana
rid = show("1 Ana POST /runs {v1, PD}", c.post(B + "/runs", json={"dataset_id": v1, "project_id": pd, "label": "y", "time_limit": 20, "by": "Ana"},
                                                headers=h("Ana")), ["id"])["id"]
for _ in range(120):
    if c.get(f"{B}/runs/{rid}", headers=h("Ana")).json()["status"] in ("succeeded", "failed"):
        break
    time.sleep(3)
vid = show("2 Ana POST /runs/{r}/register", c.post(f"{B}/runs/{rid}/register", json={"created_by": "Ana", "model_name": "pd-loans"},
                                                     headers=h("Ana")), ["id", "version", "status"])["id"]
for _ in range(120):
    v = c.get(f"{B}/registry/versions/{vid}", headers=h("Ana")).json()
    if v.get("status") in ("ready", "failed"):
        break
    time.sleep(3)
print("    version", vid, v.get("status"), v.get("approval"))

print("## 3 registry review: registrant Ana and lookalikes")
spoofs(f"/registry/versions/{vid}/review", ANA, lambda s: {"decision": "approve", "reviewer": s})
show("3b Rita (risk reviewer) approves the review", c.post(f"{B}/registry/versions/{vid}/review", json={"decision": "approve", "reviewer": "Rita"},
                                                            headers=h("Rita")), ["version"], cut=120)

ch = show("4 Ana POST /projects/PD/champion", c.post(f"{B}/projects/{pd}/champion", json={"version_id": vid, "by": "Ana", **po}, headers=h("Ana")),
          ["id", "status"])["id"]
print("## 4a champion decide: requester Ana and lookalikes")
spoofs(f"/champions/{ch}/decide", ANA, lambda s: {"approve": True, "by": s, **po})
show("4b Ben rejects the champion", c.post(f"{B}/champions/{ch}/decide", json={"approve": False, "comment": "not now", "by": "Ben"},
                                            headers=h("Ben")), ["status"])

rq = show("5 Ana POST /registry/versions/{v}/retire", c.post(f"{B}/registry/versions/{vid}/retire", json={"by": "Ana", "reason": "P21 four eyes"},
                                                              headers=h("Ana")), ["id", "status"])["id"]
print("## 5a retire decide: requester Ana and lookalikes")
spoofs(f"/registry/version-requests/{rq}/decide", ANA, lambda s: {"approve": True, "by": s})
show("5b Ana withdraws", c.post(f"{B}/registry/version-requests/{rq}/decide", json={"approve": False, "by": "Ana"}, headers=h("Ana")),
     ["status"])

dq = show("6 Ana POST /datasets/v2/delete {only_version}", c.post(f"{B}/datasets/{ST['v2']}/delete", json={
    "by": "Ana", "reason": "P21 four eyes", "only_version": True, "irreproducible": True}, headers=h("Ana")), ["id", "status", "detail"])
if dq.get("id"):
    print("## 6a dataset delete decide: requester Ana and lookalikes")
    spoofs(f"/dataset-requests/{dq['id']}/decide", ANA, lambda s: {"approve": True, "by": s})
    show("6b Ana withdraws", c.post(f"{B}/dataset-requests/{dq['id']}/decide", json={"withdraw": True, "by": "Ana"}, headers=h("Ana")), ["status"])

xr = show("7 Ada POST /admin/reset-requests", c.post(f"{B}/admin/reset-requests", json={"by": "Ada", "reason": "P21 four-eyes check"},
                                                      headers=h("Ada")), ["id", "status", "detail"])
if xr.get("id"):
    print("## 7a reset decide: requester Ada and lookalikes")
    spoofs(f"/admin/reset-requests/{xr['id']}/decide", ADA, lambda s: {"approve": True, "by": s})

# require_certified_inputs at Production
show("8 Ben PATCH /teams/credit-risk {policy: require_certified_inputs approved_for_model}",
     c.patch(f"{B}/teams/{cr}", json={"policy": {"require_certified_inputs": "approved_for_model"}, "by": "Ben"}, headers=h("Ben")), ["policy"])
show("8a Ben → Production", c.post(f"{B}/registry/versions/{vid}/transition", json={"stage": "Production", "actor": "Ben", **po}, headers=h("Ben")),
     ["detail"], cut=600)
ac = show("8b Ana requests approved_for_model on v1", c.post(f"{B}/datasets/{v1}/certifications", json={
    "level": "approved_for_model", "reason": "PD model input", "by": "Ana"}, headers=h("Ana")), ["id", "status"])["id"]
show("8c /authz/check Ben data.certify_decide_model", c.post(B + "/authz/check", json={
    "action": "data.certify_decide_model", "object_kind": "certification", "object_id": ac, "principal": "Ben"}, headers=h("Ada")),
     ["allow", "needed", "role"])
show("8d Rita decides approved_for_model", c.post(f"{B}/certifications/{ac}/decide", json={"approve": True, "by": "Rita"}, headers=h("Rita")),
     ["status", "level", "decided_by"])
j = show("8e Ben → Production", c.post(f"{B}/registry/versions/{vid}/transition", json={"stage": "Production", "actor": "Ben", **po},
                                        headers=h("Ben")), ["audit"], cut=900)
v = c.get(f"{B}/registry/versions/{vid}", headers=h("Ben")).json()
print("    stage", v.get("stage"), "| result.input_certifications:",
      json.dumps((v.get("result") or {}).get("input_certifications"), default=str)[:400])
