"""X4: purge breadth. Release the hold, warm the caches that hold feature values (SHAP row, fairness on ssn, explain), purge
loans.ssn with every involved owner's approval, then look for any of the 600 raw SSNs: through the API (as Ana, an owner,
unmasked with a reason where a route offers it) and on disk (every file under the data dir, as decimal text, int64 and
float64 bytes; and the database).

    X4_DATA=<data dir> X4_DB=<sqlite file | postgresql://…> python purge.py <base> <state.json>"""
import json
import os
import re
import struct  # noqa: F401

import numpy as np

from x4lib import R, get, load, post, show

st = load()
v1, cr = st["v1"], st["cr"]
DATA, DB = os.environ["X4_DATA"], os.environ["X4_DB"]
SSN = [r["ssn"] for r in R]
SSN_SET = {str(s).encode() for s in SSN} | {f"{s:,}".encode() for s in SSN}
_I = np.array(SSN, dtype="<i8")
_F = np.array(SSN, dtype="<f8")


def text_hits(b):
    return {m for m in re.findall(rb"(?<!\d)(?:\d{9}|\d{3},\d{3},\d{3})(?!\d)", b) if m in SSN_SET}


def bin_hits(b):
    out = set()
    for off in range(8):
        n = (len(b) - off) // 8
        if n <= 0:
            continue
        a = np.frombuffer(b, dtype="<i8", count=n, offset=off)
        out |= set(a[np.isin(a, _I)].tolist())
        f = np.frombuffer(b, dtype="<f8", count=n, offset=off)
        out |= {int(x) for x in f[np.isin(f, _F)].tolist()}
    return out


def n_api(r):
    try:
        return len(text_hits(r.text.encode()))
    except Exception:  # noqa: BLE001
        return len(text_hits(r.content))


def scan_disk(tag):
    hits = {}
    for root, _, files in os.walk(DATA):
        for f in files:
            p = os.path.join(root, f)
            try:
                b = open(p, "rb").read()
            except OSError:
                continue
            t = len(text_hits(b))
            bn = len(bin_hits(b))
            if t or bn:
                hits[os.path.relpath(p, DATA)] = (t, bn)
    groups = {}
    for p, (t, bn) in hits.items():
        k = "/".join(p.split("/")[:2]) if not p.startswith(("runs/", "registry/")) else "/".join(p.split("/")[:1] + [re.sub(r"[0-9a-f]{6,}", "*", "/".join(p.split("/")[2:]))])
        g = groups.setdefault(k, [0, 0, 0])
        g[0] += 1
        g[1] = max(g[1], t)
        g[2] = max(g[2], bn)
    print(f"    disk ({tag}): {len(hits)} files hold raw SSNs; by place (files, max distinct as text, max distinct as int64/float64):")
    for k, g in sorted(groups.items()):
        print(f"      {k}: {g}")
    return hits


def scan_db(tag):
    if DB.startswith("postgres"):
        import psycopg
        con = psycopg.connect(DB)
        tabs = [r[0] for r in con.execute("SELECT tablename FROM pg_tables WHERE schemaname = 'public'")]
        q = lambda t: con.execute(f'SELECT * FROM "{t}"').fetchall()  # noqa: E731
    else:
        import sqlite3
        con = sqlite3.connect(DB)
        tabs = [r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type = 'table'")]
        q = lambda t: con.execute(f'SELECT * FROM "{t}"').fetchall()  # noqa: E731
    out = {}
    for t in tabs:
        n = 0
        for row in q(t):
            n += len(text_hits(json.dumps(row, default=str).encode()))
        if n:
            out[t] = n
    print(f"    database ({tag}): tables with raw SSNs (distinct values per table): {out}")


API = [("rows v1 (unmasked)", lambda: get("Ana", f"/datasets/{v1}/rows", params={"by": "Ana", "unmasked": 1, "reason": "purge check", "limit": 600})),
       ("export v1 (unmasked)", lambda: get("Ana", f"/datasets/{v1}/export", params={"by": "Ana", "unmasked": 1, "reason": "purge check"})),
       ("stats v1", lambda: get("Ana", f"/datasets/{v1}/stats", params={"by": "Ana"})),
       ("explain run", lambda: get("Ana", f"/runs/{st['run']}/explain")),
       ("shap row 1", lambda: get("Ana", f"/runs/{st['run']}/shap", params={"row": st.get("shap_row", 0)})),
       ("fairness ssn", lambda: get("Ana", f"/runs/{st['run']}/fairness", params={"group": "ssn"})),
       ("scorecard", lambda: get("Ana", f"/runs/{st['run']}/scorecard")),
       ("model card", lambda: get("Ana", f"/registry/versions/{st['mv']}/card")),
       ("recourse", lambda: get("Ana", f"/registry/versions/{st['mv']}/recourse")),
       ("batch", lambda: get("Ana", f"/registry/batches/{st['batch']}")),
       ("batch download", lambda: get("Ana", f"/registry/batches/{st['batch']}/download")),
       ("anomaly top (unmasked)", lambda: get("Ana", f"/anomaly/runs/{st['anomaly']}/top", params={"by": "Ana", "unmasked": 1, "reason": "purge check", "limit": 50})),
       ("anomaly scores.csv", lambda: get("Ana", f"/anomaly/runs/{st['anomaly']}/scores.csv", params={"by": "Ana"})),
       ("intake v1", lambda: get("Ana", f"/datasets/{v1}/intake", params={"by": "Ana", "unmasked": 1, "reason": "purge check"})),
       ("data doctor", lambda: get("Ana", f"/datasets/{v1}/data-doctor")),
       ("audit (credit-risk)", lambda: get("Ada", f"/teams/{cr}/audit", params={"limit": 2000}))]


def api(tag):
    print(f"    API ({tag}): distinct raw SSNs per call:")
    for n, f in API:
        try:
            r = f()
            print(f"      {n}: HTTP {r.status_code}, {n_api(r)}")
        except Exception as e:  # noqa: BLE001
            print(f"      {n}: {type(e).__name__}")


print(f"# mode {st['mode']}")
if st.get("hold"):
    show("0 Adam releases the hold", post("Adam", f"/legal-holds/{st['hold']}/release", {"reason": "case closed by counsel", "by": "Adam"}),
         ["active"], cut=100)
hp = get("Ana", f"/runs/{st['run']}/holdout-predictions", params={"by": "Ana"}).json()
st["shap_row"] = (hp.get("rows") or [0])[0]
get("Ana", f"/runs/{st['run']}/shap", params={"row": st["shap_row"]})
print("\n# 1. before the purge")
api("before")
scan_disk("before")
scan_db("before")

print("\n# 2. purge loans.ssn")
p = show("2a Ana POST /datasets/v1/purge {loans, [ssn]}", post("Ana", f"/datasets/{v1}/purge", {"table": "loans", "columns": ["ssn"],
         "reason": "customer erasure requests (GDPR art. 17), batch 9", "by": "Ana"}), ["id", "status", "new_version_id", "teams_needed"])
pu = p.get("id")
imp = p.get("impact") or {}
print("    impact teams:", imp.get("teams"), "| runs:", [(x["id"], x["team"], x.get("via")) for x in imp.get("runs") or []],
      "| models:", imp.get("models"), "| shares:", [x.get("id") for x in imp.get("shares") or []], "| join frames:", imp.get("join_frames"),
      "| blobs:", len(imp.get("blobs") or []))
owners = {st["cr"]: "Ben", st["co"]: "Cole", st["fr"]: "Fox"}
j = p
for t in p.get("teams_needed") or []:
    j = show(f"2b {owners.get(t, '?')} approves for {t}", post(owners.get(t, "Ada"), f"/purges/{pu}/decide", {"team": t, "approve": True,
                                                                                                          "by": owners.get(t, "Ada")}),
             ["status", "waiting_for"], cut=200)
print("    executed:", j.get("status"), "| blobs deleted:", len(j.get("blobs_deleted") or []), "| failed:", j.get("blobs_failed"))
st["new_v"] = p.get("new_version_id")

print("\n# 3. after the purge")
api("after")
after = scan_disk("after")
for f in sorted(after)[:40]:
    print("      ", f, after[f])
scan_db("after")
show("3a GET /admin/lake/runs/{run}/verify", get("Ada", f"/admin/lake/runs/{st['run']}/verify"), ["ok", "status"], cut=200)
