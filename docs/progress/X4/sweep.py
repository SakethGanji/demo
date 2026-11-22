"""X4: every GET route of /authz/coverage?all=true, its path parameters filled with set-up ids (Credit Risk's objects), called
as Fay (Fraud member: no role, no share), Carl (Collections member: a masked share of loans, scope certified) and Vic (Credit
Risk viewer). Prints status, the would-deny header, and `pii` = raw PII values of the loans table in the body.

    python sweep.py <base> <state.json>"""
import re

from x4lib import c, get, h, leak, load, post, wait  # noqa: F401

st = load()
cov = get("Ada", "/authz/coverage", params={"all": "true"}).json()
# an agent conversation of Ana's (read-only intent) so /agent/sessions/{sid} has something to show
ag = post("Ana", "/agent/sessions", {"message": "train a model on loans-x4", "user": "Ana", "decider": "stub"}).json()
st["agent_sid"] = ag.get("id")
ups = get("Ana", "/uploads/" + "x").status_code  # noqa: F841
FILL = {"dataset_id": st["v1"], "a": st["v1"], "b": st["v1"], "root_id": st["root"], "version_id": st["v1"], "run_id": st["run"],
        "vid": st["mv"], "bid": st["batch"], "team": st["cr"], "project_id": st["pd"], "cid": st["cert"], "table": "loans",
        "name": "pd-x4", "kind": "dataset", "node_id": st["v1"], "principal": "Ana", "sid": st["share_co"]}
SKIP = ("/stream", "/api/demo")
rows = []
for r in cov["routes"]:
    if r["method"] != "GET" or any(s in r["path"] for s in SKIP):
        continue
    p = r["path"]
    if p.startswith("/api/agent/sessions/"):
        fill = {**FILL, "sid": st["agent_sid"]}
    elif p.startswith("/api/anomaly/runs/"):
        fill = {**FILL, "run_id": st["anomaly"]}
    else:
        fill = FILL
    names = re.findall(r"{([^}:]+)(?::[^}]*)?}", p)
    if any(n not in fill for n in names):
        rows.append((p, r.get("action") or r.get("public") or "-", "unfilled", "", "", ""))
        continue
    url = re.sub(r"{([^}:]+)(?::[^}]*)?}", lambda m: str(fill[m.group(1)]), p).removeprefix("/api")
    out = []
    for who in ("Fay", "Carl", "Vic"):
        params = {"by": who}
        if "/shap" in p:
            params["row"] = 0
        if p.endswith("/fairness"):
            params["group"] = "ssn"
        try:
            x = get(who, url, params=params, timeout=120)
            wd = "W" if "x-ensemble-authz" in x.headers else ""
            out.append(f"{x.status_code}{wd}/{leak(x)}")
        except Exception as e:  # noqa: BLE001
            out.append(f"ERR {type(e).__name__}")
    rows.append((p, r.get("action") or ("public:" + r["public"] if r.get("public") else "-"), *out))
print(f"# mode {st['mode']} | GET routes {len(rows)} | cells: status[W=would-deny]/pii as Fay (no relation) · Carl (masked share) · Vic (viewer)")
for row in rows:
    print(" | ".join(str(x) for x in row))
fay200 = [r for r in rows if r[2].startswith("200")]
print(f"\n# Fay (no role, no share) got 200 on {len(fay200)} of {len(rows)} GET routes for Credit Risk objects:")
for r in fay200:
    print("   ", r[0], "|", r[1], "|", r[2])
print("\n# any raw PII in a body (any caller):")
for r in rows:
    if any(re.search(r"/[1-9]\d*$", str(x)) for x in r[2:]):
        print("   ", r)
