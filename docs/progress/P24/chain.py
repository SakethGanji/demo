"""P24 verification chain: manual API calls chained as the UI makes them (no smoke suite). Same script on SQLite and
on Postgres (ENSEMBLE_DB_URL), on the local store and on S3 (MinIO). Usage:

    python chain.py <base> <state.json>     (API on <base>, ENSEMBLE_ADMINS=Ada,Adam, ENSEMBLE_GC_LIVE_GRACE_DAYS=0, fresh data dir)

People: Ada, Adam (platform admins); Credit Risk: Ben, Bo (owners), Ana (maintainer); Collections: Cole (owner), Carl
(member). Data: `loans` (with `ssn`) + `bureau`, eight versions; bureau is re-uploaded at v7 and carried into v8."""
import json
import sys
import time

import httpx

B = (sys.argv[1] if len(sys.argv) > 1 else "http://localhost:8000") + "/api"
STATE = sys.argv[2] if len(sys.argv) > 2 else "state.json"
c = httpx.Client(timeout=900)
N = 400


def h(who):
    return {"X-Ensemble-Actor": who}


def show(n, r, keys=None, cut=700):
    try:
        j = r.json()
    except ValueError:
        j = r.text[:300]
    hdr = f" | {r.headers['x-ensemble-authz']}" if "x-ensemble-authz" in r.headers else ""
    shown = {k: j.get(k) for k in keys} if keys and isinstance(j, dict) else j
    print(f"## {n} → HTTP {r.status_code}{hdr}")
    print("   ", json.dumps(shown, default=str, ensure_ascii=False)[:cut])
    return j


def post(who, url, js=None, **kw):
    return c.post(B + url, json=js if js is not None else {"by": who}, headers=h(who), **kw)


def get(who, url, **kw):
    return c.get(B + url, headers=h(who), **kw)


def loans(k):
    rows = ["app_id,ssn,customer_id,income,age,segment,y"]
    for i in range(N + 10 * k):
        inc = 30000 + (i * 37 + k) % 50000
        rows.append(f"A{i:05d},{100 + i % 800:03d}-{10 + i % 89:02d}-{1000 + i:04d},C{i % 300:04d},{inc},{20 + i % 50},"
                    f"{['retail', 'sme', 'corporate'][i % 3]},{int(inc < 52000 and (20 + i % 50) < 50 or i % 7 == 0)}")
    return ("\n".join(rows) + "\n").encode()


def bureau(k):
    return ("customer_id,score,n_inquiries\n" + "".join(f"C{i:04d},{500 + (i * 13 + k) % 350},{(i + k) % 9}\n" for i in range(300))).encode()


def upload(who, files, q):
    return c.post(B + "/uploads", params=q, files=[("file", (n, d, "text/csv")) for n, d in files], headers=h(who))


def commit(who, files, q, reason):
    u = upload(who, files, q).json()["upload_id"]
    ch = post(who, f"/uploads/{u}/check").json()
    if ch.get("blocks"):
        print("    check blocks:", ch["blocks"])
    match = {"method": "key", "columns": ["app_id"]}
    if len(files) > 1:  # several tables: one match per table
        match = {"loans": match, "bureau": {"method": "key", "columns": ["customer_id"]}}
    r = post(who, f"/uploads/{u}/commit", {"reason": reason, "by": who, "match": match})
    if r.status_code != 201:
        print(f"    commit {reason} → HTTP {r.status_code} {r.text[:400]}")
    m = r.json()
    return {**m, "version_id": m.get("version_id") or m.get("id")}


def wait_run(rid):
    for _ in range(200):
        st = get("Ada", f"/runs/{rid}").json()
        if st["status"] in ("succeeded", "failed"):
            return st
        time.sleep(3)
    return st


mode = get("Ada", "/me").json().get("authz_mode")
print(f"# authz mode: {mode} | store: {get('Ada', '/admin/lake/status').json()['store']}")

# 0. teams, people, projects ------------------------------------------------------------------------------------------------
cr = post("Ada", "/teams", {"name": "Credit Risk", "by": "Ada"}).json()["id"]
for who, role in (("Ben", "owner"), ("Bo", "owner"), ("Ana", "maintainer")):
    post("Ada", f"/teams/{cr}/members", {"name": who, "role": role, "by": "Ada"})
co = post("Ada", "/teams", {"name": "Collections", "by": "Ada"}).json()["id"]
for who, role in (("Cole", "owner"), ("Carl", "member")):
    post("Ada", f"/teams/{co}/members", {"name": who, "role": role, "by": "Ada"})
pd = post("Ana", "/projects", {"name": "PD", "team_id": cr, "by": "Ana"}).json()["id"]
cp = post("Carl", "/projects", {"name": "Recoveries", "team_id": co, "by": "Carl"}).json()["id"]
print(f"## 0 teams {cr} {co} | projects PD {pd}, Recoveries {cp}")

# 1. eight versions: loans + bureau at v1, loans changes every version, bureau re-uploaded at v7 and carried into v8 ----------
V = {}
m = commit("Ana", [("loans.csv", loans(1)), ("bureau.csv", bureau(1))], {"project_id": pd}, "loans v1")
V[1] = m["version_id"]
root = V[1]
for k in range(2, 9):
    files = [("loans.csv", loans(k))] + ([("bureau.csv", bureau(7))] if k == 7 else [])
    m = commit("Ana", files, {"parent_version_id": V[k - 1]}, f"loans v{k}")
    V[k] = m["version_id"]
for k in (7, 8):
    j = get("Ana", f"/datasets/{root}/versions/{V[k]}").json()
    print(f"## 1 v{k} {V[k]}:", [(t["name"], t["change"], t["table_sha256"][:12]) for t in j["tables"]])
ST = {"cr": cr, "co": co, "pd": pd, "cp": cp, "V": V}

# 2. share to Collections (Ben asks, Bo approves); runs: Ana on v6 (an old run), Carl on v8 in Recoveries ------------------------
sid = post("Ben", "/shares", {"object_id": root, "grantee_team": co, "scope": "all", "reason": "recoveries models", "by": "Ben"}).json()["id"]
show("2a Bo POST /shares/{s}/decide", post("Bo", f"/shares/{sid}/decide", {"approve": True, "by": "Bo"}), ["id", "status", "scope", "pii"])
rb = {"label": "y", "table": "loans", "time_limit": 15, "presets": "medium_quality"}
r_ana = show("2b Ana POST /runs {v6, loans}", post("Ana", "/runs", {**rb, "dataset_id": V[6], "by": "Ana"}), ["id", "status"])["id"]
r_carl = show("2c Carl POST /runs {v8, loans, project Recoveries}", post("Carl", "/runs", {**rb, "dataset_id": V[8], "project_id": cp,
                                                                                         "by": "Carl"}), ["id", "status"])["id"]
jid = show("2d Ana POST /datasets/{root}/joins {loans ⋈ bureau}", post("Ana", f"/datasets/{root}/joins", {
    "name": "loans_bureau", "spec": {"grain": "loans", "joins": [{"type": "n:1", "table": "bureau", "on": ["customer_id"]}]}, "by": "Ana"}),
    ["id", "spec_sha256"])["id"]
f6 = show("2e Ana POST /datasets/{root}/versions/v6/frames", post("Ana", f"/datasets/{root}/versions/{V[6]}/frames", {"join_id": jid, "by": "Ana"}),
          ["id", "n_rows", "cache_blob"])
f8 = show("2f Ana POST /datasets/{root}/versions/v8/frames", post("Ana", f"/datasets/{root}/versions/{V[8]}/frames", {"join_id": jid, "by": "Ana"}),
          ["id", "n_rows", "cache_blob"])
r_join = show("2g Ana POST /runs {v8, join_id}", post("Ana", "/runs", {"label": "y", "time_limit": 15, "presets": "medium_quality",
                                                                     "dataset_id": V[8], "join_id": jid, "by": "Ana"}), ["id", "status"])["id"]
print("    ", r_ana, wait_run(r_ana)["status"], "|", r_carl, wait_run(r_carl)["status"], "|", r_join, wait_run(r_join)["status"])
ST.update(sid=sid, r_ana=r_ana, r_carl=r_carl, r_join=r_join, f6=f6["id"], f8=f8["id"])

# 3. legal hold on project PD --------------------------------------------------------------------------------------------------
dr = show("3a Ana POST /datasets/v2/delete (before the hold)", post("Ana", f"/datasets/{V[2]}/delete", {"reason": "superseded extract", "by": "Ana"}),
          ["id", "status"])
hold = show("3b Ben POST /legal-holds (not an admin)", post("Ben", "/legal-holds", {"scope_kind": "project", "scope_id": pd,
                                                                                    "case_ref": "CASE-2026-114", "reason": "litigation", "by": "Ben"}))
hold = show("3c Ada POST /legal-holds {project PD, CASE-2026-114}", post("Ada", "/legal-holds", {
    "scope_kind": "project", "scope_id": pd, "case_ref": "CASE-2026-114", "reason": "regulator inquiry on 2026 PD decisions", "by": "Ada"}),
    ["id", "scope_kind", "scope_id", "case_ref", "team_id", "active"])
hid = hold["id"]
per = json.dumps({"versions": {"keep_days": 0, "keep_min": 1}, "runs": {"evict_days": 0}, "sources": {"keep_days": 0}})
j = show("3d Ben GET /retention/plan?team=credit-risk&periods=…(0 days)", get("Ben", "/retention/plan", params={"team": cr, "periods": per}),
         ["n_candidates", "n_held", "counts"])
print("     held versions:", [(x["version_id"] == V[k] and f"v{k}" or x["version_id"], [h["case_ref"] for h in x["holds"]])
                              for x in j["categories"]["versions"]["held"] for k in [next(kk for kk in V if V[kk] == x["version_id"])]])
show("3e Ben POST /dataset-requests/{v2 request}/decide {approve}", post("Ben", f"/dataset-requests/{dr['id']}/decide", {"approve": True, "by": "Ben"}),
     None, cut=500)
show("3f Ana POST /datasets/v3/delete (under the hold)", post("Ana", f"/datasets/{V[3]}/delete", {"reason": "superseded extract", "by": "Ana"}),
     None, cut=300)
show("3g Ana DELETE /runs/{r_ana}", c.delete(B + f"/runs/{r_ana}", params={"by": "Ana", "reason": "cleanup"}, headers=h("Ana")), None, cut=300)
show("3h Ada POST /retention/run {run_ids:[r_ana], tier: evict}", post("Ada", "/retention/run", {"run_ids": [r_ana], "tier": "evict",
                                                                                              "confirm": "retention", "by": "Ada"}), None, cut=300)
show("3i Ana POST /datasets/v8/purge {ssn} (under the hold)", post("Ana", f"/datasets/{V[8]}/purge", {"table": "loans", "columns": ["ssn"],
                                                                                                   "reason": "customer erasure requests batch 7", "by": "Ana"}), None, cut=300)
show("3j Ada POST /legal-holds/{h}/release (the placer)", post("Ada", f"/legal-holds/{hid}/release", {"reason": "case closed", "by": "Ada"}), None, cut=300)
show("3k Adam POST /legal-holds/{h}/release", post("Adam", f"/legal-holds/{hid}/release", {"reason": "case closed by counsel", "by": "Adam"}),
     ["id", "released_by", "active"])
show("3l Ben POST /dataset-requests/{v2 request}/decide {approve} (hold released)", post("Ben", f"/dataset-requests/{dr['id']}/decide",
                                                                                        {"approve": True, "by": "Ben"}), ["id", "status", "deleted"], cut=300)

# 4. manual retention: no period → nothing; a team policy; dry run; run ------------------------------------------------------------
j = show("4a Ben GET /retention/plan?team=credit-risk (no policy)", get("Ben", "/retention/plan", params={"team": cr}),
         ["n_candidates", "no_period_set", "periods_from"])
show("4b Ben PATCH /teams/credit-risk {policy.retention}", c.patch(B + f"/teams/{cr}", json={"policy": {"retention": {
    "versions": {"keep_days": 0, "keep_min": 3, "keep_certified": True}, "join_caches": {"evict_unused_days": 0},
    "staging": {"days": 0}}}, "by": "Ben"}, headers=h("Ben")),
     ["id", "policy"])
j = show("4c Ben POST /retention/dry-run {team}", post("Ben", "/retention/dry-run", {"team": cr, "by": "Ben"}),
         ["plan_id", "n_candidates", "counts", "periods_from"])
pid = j["plan_id"]
print("     candidates:", [(x["id"], x["action"], x["reason"]) for v in j["categories"].values() for x in v["candidates"]][:12])
print("     kept:", [(x["id"], x["why"][:60]) for c_ in ("versions", "join_caches") for x in j["categories"][c_]["kept"]][:10])
print("     join caches: v6 frame candidate?", any(x.get("join_frame_id") == f6["id"] for x in j["categories"]["join_caches"]["candidates"]),
      "| v8 frame (a run uses it) kept?", any(x.get("join_frame_id") == f8["id"] for x in j["categories"]["join_caches"]["kept"]))
show("4d Ana POST /retention/run {plan_id} (not an owner)", post("Ana", "/retention/run", {"team": cr, "plan_id": pid, "confirm": "retention", "by": "Ana"}), None, cut=300)
j = show("4e Ben POST /retention/run {plan_id}", post("Ben", "/retention/run", {"team": cr, "plan_id": pid, "confirm": "retention", "by": "Ben"}),
         ["acted", "failed", "skipped", "by_action"])
reqs = [a.get("request_id") for a in j["actions"] if a.get("request_id")]
print("     delete requests:", [(a["version_id"], a.get("status")) for a in j["actions"] if a.get("action") == "request_delete"])
show("4f Ben POST /retention/run {plan_id} again", post("Ben", "/retention/run", {"team": cr, "plan_id": pid, "confirm": "retention", "by": "Ben"}), None, cut=200)
for rq in reqs:
    show(f"4g Bo POST /dataset-requests/{rq}/decide {{approve}}", post("Bo", f"/dataset-requests/{rq}/decide", {"approve": True, "by": "Bo"}),
         ["id", "dataset_id", "status"], cut=200)

# 5. GC: disarmed → verify-all arms it → delete v7 (bureau carried into v8) → dry run → run ----------------------------------------
j = show("5a Ada POST /admin/lake/gc?dry_run=1", post("Ada", "/admin/lake/gc?dry_run=1"), ["plan_id", "armed", "candidate_bytes"])
show("5b Ada POST /admin/lake/gc?dry_run=0 (disarmed)", post("Ada", "/admin/lake/gc?dry_run=0", {"plan_id": j["plan_id"], "confirm": "gc", "by": "Ada"}),
     None, cut=300)
j = show("5c Ada GET /admin/lake/verify-all", get("Ada", "/admin/lake/verify-all", params={"by": "Ada"}), ["ok", "checked", "legacy_json", "gc_armed"])
show("5d Ada GET /admin/lake/gc/armed", get("Ada", "/admin/lake/gc/armed"), ["armed", "armed_at"])
man7 = get("Ana", f"/datasets/{V[7]}/manifest").json()["manifest"]
parts7 = {t["name"]: [f["sha256"] for f in t["files"]] for t in man7["data"]["tables"]}
man8 = get("Ana", f"/datasets/{V[8]}/manifest").json()["manifest"]
parts8 = {t["name"]: [f["sha256"] for f in t["files"]] for t in man8["data"]["tables"]}
print("     v7 parts:", {k: [s[:12] for s in v] for k, v in parts7.items()}, "| v8 parts:", {k: [s[:12] for s in v] for k, v in parts8.items()})
dq = show("5e Ana POST /datasets/v7/delete", post("Ana", f"/datasets/{V[7]}/delete", {"reason": "v7 extract superseded by v8", "by": "Ana"}), ["id", "status"])
show("5f Ben POST /dataset-requests/{v7}/decide", post("Ben", f"/dataset-requests/{dq['id']}/decide", {"approve": True, "by": "Ben"}), ["status"], cut=200)
g = show("5g Ada POST /admin/lake/gc?dry_run=1", post("Ada", "/admin/lake/gc?dry_run=1"), ["plan_id", "armed", "candidate_bytes", "blobs"])
cand = {x["sha256"] for x in g["candidates"]}
print("     candidates:", sorted((x["sha256"][:12], x["kind"], x["bytes"]) for x in g["candidates"]))
print("     v7 bureau part listed?", any(s in cand for s in parts7.get("bureau", [])), "| v7 loans part listed?",
      all(s in cand for s in parts7.get("loans", [])), "| bureau shared with v8?", parts7.get("bureau") == parts8.get("bureau"))
r = show("5h Ada POST /admin/lake/gc?dry_run=0 {plan_id}", post("Ada", "/admin/lake/gc?dry_run=0", {"plan_id": g["plan_id"], "confirm": "gc", "by": "Ada"}),
         ["deleted", "bytes", "skipped", "failed", "aborted_multipart", "store"])
gone = {x["sha256"] for x in r["deleted_blobs"]}
print("     deleted == listed:", gone == cand, "| v8 still verifies:", get("Ana", f"/datasets/{V[8]}/verify").json()["ok"])
show("5i Ada POST /admin/lake/gc?dry_run=0 {same plan_id}", post("Ada", "/admin/lake/gc?dry_run=0", {"plan_id": g["plan_id"], "confirm": "gc", "by": "Ada"}),
     None, cut=200)

# 6. purge ssn: new version + the impact report → approvals by both owners → bytes purged → re-verify says purged -----------------
show("6a Ada GET /admin/lake/runs/{r_carl}/verify (before)", get("Ada", f"/admin/lake/runs/{r_carl}/verify"), ["ok", "data"], cut=300)
p = show("6b Ana POST /datasets/v8/purge {table loans, columns [ssn]}", post("Ana", f"/datasets/{V[8]}/purge", {
    "table": "loans", "columns": ["ssn"], "reason": "customer erasure requests batch 7 (GDPR art. 17)", "by": "Ana"}),
    ["id", "status", "new_version_id", "teams_needed", "waiting_for"])
pu = p["id"]
imp = p["impact"]
print("     impact: teams", imp["teams"], "| by_team", dict(sorted(imp["by_team"].items(), key=lambda kv: kv[0] != imp["owner_team"])), "| owner", imp["owner_team"])
print("     shares:", imp["shares"], "| join frames:", imp["join_frames"], "| models:", imp["models"], "| champions:", imp["champions"])
print("     versions:", [(x["id"], x["team"], x["state"]) for x in imp["versions"]], "| runs:", [(x["id"], x["team"], x["via"]) for x in imp["runs"]])
print("     blobs:", len(imp["blobs"]), "bytes", imp["bytes"], "| refs by owner kind:",
      {k: sum(1 for x in imp["refs"] if x["owner_kind"] == k) for k in sorted({x["owner_kind"] for x in imp["refs"]})}, "| holds", imp["holds"])
nv = get("Ana", f"/datasets/{root}/versions/{p['new_version_id']}").json()
print("     new version", nv.get("version_no"), [(t["name"], t["change"], [cc["name"] for cc in t.get("schema", [])][:7]) for t in nv["tables"]])
show("6c Ana POST /purges/{p}/decide {credit-risk} (requester)", post("Ana", f"/purges/{pu}/decide", {"team": cr, "approve": True, "by": "Ana"}), None, cut=250)
show("6d Carl POST /purges/{p}/decide {collections} (member)", post("Carl", f"/purges/{pu}/decide", {"team": co, "approve": True, "by": "Carl"}), None, cut=250)
show("6e Ben POST /purges/{p}/decide {credit-risk}", post("Ben", f"/purges/{pu}/decide", {"team": cr, "approve": True, "by": "Ben"}),
     ["status", "approvals", "waiting_for"], cut=400)
show("6f Ben POST /purges/{p}/decide {collections} (not their owner)", post("Ben", f"/purges/{pu}/decide", {"team": co, "approve": True, "by": "Ben"}),
     None, cut=250)
j = show("6g Cole POST /purges/{p}/decide {collections}", post("Cole", f"/purges/{pu}/decide", {"team": co, "approve": True, "by": "Cole"}),
         ["status", "approvals", "executed"], cut=900)
print("     blobs deleted:", len(j.get("blobs_deleted") or []), "failed:", j.get("blobs_failed"))
st = get("Ada", "/admin/lake/status").json()["blobs"]
print("     blob states:", dict(sorted(st.items())))
show("6h Ada GET /admin/lake/runs/{r_carl}/verify (after)", get("Ada", f"/admin/lake/runs/{r_carl}/verify"), ["ok", "status", "data", "split"], cut=600)
show("6i Ada GET /admin/lake/runs/{r_ana}/verify (old run on v6)", get("Ada", f"/admin/lake/runs/{r_ana}/verify"), ["ok", "status", "data", "split"], cut=600)
show("6i2 Ada GET /admin/lake/runs/{r_join}/verify (join run on v8)", get("Ada", f"/admin/lake/runs/{r_join}/verify"), ["ok", "status", "data"], cut=600)
show("6j Ana GET /datasets/{new}/verify", get("Ana", f"/datasets/{p['new_version_id']}/verify"), ["ok"])
show("6k Ana GET /datasets/v8 rows (deleted)", get("Ana", f"/datasets/{V[8]}/rows", params={"limit": 1}), None, cut=200)
g2 = show("6l Ada POST /admin/lake/gc?dry_run=1 (after the purge)", post("Ada", "/admin/lake/gc?dry_run=1"), ["plan_id", "candidate_bytes", "blobs"])
r2 = show("6m Ada POST /admin/lake/gc?dry_run=0", post("Ada", "/admin/lake/gc?dry_run=0", {"plan_id": g2["plan_id"], "confirm": "gc", "by": "Ada"}),
          ["deleted", "bytes", "skipped", "failed"])
print("     deleted == listed:", {x["sha256"] for x in r2["deleted_blobs"]} == {x["sha256"] for x in g2["candidates"]},
      "| new version still verifies:", get("Ana", f"/datasets/{p['new_version_id']}/verify").json()["ok"],
      "| Carl's run re-verify:", get("Ada", f"/admin/lake/runs/{r_carl}/verify").json().get("status"))
h1 = get("Ana", f"/datasets/{root}/history").json()
print("     history: default", h1.get("default_version_id") == p["new_version_id"] and "→ new version" or h1.get("default_version_id"),
      "| states", [(v.get("v"), v.get("state")) for v in h1.get("versions", [])])

# 7. audit: every action is in the team chains, and both chains verify ----------------------------------------------------------
for t in (cr, co):
    a = get("Ada", f"/teams/{t}/audit", params={"limit": 500}).json()
    rows = a.get("entries") or []
    acts = {}
    for x in rows:
        if any(x.get("action", "").startswith(p_) for p_ in ("legal_hold", "retention", "purge", "gc", "blob_", "dataset_delete")):
            acts[x["action"]] = acts.get(x["action"], 0) + 1
    print(f"## 7 {t} audit P24 actions:", dict(sorted(acts.items())))
    show(f"7 GET /teams/{t}/audit/verify", get("Ada", f"/teams/{t}/audit/verify"), None, cut=250)
lk = get("Ada", "/teams/tm_default/audit", params={"limit": 500}).json()
rows = lk.get("entries") or []
print("## 7 tm_default (lake) actions:", {a: sum(1 for x in rows if x.get("action") == a) for a in ("gc_dry_run", "gc_run_started", "gc_run",
      "gc_armed", "blob_deleted", "lake_gc_refused")})
show("7 GET /teams/tm_default/audit/verify", get("Ada", "/teams/tm_default/audit/verify"), None, cut=250)
json.dump(ST, open(STATE, "w"))
