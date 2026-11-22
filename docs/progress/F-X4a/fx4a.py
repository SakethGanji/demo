"""F-X4a: the checks X4's scripts do not make, after setup.py (and sweep.py / decide.py / teams.py if they ran first).

    python fx4a.py <base> <state.json>        (needs ../X4 on sys.path: x4lib)

C2  masking on the run / model-version pages that quote values (fairness, scorecard + csv, recourse, shift options,
    credit report, card), the unmask path (reason, decision, audit), and the fairness GET being pure (POST saves).
M3  batch preview / download masked; unmasked download = data.export_unmasked + reason, audited.
M1  the review's decider is the principal (reviewer ≠ principal → 409 in enforce).
M2  member changes: owners only for owners / maintainers / risk reviewers, no self-grants, DELETE alike, last owner.
M5  /registry/audit and /registry/summary without a filter: only the caller's own teams."""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "X4"))
from x4lib import B, audit_rows, c, get, h, leak, load, patch, post, show  # noqa: E402

st = load()
mode, cr, run, mv, bid = st["mode"], st["cr"], st["run"], st["mv"], st["batch"]
print(f"# mode {mode}")


def line(n, r, extra=""):
    hdr = f" | {r.headers['x-ensemble-authz']}" if "x-ensemble-authz" in r.headers else ""
    try:
        j = r.json()
        code = j.get("detail", {}).get("code") if isinstance(j, dict) and isinstance(j.get("detail"), dict) else None
    except ValueError:
        j, code = None, None
    print(f"## {n} → HTTP {r.status_code}{hdr}{' ' + code if code else ''} | pii {leak(r)}{' | ' + extra if extra else ''}")
    sys.stdout.flush()
    return j


def reads(team, since):
    return [(e["actor"], (e.get("details") or {}).get("read"), (e.get("details") or {}).get("reason"), (e.get("details") or {}).get("columns"))
            for e in audit_rows(team, "dataset_rows_read") if e["id"] > since]


def last_id(team):
    rows = audit_rows(team, limit=1)
    return rows[0]["id"] if rows else 0


print("\n# C2. pages that quote column values: Vic (viewer), masked by default")
for n, url, prm in (("fairness ?group=ssn", f"/runs/{run}/fairness", {"group": "ssn"}),
                    ("scorecard", f"/runs/{run}/scorecard", {}), ("scorecard.csv", f"/runs/{run}/scorecard.csv", {}),
                    ("recourse", f"/registry/versions/{mv}/recourse", {}), ("shift (options)", f"/runs/{run}/shift", {}),
                    ("credit-report", f"/registry/versions/{mv}/credit-report", {}),
                    ("credit-report md", f"/registry/versions/{mv}/credit-report", {"format": "md"}),
                    ("card", f"/registry/versions/{mv}/card", {}), ("card md", f"/registry/versions/{mv}/card", {"format": "md"})):
    r = get("Vic", url, params=prm, timeout=300)
    j = None
    try:
        j = r.json()
    except ValueError:
        pass
    extra = ""
    if isinstance(j, dict):
        extra = f"masked_columns {j.get('masked_columns')}"
        if "by_group" in j:
            extra += f" | groups {[g['group'] for g in j['by_group']][:4]}"
    elif "x-ensemble-masked-columns" in r.headers:
        extra = f"masked_columns {r.headers['x-ensemble-masked-columns']}"
    line(f"Vic GET {n}", r, extra)

print("\n# C2. the unmask path on a run page (fairness ?group=ssn)")
a0 = last_id(cr)
line("Ana unmasked=1, no reason", get("Ana", f"/runs/{run}/fairness", params={"group": "ssn", "unmasked": 1, "by": "Ana"}))
line("Ana (maintainer) unmasked=1&reason=QA-77", get("Ana", f"/runs/{run}/fairness", params={"group": "ssn", "unmasked": 1,
                                                                                             "reason": "QA-77", "by": "Ana"}))
line("Vic (viewer) unmasked=1&reason=curious", get("Vic", f"/runs/{run}/fairness", params={"group": "ssn", "unmasked": 1,
                                                                                           "reason": "curious", "by": "Vic"}))
line("Ana scorecard unmasked=1&reason=QA-77", get("Ana", f"/runs/{run}/scorecard", params={"unmasked": 1, "reason": "QA-77", "by": "Ana"}))
print("   dataset_rows_read rows since:", reads(cr, a0))

print("\n# C2 / m6. fairness: the GET is pure; POST saves (run.train), audited")


def card_reports():
    return sorted(((get("Ana", f"/registry/versions/{mv}/card").json().get("fairness") or {}).get("reports") or {}))


print("   card fairness reports before:", card_reports())
for who in ("Fay", "Vic", "Ana"):
    line(f"{who} GET /runs/{{run}}/fairness?group=segment", get(who, f"/runs/{run}/fairness", params={"group": "segment"}))
print("   card fairness reports after three GETs:", card_reports())
line("Vic (viewer) POST /runs/{run}/fairness {group: segment}", post("Vic", f"/runs/{run}/fairness", {"group": "segment"}))
line("Fay (outsider) POST /runs/{run}/fairness {group: segment}", post("Fay", f"/runs/{run}/fairness", {"group": "segment"}))
line("Ana POST /runs/{run}/fairness {group: ssn}", post("Ana", f"/runs/{run}/fairness", {"group": "ssn"}))
print("   card fairness reports after Ana's POST:", card_reports())
print("   audit:", [(e["actor"], e["action"], (e.get("details") or {}).get("group")) for e in audit_rows(cr, "fairness_report_saved")][:3])
j = line("Vic GET card after the save", get("Vic", f"/registry/versions/{mv}/card"))
print("   card fairness reports:", {k: [g.get("group") for g in (v.get("by_group") or [])][:4]
                                    for k, v in (((j or {}).get("fairness") or {}).get("reports") or {}).items()})

print("\n# M3. batches: preview and download")
for who in ("Vic", "Mo", "Ana"):
    j = line(f"{who} GET /registry/batches/{{bid}}", get(who, f"/registry/batches/{bid}"))
    if j:
        print("    preview[0]:", str(((j.get("result") or {}).get("preview") or [{}])[0])[:160], "| masked", j.get("masked_columns"))
    r = get(who, f"/registry/batches/{bid}/download")
    line(f"{who} GET …/download", r, f"masked {r.headers.get('x-ensemble-masked-columns')} | {r.text.splitlines()[1][:90] if r.status_code == 200 else ''}")
a0 = last_id(cr)
line("Ana download unmasked=1, no reason", get("Ana", f"/registry/batches/{bid}/download", params={"unmasked": 1, "by": "Ana"}))
line("Mo (member) download unmasked=1&reason=x-ray", get("Mo", f"/registry/batches/{bid}/download",
                                                         params={"unmasked": 1, "reason": "x-ray", "by": "Mo"}))
line("Vic (viewer) download unmasked=1&reason=x-ray", get("Vic", f"/registry/batches/{bid}/download",
                                                          params={"unmasked": 1, "reason": "x-ray", "by": "Vic"}))
line("Ana (maintainer) download unmasked=1&reason=TICKET-9", get("Ana", f"/registry/batches/{bid}/download",
                                                                 params={"unmasked": 1, "reason": "TICKET-9", "by": "Ana"}))
line("Ana preview unmasked=1&reason=TICKET-9", get("Ana", f"/registry/batches/{bid}", params={"unmasked": 1, "reason": "TICKET-9", "by": "Ana"}))
print("   dataset_rows_read rows since:", reads(cr, a0))
line("Fay GET /registry/batches/{bid}", get("Fay", f"/registry/batches/{bid}"))
line("Carl GET /registry/batches/{bid}/download", get("Carl", f"/registry/batches/{bid}/download"))

print("\n# M1. review: the decider is the principal")
reg = post("Ana", f"/runs/{run}/register", {"created_by": "Ana", "model_name": "pd-x4"}).json()
mv2 = reg.get("id")
for _ in range(200):
    if get("Ana", f"/registry/versions/{mv2}").json().get("status") in ("ready", "failed", "error"):
        break
    __import__("time").sleep(3)
line("Rita (risk reviewer) review reviewer='Bo'", post("Rita", f"/registry/versions/{mv2}/review",
                                                       {"decision": "approve", "reviewer": "Bo", "comment": "x"}))
line("Rita review reviewer='Rita', by='Bo'", post("Rita", f"/registry/versions/{mv2}/review",
                                                  {"decision": "approve", "reviewer": "Rita", "by": "Bo", "comment": "x"}))
j = line("Rita review with no reviewer field", post("Rita", f"/registry/versions/{mv2}/review", {"decision": "approve", "comment": "ok"}))
print("   approval:", ((j or {}).get("version") or {}).get("approval"), "| recorded actor:", ((j or {}).get("audit") or {}).get("actor"))
print("   audit rows:", [(e["action"], e["actor"], e.get("principal_id")) for e in audit_rows(cr) if e.get("object_id") == mv2
                         and e["action"] in ("approved", "review_refused", "authz_denied", "authz_would_deny")][:4])

print("\n# M2. team members (C §1.3)")


def members():
    return sorted((m["display_name"], m["role"]) for m in get("Ada", f"/teams/{cr}").json()["members"])


print("before:", members())
d = lambda who, p: c.delete(f"{B}/teams/{cr}/members/{p}", params={"by": who}, headers=h(who))  # noqa: E731
for n, r in (
    ("Ana (maintainer) PATCH Bo (owner) → viewer", patch("Ana", f"/teams/{cr}/members/Bo", {"role": "viewer", "by": "Ana"})),
    ("Ana DELETE Bo (owner)", d("Ana", "Bo")),
    ("Ana PATCH Mo (member) → risk_reviewer", patch("Ana", f"/teams/{cr}/members/Mo", {"role": "risk_reviewer", "by": "Ana"})),
    ("Ana PATCH Ana (self) → risk_reviewer", patch("Ana", f"/teams/{cr}/members/Ana", {"role": "risk_reviewer", "by": "Ana"})),
    ("Ana PATCH Ana (self) → owner", patch("Ana", f"/teams/{cr}/members/Ana", {"role": "owner", "by": "Ana"})),
    ("Ana DELETE Rita (risk reviewer)", d("Ana", "Rita")),
    ("Ana POST add Max as maintainer", post("Ana", f"/teams/{cr}/members", {"name": "Max", "role": "maintainer", "by": "Ana"})),
    ("Ana POST add Max as risk_reviewer", post("Ana", f"/teams/{cr}/members", {"name": "Max", "role": "risk_reviewer", "by": "Ana"})),
    ("Ana POST add Max as member (allowed)", post("Ana", f"/teams/{cr}/members", {"name": "Max", "role": "member", "by": "Ana"})),
    ("Ana PATCH Max member → viewer (allowed)", patch("Ana", f"/teams/{cr}/members/Max", {"role": "viewer", "by": "Ana"})),
    ("Ana DELETE Max (viewer; allowed)", d("Ana", "Max")),
    ("Ben (owner) PATCH Ben (self) → risk_reviewer", patch("Ben", f"/teams/{cr}/members/Ben", {"role": "risk_reviewer", "by": "Ben"})),
    ("Mo (member) POST add Mo2 as viewer", post("Mo", f"/teams/{cr}/members", {"name": "Mo2", "role": "viewer", "by": "Mo"})),
    ("Ben (owner) PATCH Mo → risk_reviewer (allowed)", patch("Ben", f"/teams/{cr}/members/Mo", {"role": "risk_reviewer", "by": "Ben"})),
    ("Ben (owner) PATCH Mo → member (allowed)", patch("Ben", f"/teams/{cr}/members/Mo", {"role": "member", "by": "Ben"})),
    ("Ben (owner) PATCH Bo (owner) → maintainer (allowed)", patch("Ben", f"/teams/{cr}/members/Bo", {"role": "maintainer", "by": "Ben"})),
    ("Bo (now maintainer) DELETE Ben (owner)", d("Bo", "Ben")),
    ("Ben (owner) DELETE Ada (owner; allowed)", d("Ben", "Ada")),
    ("Ben (owner) PATCH Bo → owner (allowed)", patch("Ben", f"/teams/{cr}/members/Bo", {"role": "owner", "by": "Ben"})),
    ("Bo (owner) DELETE Ben (owner; allowed)", d("Bo", "Ben")),
    ("Bo (owner) PATCH Bo (self) → maintainer", patch("Bo", f"/teams/{cr}/members/Bo", {"role": "maintainer", "by": "Bo"})),
    ("Ada (admin) DELETE Bo (the last owner)", d("Ada", "Bo")),
    ("Ada (admin) PATCH Bo (the last owner) → member", patch("Ada", f"/teams/{cr}/members/Bo", {"role": "member", "by": "Ada"})),
    ("Bo DELETE Bo (leaves; the last owner)", d("Bo", "Bo")),
    ("Bo (owner) POST add Ben back as owner (allowed)", post("Bo", f"/teams/{cr}/members", {"name": "Ben", "role": "owner", "by": "Bo"})),
):
    line(n, r)
print("after:", members())
line("/authz/check Ana model.review", post("Ana", "/authz/check", {"action": "model.review", "object_kind": "model_version",
                                                                  "object_id": mv}))
print("   allow:", post("Ana", "/authz/check", {"action": "model.review", "object_kind": "model_version", "object_id": mv}).json().get("allow"))

print("\n# C2. outsider (Fay) on newly declared object routes the sweep cannot fill")
up = c.post(B + "/uploads", params={"filename": "x.csv", "project_id": st["pd"]}, files={"file": ("x.csv", b"a,b\n1,2\n", "text/csv")},
            headers=h("Ana")).json().get("upload_id")
jobs = get("Ana", "/jobs", params={"limit": 50}).json().get("jobs") or []
jid = next((j["id"] for j in jobs if j.get("ref_kind") == "run" and j.get("ref_id") == run), jobs[0]["id"] if jobs else None)
for who in ("Fay", "Vic", "Ana"):
    line(f"{who} GET /uploads/{{Ana's staged upload}}", get(who, f"/uploads/{up}"))
    line(f"{who} GET /jobs/{{the run's job}}", get(who, f"/jobs/{jid}"))
print("   /jobs list sizes (Fay, Vic, Ana):", [len(get(w, "/jobs", params={"limit": 1000}).json().get("jobs") or []) for w in ("Fay", "Vic", "Ana")])
line("Fay GET /teams/{cr}", get("Fay", f"/teams/{cr}"))
line("Fay GET /teams/{cr}/members", get("Fay", f"/teams/{cr}/members"))
line("Vic GET /teams/{cr}/members", get("Vic", f"/teams/{cr}/members"))

print("\n# M5. /registry/audit and /registry/summary with no filter")
teams = {t["id"]: t["slug"] for t in get("Ada", "/teams").json()["teams"]}
for who in ("Fay", "Carl", "Vic", "Ada"):
    e = get(who, "/registry/audit", params={"limit": 2000}).json().get("entries") or []
    s = get(who, "/registry/summary").json().get("audit") or []
    by = {}
    for x in e:
        k = teams.get(x.get("team_id"), x.get("team_id"))
        by[k] = by.get(k, 0) + 1
    print(f"   {who}: /registry/audit {len(e)} rows by team {by} | summary audit teams {sorted({teams.get(x.get('team_id')) for x in s})}")
line("Fay /registry/audit?version_id=<cr mv>", get("Fay", "/registry/audit", params={"version_id": mv}))
