"""P21 verification chain: manual API calls chained as the UI makes them. The same script runs in `audit` and in
`enforce` mode, on SQLite and on Postgres. Usage:

    python chain.py <base> <state.json>     (API on <base>, ENSEMBLE_ADMINS=Ada, a fresh data dir)

People: Ada (platform admin), Credit Risk: Ben + Bo (owners), Ana (maintainer); Collections: Cole (owner), Carl (member);
Fraud: Fay (member). Every response body is scanned for the raw values of the PII columns (`pii` after each call)."""
import json
import re
import sys
import time

import httpx

B = (sys.argv[1] if len(sys.argv) > 1 else "http://localhost:8000") + "/api"
STATE = sys.argv[2] if len(sys.argv) > 2 else "state.json"
c = httpx.Client(timeout=600)
ST: dict = {}

# -- the data: 600 loan applications with two direct identifiers --------------------------------------------------------
N = 600


def row(i):
    inc = 30000 + (i * 37) % 50000
    return {"app_id": f"A{i:05d}", "applicant_name": f"Person{i:04d} Example", "ssn": f"{100 + i % 800:03d}-{10 + i % 89:02d}-{1000 + i:04d}",
            "income": inc, "age": 20 + i % 50, "segment": ["retail", "sme", "corporate"][i % 3],
            "y": int(inc < 52000 and (20 + i % 50) < 50 or i % 7 == 0)}


def csv(rows, drop=()):
    cols = [k for k in rows[0] if k not in drop]
    return (",".join(cols) + "\n" + "".join(",".join(str(r[k]) for k in cols) + "\n" for r in rows)).encode()


R1 = [row(i) for i in range(N)]
R2 = [row(i) for i in range(N + 50)]  # v2: 50 new applications, same columns
RAW = {r["ssn"] for r in R2} | {r["applicant_name"] for r in R2}
_rx = re.compile("|".join(re.escape(v) for v in sorted(RAW, key=len, reverse=True)))


def h(who):
    """The typed name as the header; a name with non-ASCII letters cannot be a header value, so it goes only as the
    body / query `by` (which stands in for the header when it is missing)."""
    return {"X-Ensemble-Actor": who} if who and who.isascii() else {}


def show(n, r, keys=None, cut=600):
    try:
        j = r.json()
    except ValueError:
        j = r.text[:200]
    hdr = f" | {r.headers['x-ensemble-authz']}" if "x-ensemble-authz" in r.headers else ""
    leak = len(_rx.findall(r.text)) if r.headers.get("content-type", "").startswith("application/json") else "n/a"
    shown = {k: j.get(k) for k in keys} if keys and isinstance(j, dict) else j
    print(f"## {n} → HTTP {r.status_code}{hdr} | pii {leak}")
    print("   ", json.dumps(shown, default=str, ensure_ascii=False)[:cut])
    return j


def post(who, url, js=None, **kw):
    return c.post(B + url, json=js, headers=h(who), **kw)


def get(who, url, **kw):
    return c.get(B + url, headers=h(who), **kw)


def upload(who, data: bytes, fname, q):
    return c.post(B + "/uploads", params={"filename": fname, **q}, files={"file": (fname, data, "text/csv")}, headers=h(who))


mode = get("Ada", "/me").json().get("authz_mode")
print(f"# authz mode: {mode}")

# 0. teams and people -----------------------------------------------------------------------------------------------------
cr = show("0a Ada POST /teams {Credit Risk}", post("Ada", "/teams", {"name": "Credit Risk", "by": "Ada"}), ["id", "slug"])["id"]
for who, role in (("Ben", "owner"), ("Bo", "owner"), ("Ana", "maintainer")):
    post("Ada", f"/teams/{cr}/members", {"name": who, "role": role, "by": "Ada"})
co = show("0b Ada POST /teams {Collections}", post("Ada", "/teams", {"name": "Collections", "by": "Ada"}), ["id", "slug"])["id"]
for who, role in (("Cole", "owner"), ("Carl", "member")):
    post("Ada", f"/teams/{co}/members", {"name": who, "role": role, "by": "Ada"})
fr = show("0c Ada POST /teams {Fraud}", post("Ada", "/teams", {"name": "Fraud", "by": "Ada"}), ["id"])["id"]
post("Ada", f"/teams/{fr}/members", {"name": "Fay", "role": "member", "by": "Ada"})
print("   members:", {t: [(m["display_name"], m["role"]) for m in get("Ada", f"/teams/{t}").json()["members"]] for t in (cr, co, fr)})
pd = show("0d Ana POST /projects {PD, credit-risk}", post("Ana", "/projects", {"name": "PD", "team_id": cr, "by": "Ana"}), ["id", "team_id"])["id"]
cp = show("0e Carl POST /projects {Recoveries, collections}", post("Carl", "/projects", {"name": "Recoveries", "team_id": co, "by": "Carl"}),
          ["id", "team_id"])["id"]
fp = post("Fay", "/projects", {"name": "Fraud lab", "team_id": fr, "by": "Fay"}).json()["id"]

# 1. v1, PII review, contract frozen from v1 --------------------------------------------------------------------------------
u = upload("Ana", csv(R1), "loans.csv", {"project_id": pd}).json()["upload_id"]
show("1a Ana POST /uploads/{u}/check", post("Ana", f"/uploads/{u}/check"), ["blocks", "contract"])
v1 = show("1b Ana POST /uploads/{u}/commit", post("Ana", f"/uploads/{u}/commit", {"reason": "loans v1", "by": "Ana"}),
          ["version_id", "version_no"])["version_id"]
show("1c Ana PUT /datasets/v1/pii {confirm: suggested}", c.put(f"{B}/datasets/{v1}/pii", json={"confirm": "suggested", "by": "Ana"},
                                                                headers=h("Ana")), ["changed", "pending_review"])
j = show("1d Ana POST /datasets/v1/contract/from-version/v1", post("Ana", f"/datasets/{v1}/contract/from-version/{v1}", {"by": "Ana"}),
         ["id", "version", "active"])
print("    spec loans:", {k: v for k, v in j["spec"]["tables"][next(iter(j["spec"]["tables"]))].items() if k != "columns"},
      "| columns:", {k: v for k, v in list(j["spec"]["tables"][next(iter(j["spec"]["tables"]))]["columns"].items())[:4]})
print("    result on v1:", j["result"]["status"], j["result"]["n_rules"], "rules,", j["result"]["n_blocks"], "blocks")

# 2. v2 without 'income' (a removed column blocks) → /check blocks, /commit 409 ---------------------------------------------
u2 = upload("Ana", csv(R2, drop=("income",)), "loans_v2.csv", {"parent_version_id": v1}).json()["upload_id"]
j = show("2a Ana POST /uploads/{u2}/check (v2 without income)", post("Ana", f"/uploads/{u2}/check"), ["blocks"])
print("    contract:", j["contract"]["status"], "contract v", j["contract"]["contract_version"],
      [(r["rule"], r.get("column"), r["severity"]) for r in j["contract"]["rules"] if not r["ok"]])
show("2b Ana POST /uploads/{u2}/commit", post("Ana", f"/uploads/{u2}/commit", {"reason": "v2", "by": "Ana",
                                                                            "match": {"method": "key", "columns": ["app_id"]}}))
u3 = upload("Ana", csv(R2), "loans_v2.csv", {"parent_version_id": v1}).json()["upload_id"]
j = show("2c Ana POST /uploads/{u3}/check (v2 with every column, +50 rows)", post("Ana", f"/uploads/{u3}/check"), ["blocks"])
print("    contract:", j["contract"]["status"], [(t["name"], t.get("contract")) for t in j["tables"]])
v2 = show("2d Ana POST /uploads/{u3}/commit", post("Ana", f"/uploads/{u3}/commit", {"reason": "v2: 50 new applications", "by": "Ana",
                                                                                 "match": {"method": "key", "columns": ["app_id"]}}),
          ["version_id", "version_no", "is_default"])["version_id"]
print("    v2 contract_result:", (get("Ana", f"/datasets/{v2}/contract").json()["active"] or {}).get("version"),
      show("2e Ana POST /datasets/v2/contract/check", post("Ana", f"/datasets/{v2}/contract/check"), ["status", "n_rules", "n_blocks"])["status"])

# 3. certification: Ana requests, Ana (and lookalikes) cannot decide, Ben does ------------------------------------------------
cid = show("3a Ana POST /datasets/v1/certifications {certified}", post("Ana", f"/datasets/{v1}/certifications",
                                                                        {"level": "certified", "reason": "PD model input 2026", "by": "Ana"}),
           ["id", "status", "contract_id", "requested_by"])["id"]
show("3b Ana POST /certifications/{c}/decide {approve}", post("Ana", f"/certifications/{cid}/decide", {"approve": True, "by": "Ana"}))
for spoof in ("A.n.a", "Anà", "Аna", "ANA", "A n a", "An​a", "Ａna"):
    r = post(spoof, f"/certifications/{cid}/decide", {"approve": True, "by": spoof})
    print(f"   3c spoof {spoof!r:>10} → {r.status_code} {str(r.json().get('detail'))[:150]}")
show("3d Ben POST /certifications/{c}/decide {approve}", post("Ben", f"/certifications/{cid}/decide", {"approve": True, "comment": "ok", "by": "Ben"}),
     ["id", "status", "decided_by", "expires_at"])

# 4. share to Collections (scope certified, masked): requested by Ben, Ben cannot decide, Bo does -----------------------------
sid = show("4a Ben POST /shares {v1's dataset → collections, certified}", post("Ben", "/shares", {
    "object_id": v1, "grantee_team": "collections", "scope": "certified", "reason": "recoveries models", "by": "Ben"}),
    ["id", "status", "scope", "pii", "owner_team", "grantee_team"])["id"]
for spoof in ("Ben", "B.e.n", "Bén", "Веn", "BEN"):
    r = post(spoof, f"/shares/{sid}/decide", {"approve": True, "by": spoof})
    print(f"   4b {spoof!r:>10} decides → {r.status_code} {str(r.json().get('detail'))[:150]}")
show("4c Ada POST /authz/check {data.share_decide, share, principal Ana}", post("Ada", "/authz/check", {
    "action": "data.share_decide", "object_kind": "share", "object_id": sid, "principal": "Ana"}), ["allow", "needed", "role", "reason"])
show("4d Bo POST /shares/{s}/decide {approve}", post("Bo", f"/shares/{sid}/decide", {"approve": True, "by": "Bo"}),
     ["id", "status", "decided_by", "scope", "pii"])

# 5. Carl sees only v1; reads it masked; unmask is refused; trains into his own project; edits are refused --------------------
j = show("5a Carl GET /datasets?root_id=<root>&limit=50", get("Carl", "/datasets", params={"root_id": v1, "limit": 50}), ["total"])
print("    rows:", [(d["id"] == v1 and "v1" or d["id"] == v2 and "v2" or d["id"], d["team"]["slug"], d.get("access")) for d in j["datasets"]])
show("5b Carl GET /datasets/v1", get("Carl", f"/datasets/{v1}"), ["id", "rows"])
show("5c Carl GET /datasets/v2", get("Carl", f"/datasets/{v2}"), ["id", "rows", "detail"])
show("5d Carl GET /datasets/{root}/versions/v2", get("Carl", f"/datasets/{v1}/versions/{v2}"), ["version_id", "detail"])
j = show("5e Carl GET /datasets/v1/rows?limit=2", get("Carl", f"/datasets/{v1}/rows", params={"limit": 2, "by": "Carl"}),
         ["masked_columns", "unmasked"])
print("    first row:", (j.get("rows") or [None])[0])
show("5f Carl GET /datasets/v1/rows?unmasked=1&reason=QA", get("Carl", f"/datasets/{v1}/rows", params={"limit": 2, "unmasked": 1, "reason": "QA", "by": "Carl"}),
     ["masked_columns", "unmasked", "unmasked_refused", "detail"])
show("5g Carl GET /datasets/v2/rows", get("Carl", f"/datasets/{v2}/rows", params={"limit": 1, "by": "Carl"}), ["masked_columns", "detail"])
run_body = {"dataset_id": v1, "label": "y", "time_limit": 20, "presets": "medium_quality", "by": "Carl"}
show("5h Carl POST /runs {v1} (no project_id)", post("Carl", "/runs", run_body))
show("5i Carl POST /runs {v1, project_id: Ana's PD}", post("Carl", "/runs", {**run_body, "project_id": pd}), ["id", "detail"])
show("5j Carl POST /runs {v2, project_id: Recoveries}", post("Carl", "/runs", {**run_body, "dataset_id": v2, "project_id": cp}), ["id", "detail"])
rid = show("5k Carl POST /runs {v1, project_id: Recoveries}", post("Carl", "/runs", {**run_body, "project_id": cp}),
           ["id", "status", "dataset_id"])["id"]
for _ in range(120):
    st = get("Carl", f"/runs/{rid}").json()
    if st["status"] in ("succeeded", "failed"):
        break
    time.sleep(3)
rl = get("Carl", "/runs", params={"project_id": cp, "light": "true"}).json()
print(f"    run {rid}: {st['status']}; /runs?project_id=Recoveries →",
      [(r["id"], r.get("project_id"), (r.get("team") or {}).get("slug")) for r in rl.get("runs", [])])
show("5l Carl POST /datasets/v1/edit {preview}", post("Carl", f"/datasets/{v1}/edit", {
    "ops": [{"op": "drop_column", "column": "segment"}], "preview": True, "by": "Carl"}), ["preview", "detail"], cut=300)
for act in ("data.tag_pii", "data.version", "data.contract", "data.certify", "data.share", "data.read_meta", "data.preview", "data.export"):
    d = post("Ada", "/authz/check", {"action": act, "object_kind": "dataset", "object_id": v1, "principal": "Carl"}).json()
    d2 = post("Ada", "/authz/check", {"action": act, "object_kind": "dataset", "object_id": v2, "principal": "Carl"}).json()
    print(f"   5m /authz/check Carl {act:<16} v1 → {d['allow']!s:<5} v2 → {d2['allow']!s:<5} ({d['reason'][:90]})")
j = show("5n Ana GET /datasets/v1/usage (owner's \"Used by\")", get("Ana", f"/datasets/{v1}/usage"), None, cut=0)
print("    runs:", [r.get("id") or r.get("run_id") for r in (j.get("runs") or [])][:5])

# 6. an unmasked share (explicit grant) to Fraud; Fay's unmasked read is audited with its reason -----------------------------
s2 = show("6a Ben POST /shares {→ fraud, scope all, pii unmasked}", post("Ben", "/shares", {
    "object_id": v1, "grantee_team": "fraud", "scope": "all", "pii": "unmasked", "reason": "fraud rules on identities", "by": "Ben"}),
    ["id", "pii", "scope"])["id"]
show("6b Bo decides {approve} (no grant_unmasked)", post("Bo", f"/shares/{s2}/decide", {"approve": True, "by": "Bo"}))
show("6c Bo decides {approve, grant_unmasked: true}", post("Bo", f"/shares/{s2}/decide", {"approve": True, "grant_unmasked": True, "by": "Bo"}),
     ["status", "pii"])
show("6d Fay GET /datasets/v2/rows?unmasked=1 (no reason)", get("Fay", f"/datasets/{v2}/rows", params={"limit": 1, "unmasked": 1, "by": "Fay"}),
     ["detail"])
j = show("6e Fay GET /datasets/v2/rows?unmasked=1&reason=fraud case 77", get("Fay", f"/datasets/{v2}/rows",
                                                                            params={"limit": 1, "unmasked": 1, "reason": "fraud case 77", "by": "Fay"}),
         ["masked_columns", "unmasked"])
print("    first row:", (j.get("rows") or [None])[0])
j = get("Ben", f"/teams/{cr}/audit", params={"action": "dataset_rows_read"}).json()
def det(r):
    return json.loads(r["details"]) if isinstance(r.get("details"), str) else (r.get("details") or {})


print("    credit-risk audit dataset_rows_read:", [(r["actor"], det(r).get("reason"), det(r).get("columns")) for r in j["entries"]])

# 7. revoke: access stops at once; Carl's run stays and still verifies ---------------------------------------------------------
j = show("7a Ben POST /shares/{s}/revoke", post("Ben", f"/shares/{sid}/revoke", {"reason": "project closed", "by": "Ben"}),
         ["status", "revoked_by", "runs_kept"])
show("7b Carl GET /datasets/v1", get("Carl", f"/datasets/{v1}"), ["id", "detail"])
show("7c Carl GET /datasets/v1/rows", get("Carl", f"/datasets/{v1}/rows", params={"limit": 1, "by": "Carl"}), ["masked_columns", "detail"])
show("7d Carl POST /runs {v1, Recoveries}", post("Carl", "/runs", {**run_body, "project_id": cp}), ["id", "detail"])
show("7e Carl GET /runs/{rid}", get("Carl", f"/runs/{rid}"), ["id", "status", "project_id", "dataset_id"])
show("7f Ada GET /admin/lake/runs/{rid}/verify", get("Ada", f"/admin/lake/runs/{rid}/verify"), None, cut=500)
j = show("7g Carl GET /datasets?root_id=<root>", get("Carl", "/datasets", params={"root_id": v1, "limit": 50}), ["total"])
print("    rows:", [(d["id"] == v1 and "v1" or d["id"] == v2 and "v2" or d["id"], d.get("access")) for d in j["datasets"]])

# 8. audit and chains ------------------------------------------------------------------------------------------------------
for t in (cr, co):
    j = get("Ada", f"/teams/{t}/audit", params={"limit": 200}).json()
    acts = [r["action"] for r in j["entries"]]
    print(f"   8 {t} audit:", {a: acts.count(a) for a in sorted(set(acts)) if a.startswith(("share", "certif", "contract", "authz", "dataset_rows"))})
    print("     verify:", get("Ada", f"/teams/{t}/audit/verify").json())
print("   coverage:", {k: v for k, v in get("Ada", "/authz/coverage").json().items() if k in ("mode", "must", "covered", "uncovered")})
ST.update(cr=cr, co=co, pd=pd, cp=cp, v1=v1, v2=v2, cert=cid, share=sid, share2=s2, run=rid)
json.dump(ST, open(STATE, "w"))
